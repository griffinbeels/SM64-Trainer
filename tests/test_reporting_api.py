"""Real local routes retain request protection and bound browser payloads."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.server.reporting_api import create_reporting_router
from sm64_events.server.local_access import mount_local_access


def test_browser_failures_and_preferences_use_the_same_collector(monkeypatch):
    from sm64_events.core import error_reporting as reports
    current = {"configured": True, "enabled": True, "pending": 0}
    seen = []
    monkeypatch.setattr(reports, "status", lambda: dict(current))
    monkeypatch.setattr(reports, "set_enabled", lambda value: current.update(enabled=value))
    monkeypatch.setattr(reports, "capture_problem", lambda message, **kw: seen.append((message, kw)))
    app = FastAPI()
    app.include_router(create_reporting_router())
    client = TestClient(app)
    assert client.get("/api/diagnostics/reporting").json() == current
    response = client.post("/api/diagnostics/browser-error", json={
        "message": "render failed", "stack": "app.js:12", "source": "uncaught"})
    assert response.status_code == 202
    assert seen[0][1]["context"]["component"] == "browser"
    assert client.put("/api/diagnostics/reporting", json={"enabled": False}).json()["enabled"] is False
    assert client.put("/api/diagnostics/reporting", json={"enabled": "false"}).status_code == 422
    assert client.post("/api/diagnostics/browser-error", json={"message": "x" * 1001}).status_code == 422


def test_browser_upload_and_optout_require_local_request_authority():
    app = FastAPI()
    app.include_router(create_reporting_router())
    mount_local_access(app, port=8065, protected=True)
    client = TestClient(app, base_url="http://127.0.0.1:8065")
    assert client.post("/api/diagnostics/browser-error", json={"message": "bad"}).status_code == 403
    assert client.put("/api/diagnostics/reporting", json={"enabled": False}).status_code == 403
