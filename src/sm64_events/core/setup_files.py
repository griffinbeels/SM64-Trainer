"""Restore installation files if a write fails before selecting the plugin."""
from contextlib import contextmanager
import logging

log = logging.getLogger("sm64.setup")


def _unchanged(path, contents) -> bool:
    try:
        return (path.read_bytes() if path.exists() else None) == contents
    except OSError:
        return False


@contextmanager
def restore_on_failure(paths):
    """Put back every file the failed block CHANGED. A file it never wrote
    is left alone: rewriting it gains nothing, and a plugin DLL Project64
    has loaded refuses the write (2026-09-28: two ERROR tracebacks for DLLs
    a failed refresh had never touched)."""
    before = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        yield
    except OSError:
        for path, contents in before.items():
            if _unchanged(path, contents):
                continue
            try:
                if contents is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(contents)
            except OSError:
                log.exception("could not restore installation file %s", path)
        raise
