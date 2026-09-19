"""What the overlay window actually did, written where it can be read later.

**Why this exists.** His first try of the ghost put a black rectangle half
again as big as the game over his other apps and left Project64's own window
disturbed, and every visible property of that window -- its size, its opacity,
whether it was even above the emulator -- was unprovable from anything on
disk. It could only be read off two screenshots he had to take himself. The
instrument lesson in `round-1-first-try.md` is that the next try must be
readable without them.

So the child writes one line per decision here: the DPI context it is actually
running in (asked for AND read back), the rectangle it read off Project64, the
rectangle it asked Windows for, the rectangle Windows gave it, the z-order
handle it inserted after, the alpha it asked for and the alpha the window
kept, every show and hide with its reason, and every error. The session writes
the child's own event stream into the same file, so a child that dies before
it can write -- a WebView2 that will not start -- still leaves its last words
in one place.

Same shape and same trim policy as `core/uilog.py`, for the same reason: this
is a debugging instrument for the LAST run, never an archive, and a bounded
file is what keeps it cheap enough to write on every placement.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from sm64_events.core.paths import data_root

#: Set by a test that wants the child's log somewhere of its own. The child is
#: a separate process, so an env var is the only handle a test has on it.
PATH_ENV = "SM64_GHOST_WINDOW_LOG"

MAX_RECORDS = 3000
TRIM_ABOVE_BYTES = 1_000_000

_lock = threading.Lock()


def log_path() -> Path:
    override = os.environ.get(PATH_ENV)
    if override:
        return Path(override)
    return data_root() / "data" / "ghost_window.jsonl"


def record(event: str, **fields) -> dict | None:
    """Append one line. Returns it, or None if it could not be written.

    NOTHING here may raise: this file is the thing you read when the overlay
    went wrong, and a logger that can end the overlay is a logger that deletes
    its own evidence.
    """
    entry = {"utc": datetime.now(timezone.utc).isoformat(),
             "pid": os.getpid(), "event": event}
    for key, value in fields.items():
        entry[key] = _plain(value)
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            # newline="" so Python does not translate to CRLF on Windows --
            # this file is read back line by line and diffed by eye.
            with path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if path.stat().st_size > TRIM_ABOVE_BYTES:
                _trim(path)
    except OSError:
        return None
    return entry


def _plain(value):
    """Anything a caller hands us, as JSON can carry it.

    A `Rect` arrives as its four numbers rather than as a repr, so the file can
    be compared arithmetically instead of by eye.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    as_list = getattr(value, "as_list", None)
    if callable(as_list):
        return as_list()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return str(value)


def _trim(path: Path) -> None:
    kept = path.read_text(encoding="utf-8").splitlines()[-MAX_RECORDS:]
    path.write_text("".join(line + "\n" for line in kept),
                    encoding="utf-8", newline="")


def read(path: Path | None = None) -> list[dict]:
    """Every line, oldest first; a torn last line is skipped, never raised on.

    The process that writes this file is the one that may have been killed, so
    the reader has to survive half a line.
    """
    target = log_path() if path is None else path
    if not target.exists():
        return []
    out = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out
