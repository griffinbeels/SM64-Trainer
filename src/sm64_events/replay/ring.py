"""One budget for unsaved replay segments and extracted clip/cache groups.

Readers lease source spans or temporary groups before touching files; leases
also protect segments arriving while an extraction waits for its tail. Pressure
evicts the oldest eligible media, never a leased file. Failed Windows unlinks
remain accounted and are retried, without escaping into the encoder callback.
Saved files never enter this owner. Its optional root rejects external paths.
"""
import logging
import threading
from collections import deque
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sm64_events.replay.media import MediaRun

log = logging.getLogger("sm64.replay")

# Free disk we refuse to consume: a near-full system volume thrashes the whole
# machine (Windows squeezes the pagefile), which reads as the same "everything
# is laggy / out of memory" symptom as a RAM leak. The buffer never grows so
# large that free space would drop below this.
_DISK_MARGIN_BYTES = 5 * 1024 ** 3


def effective_cap(configured_cap: int, free_bytes: int, current_total: int,
                  *, margin_bytes: int = _DISK_MARGIN_BYTES) -> int:
    """The byte cap actually enforced: the configured cap, but never so large
    that free disk would fall below margin_bytes. free_bytes is space free NOT
    counting our buffer; on top of what we already hold we may grow into
    (free - margin), and when free has ALREADY dropped below the margin that
    term is negative — the cap falls below current_total so eviction reclaims
    the deficit (a disk that filled under us shrinks the buffer back). Pure —
    unit-tested."""
    return min(configured_cap, current_total + (free_bytes - margin_bytes))


@dataclass(frozen=True)
class SegmentInfo:
    path: Path
    kind: str               # "video" | "audio"
    utc_start: datetime
    utc_end: datetime
    size_bytes: int
    # Encoded frame size. The player is free to resize the emulator window
    # mid-session, which restarts the encoder at the new size — segments on
    # either side of that cannot be concatenated (the extractor would silently
    # rescale, and squash the picture if the aspect changed), so the extractor
    # treats a dims change like a coverage hole. None = unknown (audio chunks,
    # the in-process fallback writer): never forces a break.
    dims: tuple[int, int] | None = None
    # Only the picture feed promises source PTS relative to this exact run.
    # None means the source clock is unknown, including legacy/CFR segments.
    media_run: MediaRun | None = None


@dataclass
class _Temporary:
    paths: dict[Path, int]
    utc_start: datetime
    utc_end: datetime


