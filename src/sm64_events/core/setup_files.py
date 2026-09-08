"""Restore installation files if a write fails before selecting the plugin."""
from contextlib import contextmanager
import logging

log = logging.getLogger("sm64.setup")


@contextmanager
def restore_on_failure(paths):
    before = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        yield
    except OSError:
        for path, contents in before.items():
            try:
                if contents is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(contents)
            except OSError:
                log.exception("could not restore installation file %s", path)
        raise
