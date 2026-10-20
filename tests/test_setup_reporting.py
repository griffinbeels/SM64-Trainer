"""The public installer endpoint only exposes final outcomes, with their cause."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.core import error_reporting
from sm64_events.server.setup_api import create_setup_router


def client_for(tmp_path, error):
    def install(consent):
        raise error
    layer = SimpleNamespace(install=install)
    app = FastAPI()
    app.include_router(create_setup_router(layer, mode_path=tmp_path / "mode.json"))
    return TestClient(app)


def test_final_failure_reports_original_windows_cause_and_shows_it(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(error_reporting, "capture_exception", lambda error, **kw: captured.append((error, kw)))
    error = PermissionError(13, "Access is denied", "C:/Games/Project64/Plugin/renderer.dll")
    error.winerror = 5
    response = client_for(tmp_path, error).post("/api/setup/capture-layer", json={"consent": True})
    assert response.status_code == 409
    assert "Access is denied" in response.json()["detail"]
    assert "renderer.dll" in response.json()["detail"]
    assert captured[0][0] is error
    assert captured[0][1]["context"]["winerror"] == 5


def test_uac_cancellation_is_actionable_and_not_an_error_report(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(error_reporting, "capture_exception", lambda *a, **kw: captured.append(a))
    error = OSError("User cancelled")
    error.winerror = 1223
    response = client_for(tmp_path, error).post("/api/setup/capture-layer", json={"consent": True})
    assert response.status_code == 409
    assert "cancelled" in response.json()["detail"]
    assert not captured