class SegmentRing:
    def __init__(self, retention_s: float | None, max_bytes: int,
                 free_bytes_fn=None,
                 disk_margin_bytes: int = _DISK_MARGIN_BYTES,
                 on_evict=None, scratch_root: Path | None = None,
                 deletion_guard=None):
        self._retention_s = retention_s
        self._max_bytes = max_bytes
        # free_bytes_fn() -> bytes free on the scratch volume (None = no disk
        # gating, e.g. unit tests). When set, eviction also caps the buffer so
        # free disk can't drop below disk_margin_bytes regardless of max_bytes.
        self._free_bytes_fn = free_bytes_fn
        self._disk_margin = disk_margin_bytes
        self._segments: deque[SegmentInfo] = deque()
        self._total_bytes = 0
        self._lock = threading.RLock()  # free-space probes may read total_bytes
        self._on_evict = on_evict
        self._root = scratch_root.resolve() if scratch_root is not None else None
        self._deletion_guard = deletion_guard or (lambda: nullcontext(True))
        self._temporary: dict[str, _Temporary] = {}
        self._span_pins: dict[object, tuple[str, datetime, datetime]] = {}
        self._temp_pins: dict[str, int] = {}
        self._forget: dict[str, bool] = {}
        self._pending: dict[Path, int] = {}
        self._unmanaged: dict[Path, int] = {}  # producer/index files: counted, never evicted
        self._clear_segments: set[Path] = set()
        self._now: datetime | None = None
        self.storage_pressure = False

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def reset(self) -> None:
        """Forget metadata after the recorder owner resets its scratch files."""
        with self._lock:
            self._segments.clear()
            self._temporary.clear()
            self._pending.clear()
            self._unmanaged.clear()
            self._clear_segments.clear()
            self._forget.clear()
            self._now = None
            self.storage_pressure = False
            self._recount()

    def _path(self, path: Path) -> Path:
        path = Path(path).resolve()
        if self._root is not None and not path.is_relative_to(self._root):
            raise ValueError("temporary replay must be inside scratch")
        return path

    @contextmanager
    def pin(self, kind: str, start: datetime, end: datetime):
        """Lease this source interval, including any tail arriving during it."""
        token = object()
        with self._lock:
            self._span_pins[token] = (kind, start, end)
            segments = self.covering(kind, start, end)
        try:
            yield segments
        finally:
            with self._lock:
                del self._span_pins[token]
                self._evict()

    @contextmanager
    def pin_temp(self, group: str):
        """Reserve a group before creation, or lease it through a read/save."""
        with self._lock:
            self._temp_pins[group] = self._temp_pins.get(group, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                count = self._temp_pins[group] - 1
                if count:
                    self._temp_pins[group] = count
                else:
                    del self._temp_pins[group]
                    if group in self._forget:
                        self.forget_temp(group, self._forget[group])
                self._evict()

    def register_temp(self, group: str, paths, start: datetime, end: datetime) -> None:
        """Account a clip and its sidecars together, ordered by source age.

        Paths may not exist yet: register growing output under pin_temp, then
        maintain() refreshes its byte count. Re-registration adds paths to the
        same group, so atomic-publish partials remain accounted until removed.
        """
        checked = [self._path(path) for path in paths]
        with self._lock:
            entry = self._temporary.setdefault(group, _Temporary({}, start, end))
            entry.utc_start, entry.utc_end = start, end
            entry.paths.update({path: entry.paths.get(path, 0) for path in checked})
            self._advance(end)
            self._evict()

    def forget_temp(self, group: str, delete: bool = False) -> None:
        """Forget a transferred group or delete it once its last lease ends."""
        with self._lock:
            if self._temp_pins.get(group):
                self._forget[group] = delete or self._forget.get(group, False)
            else:
                with self._deletion_guard() as allowed:
                    if delete and not allowed:
                        self._forget[group] = True
                    else:
                        self._forget.pop(group, None)
                        self._drop_temp(group, delete)
            self._recount()

    def maintain(self) -> None:
        """Refresh growing files, retry failed unlinks and enforce pressure."""
        with self._lock:
            if self._root is not None:
                self._inventory()
            self._evict()

    def _inventory(self) -> None:
        """Periodic only: count mutable encoder files and SQLite/WAL overhead.

        Unregistered files are not eviction candidates: the producer may still
        be writing. add/register_temp make closed media eligible. Leases and
        mutable files can exceed a cap until eligible files become available.
        """
        found = {}
        try:
            for path in self._root.rglob("*"):
                if path.is_symlink() or not path.resolve().is_relative_to(self._root):
                    continue
                try:
                    if path.is_file():
                        found[path.resolve()] = path.stat().st_size
                except FileNotFoundError:
                    continue  # atomic publication/segment close moved it
        except OSError:
            return  # retain prior accounting while the volume is unavailable
        self._unmanaged = found

    def protected_paths(self) -> set[Path]:
        """Files currently leased; recorder cleanup must preserve these too."""
        with self._lock:
            return ({s.path.resolve() for s in self._segments if self._pinned(s)}
                    | {p for key, entry in self._temporary.items()
                       if self._temp_pins.get(key) for p in entry.paths})

    def prune_missing(self) -> None:
        """Reconcile after owner-only scratch cleanup, retaining leased files."""
        with self._lock:
            self._segments = deque(s for s in self._segments if s.path.exists())
            for key, entry in list(self._temporary.items()):
                if not any(p.exists() for p in entry.paths):
                    del self._temporary[key]
            if self._root is not None:
                self._inventory()
            self._refresh()

    def clear(self) -> None:
        """Delete eligible unsaved files; leases survive until their release."""
        with self._lock:
            for seg in list(self._segments):
                self._clear_segments.add(seg.path)
            for key in list(self._temporary):
                self.forget_temp(key, delete=True)
            self._evict()

    @property
    def retention_s(self) -> float | None:
        return self._retention_s

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def set_limits(self, retention_s: float | None, max_bytes: int) -> None:
        """Live-apply new eviction limits (the UI settings panel) and evict
        immediately — a user who just shrank the cap expects disk to free
        now, not when the next segment lands."""
        with self._lock:
            self._retention_s = retention_s
            self._max_bytes = max_bytes
            self._evict()

    def add(self, seg: SegmentInfo) -> None:
        """Keep source order even when an old child's final CSV arrives late."""
        self._path(seg.path)
        with self._lock:
            late = bool(self._segments and seg.utc_start < self._segments[-1].utc_start)
            self._segments.append(seg)
            if late:
                self._segments = deque(sorted(self._segments, key=lambda item: item.utc_start))
            self._advance(seg.utc_end)
            self._evict()

    def discard(self, seg: SegmentInfo) -> None:
        """Drop an idle source after any extraction waiting for its tail ends."""
        with self._lock:
            self._clear_segments.add(seg.path)
            self.add(seg)

    def _advance(self, end):
        self._now = max(self._now, end) if self._now is not None else end

    def _pinned(self, seg):
        return any(kind == seg.kind and seg.utc_end > start and seg.utc_start < end
                   for kind, start, end in self._span_pins.values())

    def _unlink(self, path, size):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            self._pending[path] = size
            log.debug("replay eviction deferred (file busy): %s", path)
        else:
            self._pending.pop(path, None)
            self._unmanaged.pop(path.resolve(), None)

    def _drop_segment(self, seg):
        self._segments.remove(seg)
        self._clear_segments.discard(seg.path)
        self._unlink(seg.path, seg.size_bytes)
        if self._on_evict is not None:
            try:
                self._on_evict(seg)
            except Exception:
                log.exception("replay eviction metadata callback failed")

    def _drop_temp(self, key, delete=True):
        entry = self._temporary.pop(key, None)
        if delete and entry is not None:
            for path, size in entry.paths.items():
                self._unlink(path, size)

    def _recount(self):
        tracked = ({s.path.resolve() for s in self._segments}
                   | {p for entry in self._temporary.values() for p in entry.paths}
                   | {p.resolve() for p in self._pending})
        self._total_bytes = (sum(s.size_bytes for s in self._segments)
                             + sum(sum(e.paths.values()) for e in self._temporary.values())
                             + sum(self._pending.values())
                             + sum(size for p, size in self._unmanaged.items() if p not in tracked))

    def _refresh(self):
        for entry in self._temporary.values():
            for path in entry.paths:
                try:
                    entry.paths[path] = path.stat().st_size
                except FileNotFoundError:
                    entry.paths[path] = 0
                except OSError:
                    pass  # retain previous accounting until volume recovers
        self._recount()

    def _evict(self) -> None:
        # The recorder suspends this guard before releasing machine ownership.
        # A detached reader may release later, after another owner reused paths.
        with self._deletion_guard() as allowed:
            if allowed:
                self._evict_owned()
            else:
                self._refresh()

    def _evict_owned(self) -> None:
        # Caller holds lock. One deletion attempt per path per maintenance pass.
        for path, size in list(self._pending.items()):
            self._unlink(path, size)
        for group, delete in list(self._forget.items()):
            if not self._temp_pins.get(group):
                self._drop_temp(group, delete)
                del self._forget[group]
        self._refresh()
        cap = self._max_bytes
        floor_cap = self._total_bytes
        if self._free_bytes_fn is not None:
            try:
                free = self._free_bytes_fn()
            except OSError:
                free = None  # scratch volume not ready/gone — fall back to cap
            if free is not None:
                floor_cap = self._total_bytes + free - self._disk_margin
                cap = effective_cap(self._max_bytes, free, self._total_bytes,
                                    margin_bytes=self._disk_margin)
        horizon = (self._now - timedelta(seconds=self._retention_s)
                   if self._now is not None and self._retention_s is not None else None)
        candidates = [(s.utc_start, s.utc_end, s) for s in self._segments if not self._pinned(s)]
        candidates += [(e.utc_start, e.utc_end, key) for key, e in self._temporary.items()
                       if not self._temp_pins.get(key)]
        for _, end, item in sorted(candidates, key=lambda row: row[0]):
            clearing = isinstance(item, SegmentInfo) and item.path in self._clear_segments
            if not clearing and self._total_bytes <= cap and (horizon is None or end > horizon):
                continue
            if isinstance(item, SegmentInfo):
                self._drop_segment(item)
            else:
                self._drop_temp(item)
            self._recount()
        self.storage_pressure = self._total_bytes > floor_cap

    def covering(self, kind: str, start: datetime, end: datetime) -> list[SegmentInfo]:
        with self._lock:
            return [s for s in self._segments
                    if s.kind == kind and s.utc_end > start and s.utc_start < end]

    def coverage(self, kind: str) -> tuple[datetime, datetime] | None:
        with self._lock:
            ks = [s for s in self._segments if s.kind == kind]
            if not ks:
                return None
            return ks[0].utc_start, ks[-1].utc_end
