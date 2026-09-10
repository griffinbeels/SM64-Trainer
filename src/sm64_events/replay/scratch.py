"""Scratch deletion guarded by the recorder lock AND a lifetime token.

The caller holds the machine-wide recorder lock for prepare/cleanup. A later
viewer may acquire that lock, but cannot clean a previous owner's replacement
lifetime. Pending explicit saves and active media leases supply protected paths.
This module only visits regular descendants and never follows directory links.
"""
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class OwnedScratch:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.token = uuid4().hex
        self.marker = self.root / ".session-owner"
        self._mutation_lock = threading.RLock()
        self._active = False

    def resume_deletion(self) -> None:
        """Enable ring deletion only after acquiring ownership and preparing it."""
        with self._mutation_lock:
            self._active = self.owns()

    def pause_deletion(self) -> None:
        """Drain any deletion before releasing the machine-wide recorder lock."""
        with self._mutation_lock:
            self._active = False

    @contextmanager
    def deletion_guard(self):
        """Serialize lease finalizers with ownership release, without capture locks."""
        with self._mutation_lock:
            yield self._active and self.owns()

    def owns(self) -> bool:
        try:
            return self.marker.read_text(encoding="utf-8") == self.token
        except OSError:
            return False

    def prepare(self, preserve=()) -> None:
        """Claim a lifetime after lock acquisition, retaining recovery sources."""
        self.root.mkdir(parents=True, exist_ok=True)
        if self.marker.is_symlink() or not self.marker.resolve().is_relative_to(self.root):
            raise ValueError("scratch ownership marker must stay inside scratch")
        self._remove(preserve)
        self.marker.write_text(self.token, encoding="utf-8")

    def cleanup(self, preserve=()) -> bool:
        """Delete only this lifetime; a replaced marker makes cleanup a no-op."""
        if not self.owns():
            return False
        self._remove(preserve)
        # Keep the ownership evidence while files are protected or still busy,
        # so a later explicit cleanup may retry without touching a new owner.
        if not any(path != self.marker for path in self.root.iterdir()):
            self.marker.unlink(missing_ok=True)
        return True

    def _remove(self, preserve) -> None:
        protected = [Path(path).resolve() for path in preserve]

        def keep(path):
            return any(path == item or path.is_relative_to(item) for item in protected)

        if keep(self.root):
            return
        for directory, dirs, files in os.walk(self.root, topdown=False, followlinks=False):
            for name in files:
                path = Path(directory) / name
                resolved = path.resolve()
                if path == self.marker or not resolved.is_relative_to(self.root) or keep(resolved):
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass  # sharing violations leave debris for the next owned pass
            for name in dirs:
                path = Path(directory) / name
                resolved = path.resolve()
                if path.is_symlink() or not resolved.is_relative_to(self.root) or keep(resolved):
                    continue
                try:
                    path.rmdir()  # only empty directories; never a recursive delete
                except OSError:
                    pass
