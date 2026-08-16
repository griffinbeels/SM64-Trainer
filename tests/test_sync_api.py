# tests/test_sync_api.py
"""GET /api/sync + PUT /api/sync/verdict -- the coverage dashboard's own API.

Registers a couple of throwaway gates through `sync.gates.register` (the same
save/restore-`GATES` fixture `tests/test_sync_gates_contract.py` uses) rather
than depending on address_gates.py/calibration_gates.py/feature_gates.py --
those fill in on their own tracks and may be empty in this worktree today.
"""
import pytest
from fastapi.testclient import TestClient

from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.sync import registry as _registry  # noqa: F401  -- load the REAL registry before any fixture clears it
from sm64_events.sync import gates as G
from sm64_events.sync.report import report_path


class OfflineMemory:
    attached = False
    def attach(self): return False
    def detach(self): pass


def _ok(ctx):
    return G.Verdict("verified")


@pytest.fixture(autouse=True)
def _empty_registry():
    """GATES is module-global; every test starts and ends with the real
    registry untouched, the same discipline test_sync_gates_contract.py uses."""
    saved = list(G.GATES)
    G.GATES.clear()
    G.register(
        G.Gate("address.global_timer", "version", "address",
              "watch the counter tick", "the address is right", _ok),
        G.Gate("cal.igt_clock.DISPLAY_TICK", "IGT clock", "calibration",
              "grab a star and compare", "the JP tick matches US", _ok,
              backs="detectors.igt_clock.DISPLAY_TICK"))
    yield
    G.GATES.clear()
    G.GATES.extend(saved)


def _client(tmp_path, monkeypatch):
    # Same shape as test_diagnostics_api.py's own report-dir monkeypatch:
    # the router calls report_path() lazily per request, so redirecting it
    # here means every request in this test writes into tmp_path instead of
    # the real data/version_sync -- no test may touch the real journal dir.
    monkeypatch.setattr(
        "sm64_events.server.sync_api.report_path",
        lambda version, root=None: report_path(version, tmp_path))
    broadcaster = Broadcaster()
    poller = Poller(OfflineMemory(), [], broadcaster)
    return TestClient(create_app(poller, broadcaster)), broadcaster


def test_get_returns_every_registered_gate_and_both_empty_reports(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    with client:
        body = client.get("/api/sync").json()
    assert body["features"] == list(G.FEATURES)
    ids = {g["id"] for g in body["gates"]}
    assert ids == {"address.global_timer", "cal.igt_clock.DISPLAY_TICK"}
    assert body["reports"]["us"] == {}
    assert body["reports"]["jp"] == {}


def test_put_then_get_shows_the_verdict_and_writes_the_report_file(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    with client:
        resp = client.put("/api/sync/verdict", json={
            "version": "jp", "gate_id": "address.global_timer",
            "verdict": {"status": "verified", "value": 0x8032C694},
            "at": "2026-08-15T00:00:00Z",
        })
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        body = client.get("/api/sync").json()
    entry = body["reports"]["jp"]["address.global_timer"]
    assert entry["status"] == "verified"
    assert entry["value"] == 0x8032C694
    assert (tmp_path / "version_sync" / "jp.json").is_file()


def test_unknown_gate_id_is_404(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    with client:
        resp = client.put("/api/sync/verdict", json={
            "version": "jp", "gate_id": "address.no_such_gate",
            "verdict": {"status": "verified"}, "at": "2026-08-15T00:00:00Z",
        })
    assert resp.status_code == 404


def test_unknown_version_is_400(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    with client:
        resp = client.put("/api/sync/verdict", json={
            "version": "eu", "gate_id": "address.global_timer",
            "verdict": {"status": "verified"}, "at": "2026-08-15T00:00:00Z",
        })
    assert resp.status_code == 400


def test_bad_verdict_status_is_400(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    with client:
        resp = client.put("/api/sync/verdict", json={
            "version": "jp", "gate_id": "address.global_timer",
            "verdict": {"status": "meh"}, "at": "2026-08-15T00:00:00Z",
        })
    assert resp.status_code == 400


def test_the_put_broadcasts_sync_verdict_over_the_websocket(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    with client:
        with client.websocket_connect("/ws/events") as ws:
            resp = client.put("/api/sync/verdict", json={
                "version": "us", "gate_id": "cal.igt_clock.DISPLAY_TICK",
                "verdict": {"status": "failed", "measured": {"jp": 4, "us": 3}},
                "at": "2026-08-15T00:00:00Z",
            })
            assert resp.status_code == 200
            msg = ws.receive_json()
            assert msg["type"] == "sync_verdict"
            assert msg["payload"]["version"] == "us"
            assert msg["payload"]["gate_id"] == "cal.igt_clock.DISPLAY_TICK"
            assert msg["payload"]["verdict"]["status"] == "failed"
