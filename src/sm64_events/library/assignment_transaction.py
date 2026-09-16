"""Assignment edits either activate together or restore their previous state."""
from contextlib import nullcontext
from copy import deepcopy
from functools import wraps
import os
from pathlib import Path
import tempfile


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_assignment(operation):
    @wraps(operation)
    def change(self, *args, **kwargs):
        registry = getattr(self.store, "calibrations", None)
        with registry.update_lock if registry is not None else nullcontext():
            previous = deepcopy((self._rows, self._unlinked, self._automatic))
            saved = self.path.read_bytes() if self.path.exists() else None
            try:
                return operation(self, *args, **kwargs)
            except Exception:
                self._rows, self._unlinked, self._automatic = previous
                if saved is None:
                    self.path.unlink(missing_ok=True)
                else:
                    atomic_bytes(self.path, saved)
                raise
    return change
