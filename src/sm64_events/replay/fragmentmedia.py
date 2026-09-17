"""Session fragment ownership and finite native replay selections.

The ring budgets encoded bytes; this registry budgets metadata across encoder
restarts. Range readers lease shared extents. Only explicit exports materialize
media. Eviction callbacks queue identity pruning outside the ring lock, avoiding
archive -> ring / ring -> archive lock inversion on the capture thread.
"""
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import threading
from types import SimpleNamespace

from sm64_events.replay.fragmentstore import FragmentArchive
from sm64_events.replay.virtualmp4 import VirtualMp4


class FragmentMedia:
    def __init__(self, directory, ring, ledger, *, discard=None, idle_window=None):
        self.directory, self.ring, self.ledger = directory, ring, ledger
        self._discard = discard
        self._idle_window = idle_window
        self._runs, self._evicted = {}, deque()
        self._media = {}  # (run_id, start, end) -> VirtualMp4 headers for that immutable descriptor
        self._lock = threading.RLock()
        self._maintenance = threading.Lock()
        self.enabled = False

    def create(self, run, dims):
        archive = FragmentArchive(self.directory, self.ring, run, discard=self._discard,
                                  idle_window=self._idle_window)
        with self._lock:
            self._runs[run.id] = archive
        return archive

    def evicted(self, group, start, end):
        if group.startswith("fragments:"):
            self._evicted.append((group.split(":")[1], start, end))

    def reset(self):
        with self._lock:
            self._runs.clear()
            self._evicted.clear()
            self._media.clear()

    def _snapshot(self):
        with self._lock:
            return tuple(self._runs.values())

    def maintain(self):
        # Settings and recorder maintenance may overlap. Only one consumer
        # may acknowledge queued intervals; competing callers never wait.
        if not self._maintenance.acquire(blocking=False):
            return
        try:
            self._maintain()
        finally:
            self._maintenance.release()

    def _maintain(self):
        archives = self._snapshot()
        retained = []
        for archive in archives:
            if archive.coverage() is not None:
                retained.append(archive)
        count = sum(a.sample_count for a in retained)
        for archive in retained:
            if count <= 131072:
                break
            if archive.closed:
                count -= archive.sample_count
                archive.discard()
        self._discard_evicted()
        # A leased extent can outlive archive metadata. Keep its run clock
        # until the eventual ring eviction has been acknowledged too.
        groups = {group.split(":")[1] for group in self.ring.temporary_groups()
                  if group.startswith("fragments:")}
        with self._lock:
            pending = groups | {entry[0] for entry in self._evicted.copy()}
            for archive in archives:
                if (archive.closed and not archive.sample_count
                        and archive.run.id not in pending):
                    self._runs.pop(archive.run.id, None)
                    for key in [k for k in self._media if k[0] == archive.run.id]:
                        self._media.pop(key, None)

    def _discard_evicted(self):
        # A failed callback keeps both the interval and its archive reachable.
        while True:
            try:
                entry = self._evicted[0]
            except IndexError:
                break
            identity, start, end = entry
            with self._lock:
                archive = self._runs.get(identity)
            if archive is not None:
                self.ledger.discard_segment(SimpleNamespace(kind="video", media_run=archive.run,
                    utc_start=start, utc_end=end))
            with self._lock:
                # reset() may have retired the old queue while the callback
                # ran. Never acknowledge an entry from a newer session.
                if self._evicted and self._evicted[0] is entry:
                    self._evicted.popleft()

    def expire_before(self, cutoff):
        for archive in self._snapshot():
            archive.expire_before(cutoff)
        self.maintain()

    def intervals(self):
        result = []
        for archive in self._snapshot():
            bounds = archive.coverage()
            if bounds is not None:
                result.append((archive, *bounds))
        return result

    def coverage(self):
        intervals = self.intervals()
        if not intervals:
            return None
        return (self._utc(min(a.run.origin_ts + low / 90000 for a, low, _ in intervals)),
                self._utc(max(a.run.origin_ts + high / 90000 for a, _, high in intervals)))

    def covers(self, start, end):
        for archive, low, high in self.intervals():
            begin, finish = archive.run.ticks_at(start.timestamp()), archive.run.ticks_at(end.timestamp())
            if low <= begin and finish <= high:
                try:
                    if archive.window(begin, finish) == (begin, finish):
                        return True
                except (LookupError, ValueError):
                    continue
        return False

    @staticmethod
    def _utc(stamp):
        return datetime.fromtimestamp(stamp, timezone.utc)

    @contextmanager
    def open(self, start, end, *, descriptor=None, required_span=None):
        """Freeze one published interval; retain the picture held at its start.

        Optional post-roll is clamped to published coverage instead of waiting
        for future gameplay. Never join encoder restarts or move source PTS.
        """
        if descriptor is not None:
            if descriptor.get("version") != 1:
                raise LookupError("unsupported replay fragment selection")
            with self._lock:
                archive = self._runs.get(descriptor.get("run_id"))
            if archive is None:
                raise LookupError("replay footage is no longer retained")
            low, high = descriptor["start"], descriptor["end"]
        else:
            candidates = [(a, max(lo, a.run.ticks_at(start.timestamp())),
                           min(hi, a.run.ticks_at(end.timestamp()))) for a, lo, hi in self.intervals()]
            candidates = [item for item in candidates if item[2] > item[1]]
            if not candidates:
                raise ValueError("No published replay footage is available yet")
            # Pre-roll can overlap an old encoder run after reconnect. Prefer
            # a run containing the actual attempt over that irrelevant prefix.
            # Never concatenate across epochs or hide a real internal gap.
            if required_span is not None:
                complete = [item for item in candidates
                            if self._contains_span(item[0], required_span)]
                if complete:
                    candidates = complete
            # Preserve the earliest available interval, matching legacy cuts;
            # a restart/resize remains a visible truncation rather than a join.
            archive, low, high = min(candidates, key=lambda item: item[0].run.origin_ts + item[1] / 90000)
            low, high = archive.window(low, high)
        with archive.selection(low, high) as (init, tracks, units, visible):
            low = visible[0].pts
            # A descriptor names immutable bytes: the same headers serve every
            # Range request for it. The selection lease is still taken per
            # request (it is what keeps the extents on disk); only the O(samples)
            # header build is reused. VirtualMp4 holds paths, never handles.
            key = (archive.run.id, low, high)
            with self._lock:
                media = self._media.get(key)
            if media is None:
                media = VirtualMp4(init, tracks, units, low, high)
                media.identity = f"{archive.run.id}:{low}:{high}"
                with self._lock:
                    if len(self._media) >= 8:
                        self._media.pop(next(iter(self._media)))
                    self._media[key] = media
            actual_start = self._utc(archive.run.origin_ts + low / 90000)
            source = {"version": 1, "run_id": archive.run.id, "start": low, "end": high}
            result = SimpleNamespace(start_utc=actual_start, duration_s=(high-low)/90000,
                truncated=(actual_start-start).total_seconds() > 0.5
                          or (end.timestamp()-archive.run.origin_ts-high/90000) > 0.5,
                video_start_s=0.0, frame_times=[tick/90000 for tick in media.frame_ticks],
                media_run=archive.run, source_pts=[low+tick for tick in media.frame_ticks])
            yield media, result, source

    @staticmethod
    def _contains_span(archive, span):
        low, high = (archive.run.ticks_at(at.timestamp()) for at in span)
        try:
            return archive.window(low, high) == (low, high)
        except (LookupError, ValueError):
            return False

    @contextmanager
    def read(self, descriptor):
        # The descriptor, not today's padding/coverage, defines existing URLs.
        with self.open(self._utc(0), self._utc(0), descriptor=descriptor) as (media, _, _):
            yield media
