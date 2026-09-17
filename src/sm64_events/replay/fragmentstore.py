"""Published fragments in shared, lease-protected extent files.

One encoder run owns this archive. Complete units are indexed once and appended
once. A read selects byte ranges, never builds a per-attempt media file. The
existing ring owns disk accounting, free-space pressure and deletion. Native
replay views reference these bytes; explicit saves publish ordinary MP4 files.
"""
from contextlib import contextmanager, ExitStack
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import threading
from uuid import uuid4

from sm64_events.core.profiling import measured
from sm64_events.replay.fragmentindex import Sample, fragment_samples, tracks_of
from sm64_events.replay.fragments import FragmentReader
from sm64_events.replay.fragmentretention import IdleTail


@dataclass(frozen=True, slots=True)
class Published:
    path: Path
    group: str
    offset: int
    size: int
    stream_offset: int
    samples: tuple[Sample, ...]
    start: int
    end: int


@dataclass
class _Extent:
    path: Path
    group: str
    start: int
    units: list[Published] = field(default_factory=list)
    keys: list[tuple[int, int]] = field(default_factory=list)
    size: int = 0
    samples: int = 0


class _MissingStart(LookupError):
    """The chosen video GOP does not include a crossing audio packet."""


class FragmentArchive:
    def __init__(self, directory, ring, run, *, extent_bytes=8 * 1024**2,
                 extent_samples=8192, index_samples=131072, extent_ticks=720000, discard=None,
                 idle_window=None):
        if min(extent_bytes, extent_samples, index_samples) <= 0:
            raise ValueError("fragment archive limits must be positive")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ring, self.run = ring, run
        self._extent_bytes, self._extent_samples = extent_bytes, extent_samples
        self._index_limit = index_samples
        self._extent_ticks, self._discard = extent_ticks, discard
        self._idle_tail = IdleTail(run, ring, idle_window)
        self._parser, self._init, self.tracks = FragmentReader(), None, {}
        self._extents, self._count, self._sequence = [], 0, 0
        self._prune_revision = -1
        self._current, self._file, self._writer_lease = None, None, None
        self._last, self._published_last = {}, {}
        self._lock = threading.RLock()
        self.closed, self.error = False, None

    def _utc(self, ticks):
        return datetime.fromtimestamp(self.run.origin_ts + ticks / 90000, timezone.utc)

    def feed(self, data):
        with self._lock:
            if self.closed:
                raise ValueError("fragment archive is closed")
            try:
                for unit in self._parser.feed(data):
                    if unit.kind == "init":
                        self.tracks = tracks_of(unit)
                        if any(t.kind == "video" and t.timescale != 90000 for t in self.tracks.values()):
                            raise ValueError("video clock must be 90 kHz")
                        self._init = unit.data
                    elif unit.kind == "media":
                        self._publish(unit)
            except Exception as failure:
                self.finish(str(failure))
                raise

    @measured("replay.fragment_publish")
    def _publish(self, unit):
        samples = fragment_samples(unit, self.tracks, max_samples=self._index_limit)
        for sample in samples:
            if sample.dts < self._last.get(sample.track_id, sample.dts):
                raise ValueError("overlapping fragment clock")
            self._last[sample.track_id] = sample.dts + sample.duration
        video = [s for s in samples if self.tracks[s.track_id].kind == "video"]
        rotate = self._current is None or (video and video[0].key and
                 (self._current.size >= self._extent_bytes or self._current.samples >= self._extent_samples
                  or video[0].pts - self._current.start >= self._extent_ticks))
        if rotate:
            self._close_extent()
            if not video or not video[0].key:
                raise ValueError("extent must begin at a random-access picture")
            self._open_extent(video[0].pts)
        if self._current.samples + len(samples) > self._index_limit:
            raise ValueError("encoder did not publish a bounded random-access interval")
        start = video[0].pts if video else self._end
        end = video[-1].pts + video[-1].duration if video else self._end
        if any(sample.key for sample in video[1:]):
            raise ValueError("keyframe must begin a media fragment")
        offset = self._current.size
        self._file.write(unit.data)
        self._file.flush()  # published bytes are readable while capture continues
        item = Published(self._current.path, self._current.group, offset, len(unit.data), unit.offset, samples, start, end)
        if video and video[0].key:
            self._current.keys.append((start, len(self._current.units)))
        self._current.units.append(item)
        self._current.size += len(unit.data)
        self._current.samples += len(samples)
        self._count += len(samples)
        self._published_last.update({s.track_id: s.dts + s.duration for s in samples})
        if video:
            self._end = video[-1].pts + video[-1].duration
        self._trim_index()
        self._idle_tail.maintain(self._frontier())

    def _open_extent(self, tick):
        self._sequence += 1
        # The run identifier is opaque; it never becomes a filesystem path.
        path = self.directory / f"fragments_{uuid4().hex}.bin"
        group = f"fragments:{self.run.id}:{self._sequence}"
        lease = self.ring.pin_temp(group)
        lease.__enter__()
        try:
            self.ring.register_temp(group, [path], self._utc(tick), self._utc(tick))
            stream = path.open("xb")
        except BaseException:
            lease.__exit__(None, None, None)
            raise
        self._current = _Extent(path, group, tick)
        self._extents.append(self._current)
        self._writer_lease, self._file = lease, stream
        self._start, self._end = tick, tick

    def _close_extent(self):
        if self._file is None:
            return
        stream = self._file
        self._file = None
        try:
            stream.close()
            self.ring.register_temp(self._current.group, [self._current.path],
                                    self._utc(self._start), self._utc(self._end))
            if not self._current.units or (self._discard is not None and self._discard(self._utc(self._start))):
                self.ring.forget_temp(self._current.group, delete=True)
            self._idle_tail.maintain(self._frontier(), closed=self._current)
        finally:
            self._writer_lease.__exit__(None, None, None)
            self._writer_lease = None
            self._current = None
        self._prune_missing()

    def _prune_missing(self):
        revision = self.ring.temporary_revision
        if revision == self._prune_revision:
            return
        retained = []
        groups = self.ring.temporary_groups()
        for extent in self._extents:
            if extent is self._current or extent.group in groups:
                retained.append(extent)
            else:
                self._count -= extent.samples
        self._extents = retained
        self._idle_tail.prune(groups)
        self._prune_revision = revision

    def _frontier(self):
        if not self.tracks or len(self._published_last) != len(self.tracks):
            return None
        return min(tick * 90000 // self.tracks[key].timescale
                   for key, tick in self._published_last.items())

    def coverage(self):
        """Available run bounds on the slowest published track clock."""
        with self._lock:
            self._prune_missing()
            end = self._frontier()
            if not self._extents or end is None:
                return None
            start = self._playable_start(self._extents[0])
            return (start, end) if end > start else None

    def _playable_start(self, extent):
        # A retained video keyframe may precede the first retained AAC packet.
        # Start at the first actual picture with both tracks available.
        first = {}
        for unit in extent.units:
            for sample in unit.samples:
                first.setdefault(sample.track_id, sample.dts)
            if len(first) == len(self.tracks):
                break
        if len(first) != len(self.tracks):
            return extent.units[-1].end if extent.units else extent.start
        earliest = max((tick * 90000 + self.tracks[key].timescale - 1) // self.tracks[key].timescale
                       for key, tick in first.items())
        for unit in extent.units:
            for sample in unit.samples:
                if self.tracks[sample.track_id].kind == "video" and sample.pts >= earliest:
                    return sample.pts
        return extent.units[-1].end

    def window(self, start, end):
        """Clamp a new selection at eviction/idle holes without joining time."""
        with self._lock:
            self._prune_missing()
            index = max(0, bisect_right(self._extents, start, key=lambda extent: extent.start) - 1)
            while index < len(self._extents) and self._extents[index].units[-1].end <= start:
                index += 1
            if index == len(self._extents):
                raise LookupError("replay footage is no longer retained")
            first = self._extents[index]
            start = max(start, first.start)
            if index == 0 or self._extents[index-1].units[-1].end < first.start:
                start = max(start, self._playable_start(first))
            last = first
            for following_index in range(index+1, len(self._extents)):
                following = self._extents[following_index]
                if last.units[-1].end >= end or following.start > last.units[-1].end:
                    break
                last = following
            bounds = {}
            for unit in reversed(last.units):
                for sample in reversed(unit.samples):
                    bounds.setdefault(sample.track_id, sample.dts + sample.duration)
                if len(bounds) == len(self.tracks):
                    break
            end = min(end, *(tick * 90000 // self.tracks[key].timescale for key, tick in bounds.items()))
            if end <= start:
                raise ValueError("no complete replay interval is published")
            return start, end

    def discard(self):
        """Retire a closed run under the session-wide metadata budget."""
        with self._lock:
            if not self.closed:
                return
            for extent in self._extents:
                self.ring.forget_temp(extent.group, delete=True)
            self._extents.clear()
            self._count = 0

    def expire_before(self, cutoff):
        """Drop whole old extents, retaining a predecessor for GOP/AAC overlap."""
        with self._lock:
            self._prune_missing()
            boundary = self.run.ticks_at(cutoff.timestamp())
            if self.closed and self._extents and self._extents[-1].units[-1].end < boundary:
                for extent in tuple(self._extents):
                    self.ring.forget_temp(extent.group, delete=True)
                self._prune_missing()
                return
            expired = []
            for index, extent in enumerate(self._extents[:-2]):
                following = self._extents[index + 1]
                if not following.units or following.units[-1].end > boundary:
                    break
                if extent is not self._current:
                    expired.append(extent)
            for extent in expired:
                self.ring.forget_temp(extent.group, delete=True)
            self._prune_missing()

    @property
    def sample_count(self):
        with self._lock:
            return self._count

    def _trim_index(self):
        while self._count > self._index_limit and len(self._extents) > 1:
            extent = self._extents.pop(0)
            self._count -= extent.samples
            # Existing readers retain their immutable ranges and leases. New
            # queries honestly lose this history, including under RAM pressure.
            self.ring.forget_temp(extent.group, delete=True)

    def finish(self, error=None):
        with self._lock:
            if self.closed:
                return
            try:
                if error is None:
                    self._parser.finish()
            except ValueError as failure:
                error = str(failure)
            finally:
                self.closed, self.error = True, error
                try:
                    self._close_extent()
                except OSError as failure:
                    self.error = str(failure)

    @contextmanager
    def selection(self, start_tick, end_tick):
        """Lease immutable sample/byte metadata for a finite published interval."""
        with ExitStack() as leases:
            with self._lock:
                units, visible = self._select(start_tick, end_tick)
                for group in dict.fromkeys(unit.group for unit in units):
                    leases.enter_context(self.ring.pin_temp(group))
                # Pin before checking/opening; eviction cannot win between this
                # point and the final byte read. A prior eviction stays missing.
                for path in dict.fromkeys(unit.path for unit in units):
                    if not path.is_file():
                        raise LookupError("replay fragment was evicted")
            yield self._init, self.tracks, units, visible

    @contextmanager
    def read(self, start_tick, end_tick):
        """Stream the original fragment bytes inside their selection lease."""
        with self.selection(start_tick, end_tick) as (_, _, units, visible):
            active = [True]
            chunks = self._read_bytes(units, active)
            try:
                yield visible, chunks
            finally:
                active[0] = False
                chunks.close()  # cancellation closes a partially read Windows file before unpinning

    @measured("replay.fragment_select")
    def _select(self, start, end):
        if not self._init or start < 0 or end <= start:
            raise LookupError("invalid fragment interval")
        index = bisect_right(self._extents, start, key=lambda extent: extent.start) - 1
        if index < 0:
            raise LookupError("replay dependency GOP was evicted")
        keys = self._extents[index].keys
        key = bisect_right(keys, (start, float("inf"))) - 1
        if key < 0:
            raise LookupError("replay dependency GOP is unavailable")
        # Binary-search the dependency, then visit only the selected interval.
        # Never expand all samples from an entire practice session on View.
        try:
            return self._collect(index, keys[key][1], start, end)
        except _MissingStart:
            # A video keyframe does not align AAC's sample grid. The previous
            # GOP may own the audio packet crossing this start; keep its bytes,
            # with the same visible bounds. Missing older media still fails.
            if key > 0:
                previous_unit = keys[key - 1][1]
            elif index > 0:
                index -= 1
                previous_unit = self._extents[index].keys[-1][1]
            else:
                raise
            return self._collect(index, previous_unit, start, end)

    def _collect(self, extent_index, unit_index, start, end):
        selected, visible, bounds = [], [], {}
        for index in range(extent_index, len(self._extents)):
            extent = self._extents[index]
            for index in range(unit_index, len(extent.units)):
                unit = extent.units[index]
                selected.append(unit)
                for sample in unit.samples:
                    track = self.tracks[sample.track_id]
                    previous = bounds.get(track.id)
                    if previous is None and sample.dts * 90000 > start * track.timescale:
                        raise _MissingStart("replay track start is unavailable")
                    if previous and previous[1] != sample.dts:
                        raise LookupError("replay fragment interval contains a hole")
                    bounds[track.id] = (previous[0] if previous else sample.dts, sample.dts + sample.duration)
                    if track.kind == "video" and sample.pts < end and sample.pts + sample.duration > start:
                        visible.append(sample)
                if len(bounds) == len(self.tracks) and all(
                        last * 90000 >= end * self.tracks[identity].timescale
                        for identity, (_, last) in bounds.items()):
                    if not visible:
                        raise LookupError("replay track start is unavailable")
                    return tuple(selected), tuple(visible)
            unit_index = 0
        raise LookupError("replay fragment tail is not published")

    def _read_bytes(self, units, active):
        if not active[0]:
            raise RuntimeError("fragment read lease is closed")
        yield self._init
        with ExitStack() as streams:
            path, source, cursor = None, None, 0
            for unit in units:
                if not active[0]:
                    raise RuntimeError("fragment read lease is closed")
                if unit.path != path:
                    streams.close()
                    source = streams.enter_context(unit.path.open("rb"))
                    path, cursor = unit.path, 0
                if cursor != unit.offset:
                    source.seek(unit.offset)
                remaining = unit.size
                while remaining:
                    if not active[0]:
                        raise RuntimeError("fragment read lease is closed")
                    data = source.read(min(64 * 1024, remaining))
                    if not data:
                        raise OSError("published fragment was truncated")
                    remaining -= len(data)
                    yield data
                cursor = unit.offset + unit.size
