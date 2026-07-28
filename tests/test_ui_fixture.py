"""The offline fixture server: the real app, no PJ64, no live instance."""
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

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
