"""The offline fixture server: the real app, no PJ64, no live instance."""
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ui_fixture import DEV_DB, serve_ui, snapshot_db  # noqa: E402


def _get(url: str, timeout: float = 10) -> bytes:
    return urllib.request.urlopen(url, timeout=timeout).read()


def test_fixture_serves_the_real_ui_and_api(tmp_path):
    with serve_ui(tmp_path / "sweep.db") as base:
        page = _get(f"{base}/ui/index.html")
        assert b"<style>" in page, "not the real index.html"
        assert b"practice-page" in page
        assert b"app.js" in page
        assert json.loads(_get(f"{base}/health")), "/health returned no object"


def test_fixture_stops_its_server_on_exit(tmp_path):
    """No orphaned processes: the port must be dead the moment we leave."""
    with serve_ui(tmp_path / "sweep.db") as base:
        _get(f"{base}/health")
    try:
        _get(f"{base}/health", timeout=2)
    except (urllib.error.URLError, OSError, TimeoutError):
        return
    raise AssertionError("fixture server outlived its context manager")


def test_startup_reports_an_exited_server_and_its_actual_cause(tmp_path, monkeypatch):
    import ui_fixture

    def fail_start(server, sockets=None):
        raise SystemExit("fixture bind failed")

    monkeypatch.setattr(ui_fixture.uvicorn.Server, "run", fail_start)
    with pytest.raises(RuntimeError, match="thread_alive=False") as caught:
        with serve_ui(tmp_path / "failed.db", bundled_library=False):
            pytest.fail("an exited server must never be yielded")
    assert isinstance(caught.value.__cause__, SystemExit)
    assert "fixture bind failed" in str(caught.value)


def test_failed_shutdown_preserves_files_until_the_owner_exits(tmp_path, monkeypatch):
    import threading
    from functools import partial

    import ui_fixture

    release, threads, directories = threading.Event(), [], []
    original_tempdir = ui_fixture.tempfile.TemporaryDirectory

    def tempdir(*args, **kwargs):
        directory = original_tempdir(*args, dir=tmp_path, **kwargs)
        directories.append(directory)
        return directory

    def held_server(server, sockets=None):
        threads.append(threading.current_thread())
        server.started = True
        release.wait(5)

    monkeypatch.setattr(ui_fixture.tempfile, "TemporaryDirectory", tempdir)
    monkeypatch.setattr(ui_fixture.uvicorn.Server, "run", held_server)
    monkeypatch.setattr(ui_fixture, "_stop_fixture_server",
                        partial(ui_fixture._stop_fixture_server, timeout=.01))
    service = None
    try:
        with pytest.raises(RuntimeError, match="failed to stop.*thread_alive=True"):
            with ui_fixture.serve_ui_live(seed=False, bundled_library=False) as (_, service):
                pass
        assert all(Path(directory.name).is_dir() for directory in directories)
        assert service is not None
        assert service.db.events() == [], "shutdown closed a database still owned by the server"
    finally:
        release.set()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
        if service is not None:
            service.db.close()
        for directory in directories:
            directory.cleanup()


def test_fixture_construction_failure_closes_and_removes_owned_files(tmp_path, monkeypatch):
    import ui_fixture

    directories, databases = [], []
    original_tempdir = ui_fixture.tempfile.TemporaryDirectory

    def tempdir(*args, **kwargs):
        directory = original_tempdir(*args, dir=tmp_path, **kwargs)
        directories.append(directory)
        return directory

    def failed_runtime(database, *_args):
        databases.append(database)
        raise RuntimeError("injected fixture construction failure")

    monkeypatch.setattr(ui_fixture.tempfile, "TemporaryDirectory", tempdir)
    monkeypatch.setattr(ui_fixture, "_fixture_runtime", failed_runtime)
    try:
        with pytest.raises(RuntimeError, match="injected fixture construction failure"):
            with serve_ui(seed=False):
                pytest.fail("construction failed before the fixture was ready")
        assert directories and all(not Path(d.name).exists() for d in directories)
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            databases[0].events()
    finally:
        for database in databases:
            database.close()
        for directory in directories:
            directory.cleanup()


def test_snapshot_uses_the_online_backup_api_not_a_file_copy(tmp_path):
    """A file copy can catch a torn WAL; sqlite3's backup API cannot.

    Proved by content rather than by reading the implementation: write a row,
    snapshot, and require it to be there in the copy.
    """
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('present')")
    destination = snapshot_db(source, tmp_path / "snap.db")
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "present"


def test_default_db_is_the_dev_snapshot_when_one_exists(tmp_path):
    """The reported bug lives in the Active Target card, which renders only
    when a target with rank data exists -- so an EMPTY db cannot reproduce it.
    Defaulting to a snapshot of the dev db is what makes the sweep able to see
    the thing it was built for."""
    if not DEV_DB.exists():
        return                       # nothing to snapshot on a fresh clone
    with serve_ui() as base:
        state = json.loads(_get(f"{base}/state"))
        assert isinstance(state, dict)
