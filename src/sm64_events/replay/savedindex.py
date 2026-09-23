"""Which saved clip belongs to which attempt, from ONE walk of the save root.

The filename is the index (`attempt_<id>_...mp4`, `service.py`'s class
docstring), and `find_saved` used to answer it with a recursive glob per
question. The ghost picker asks it for every candidate twice per resolve, so
one resolve walked his save tree ~18 times: 3.6 ms a walk, the largest single
cost of a ghost resolve (measured 2026-09-23 on his 265-file replay folder).

The walk now happens once and is kept. It stays honest three ways:
  * `add` -- this app's own save puts the new file in as it publishes it;
  * a HIT is checked on disk (one stat), and a hit that is gone re-walks once,
    so a clip deleted or moved in Explorer is never served;
  * the whole index is re-walked when older than `MAX_AGE_S`, so a clip copied
    IN by hand is found within that long -- the save root stays his to
    reorganise, which is what makes it the registry.

`generation` moves whenever the answer can have changed, so a reader holding a
derived answer (the ghost session) knows to ask again.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from time import monotonic

_NAME = re.compile(r"attempt_(\d+)_")


class SavedClipIndex:
    #: How stale the walk may get before a lookup re-walks: the delay before a
    #: clip copied into the save root by hand is found.
    MAX_AGE_S = 60.0

    def __init__(self, root: Path, clock=monotonic):
        self._root = root
        self._clock = clock
        self._lock = threading.Lock()
        self._by_key: dict[str, list[Path]] | None = None
        self._walked_at = 0.0
        self.generation = 0

    def find(self, attempt_id: int) -> Path | None:
        """The first clip in sorted order named for this attempt, exactly as
        `sorted(root.rglob(f"attempt_{id:04d}_*.mp4"))[0]` answered it."""
        key = f"{attempt_id:04d}"
        with self._lock:
            self._fresh()
            for attempt in range(2):
                paths = self._by_key.get(key, [])
                if not paths or paths[0].exists():
                    return paths[0] if paths else None
                if attempt == 0:
                    self._walk()          # gone since the walk: look again once
            return None

    def ids(self) -> set[int]:
        with self._lock:
            self._fresh()
            return {int(key) for key in self._by_key}

    def add(self, path: Path) -> None:
        """This app just published `path`: know it now, not a minute later."""
        match = _NAME.match(path.name)
        if match is None or path.suffix != ".mp4":
            return
        with self._lock:
            if self._by_key is None:
                return                    # the first lookup walks and finds it
            paths = self._by_key.setdefault(match.group(1), [])
            if path not in paths:
                paths.append(path)
                paths.sort()
                self.generation += 1

    def forget(self) -> None:
        """Re-walk on the next lookup."""
        with self._lock:
            self._by_key = None

    def _fresh(self) -> None:
        if self._by_key is None or self._clock() - self._walked_at >= self.MAX_AGE_S:
            self._walk()

    def _walk(self) -> None:
        found: dict[str, list[Path]] = {}
        if self._root.exists():
            for path in self._root.rglob("attempt_*.mp4"):
                match = _NAME.match(path.name)
                if match:
                    found.setdefault(match.group(1), []).append(path)
        for paths in found.values():
            paths.sort()
        if found != self._by_key:
            self.generation += 1
        self._by_key = found
        self._walked_at = self._clock()


__all__ = ["SavedClipIndex"]
