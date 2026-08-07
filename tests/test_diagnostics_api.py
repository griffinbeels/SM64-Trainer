# tests/test_diagnostics_api.py
"""POST /api/diagnostics writes a real report file and returns its path.
The db-less (broadcast-only) shape must still produce a report."""
from pathlib import Path

from fastapi.testclient import TestClient

from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService


class OfflineMemory:
    attached = False
    def attach(self): return False
    def detach(self): pass


def _client(tmp_path, monkeypatch, with_db=True):
    monkeypatch.setattr("sm64_events.server.app.diagnostics_dir",
                        lambda: tmp_path / "diag")
    broadcaster = Broadcaster()
    service = (TrackerService(Database(tmp_path / "t.db"), broadcaster)
               if with_db else None)
    # No service -> the poller's sink is the broadcaster (test_app.py's shape,
    # which mirrors main.py's broadcast-only mode).
    poller = Poller(OfflineMemory(), [], service if with_db else broadcaster)
    return TestClient(create_app(poller, broadcaster, service=service))


def test_endpoint_writes_a_report_and_returns_its_path(tmp_path, monkeypatch):
    resp = _client(tmp_path, monkeypatch).post("/api/diagnostics")
    assert resp.status_code == 200
    body = resp.json()
    report = Path(body["path"])
    assert report.is_file()
    assert report.parent == tmp_path / "diag"
    assert body["size_bytes"] == report.stat().st_size > 0
    text = report.read_text(encoding="utf-8")
    assert text.startswith("# SM64 Trainer debug report")
    assert "## Health" in text


def test_db_less_server_still_reports(tmp_path, monkeypatch):
    resp = _client(tmp_path, monkeypatch, with_db=False).post("/api/diagnostics")
    assert resp.status_code == 200
    text = Path(resp.json()["path"]).read_text(encoding="utf-8")
    assert "journal unavailable" in text     # named, not crashed
