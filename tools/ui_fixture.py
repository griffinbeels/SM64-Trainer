"""Run the REAL app offline on a free port, for a browser to drive.

Never `python -m sm64_events.main` for this: that attaches to PJ64 and takes
the recorder lock, which is the only thing protecting the user's recording
while they play.  Here the poller gets a memory stub that never attaches, so
every route, template and stylesheet is the shipping one and nothing goes near
the emulator.

Why it defaults to a SNAPSHOT of the dev db rather than an empty one: the
surfaces most worth measuring only exist when there is data behind them.  The
Active Target card -- the one that clipped its own "Ready" row at 900x1180 --
renders nothing at all without a target carrying rank standards, so an empty
fixture would sweep a page that cannot show the defect and report it clean.

The snapshot goes through `sqlite3.Connection.backup`, never `shutil.copy`: a
file copy of a live WAL database can catch a torn write, and the failure looks
like corrupt data rather than a bad copy.
"""
from __future__ import annotations

import contextlib
import socket
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import uvicorn

from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

REPO = Path(__file__).resolve().parents[1]
DEV_DB = REPO / "data" / "tracker.db"


class _OfflineMemory:
    """Mirrors the stub tests/test_api.py has used since the first API test.

    Kept local rather than imported so tools/ and tests/ do not reach into each
    other's private helpers.
    """

    attached = False

    def attach(self) -> bool:
        return False

    def detach(self) -> None:
        pass


def snapshot_db(source: Path, destination: Path) -> Path:
    """Online-backup `source` to `destination` and return the destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as origin, \
            sqlite3.connect(destination) as copy:
        origin.backup(copy)
    return destination


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def serve_ui(db_path: Path | None = None, timeout: float = 30):
    """Yield the base URL of an offline instance; stop it on the way out.

    `db_path=None` means "a throwaway snapshot of the dev db if there is one,
    otherwise an empty database" -- the sweep wants realistic content, and a
    fresh clone must still work.
    """
    scratch = None
    if db_path is None:
        scratch = tempfile.TemporaryDirectory(prefix="sm64-fixture-")
        db_path = Path(scratch.name) / "fixture.db"
        if DEV_DB.exists():
            snapshot_db(DEV_DB, db_path)

    database = Database(db_path)
    broadcaster = Broadcaster()
    service = TrackerService(database, broadcaster)
    poller = Poller(_OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + timeout
        while not server.started and thread.is_alive() \
                and time.monotonic() < deadline:
            time.sleep(0.02)
        if not server.started:
            raise RuntimeError("fixture server failed to start within "
                               f"{timeout}s (port {port})")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        # Close the connection BEFORE removing the directory holding it.
        # Windows refuses to unlink an open file, so a leaked handle here is
        # not a warning -- it is a PermissionError that fails the caller.
        database.close()
        if scratch is not None:
            scratch.cleanup()
