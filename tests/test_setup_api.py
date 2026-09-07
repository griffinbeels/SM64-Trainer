"""The setup screen's routes are a skin over `CaptureLayer` + `core/modes.py`:
every route asks one of those two and maps a `LayerRefused` to 409, nothing
more."""
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from sm64_events.core.capturelayer import LayerRefused, LayerStatus
from sm64_events.core.modes import GameVersion
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller


class OfflineMemory:
    attached = False

    def attach(self):
        return False

    def detach(self):
        pass


def _status(state="not_installed", **overrides) -> LayerStatus:
    fields = dict(pj64_dir=None, pj64_running=False, registry_graphics_dll=None,
                 wrapper_present=False, wrapper_current=False,
                 wrapper_selected=False, wrapped_name=None, layer_alive=False,
                 gl_context=False, consented_at=None, problems=[], state=state)
    fields.update(overrides)
    return LayerStatus(**fields)


@dataclass
class FakeCaptureLayer:
    """The three methods setup_api.py calls, with the same names and shapes
    as core/capturelayer.py::CaptureLayer -- a real one is being built
    alongside this in the same file, so tests here stand in for it."""
    _status: LayerStatus = field(default_factory=_status)
    install_refusal: str | None = None
    uninstall_refusal: str | None = None
    installs: list = field(default_factory=list)

    def status(self) -> LayerStatus:
        return self._status

    def install(self, consent: bool) -> LayerStatus:
        self.installs.append(consent)
        if not consent or self.install_refusal:
            raise LayerRefused(self.install_refusal or "consent is required")
        self._status = _status(state="needs_restart", wrapper_present=True,
                               wrapper_current=True, wrapper_selected=True)
        return self._status

    def uninstall(self) -> LayerStatus:
        if self.uninstall_refusal:
            raise LayerRefused(self.uninstall_refusal)
        self._status = _status()
        return self._status


@pytest.fixture
def layer():
    return FakeCaptureLayer()


@pytest.fixture
def client(tmp_path, layer):
    broadcaster = Broadcaster()
    poller = Poller(OfflineMemory(), [], broadcaster)
    app = create_app(poller, broadcaster, capture_layer=layer,
                     mode_path=tmp_path / "tracker_mode.json")
    return TestClient(app)


def test_get_setup_reports_the_platform_and_the_layer_status(client):
    payload = client.get("/api/setup").json()
    assert payload["platform"] == "emu"
    assert payload["emu"]["state"] == "not_installed"
    assert payload["n64"] == {"available": False}


def test_put_platform_persists_and_echoes_get(client):
    payload = client.put("/api/setup/platform", json={"platform": "n64"}).json()
    assert payload["platform"] == "n64"
    assert client.get("/api/setup").json()["platform"] == "n64"


def test_put_platform_back_to_emu_round_trips(client):
    client.put("/api/setup/platform", json={"platform": "n64"})
    payload = client.put("/api/setup/platform", json={"platform": "emu"}).json()
    assert payload["platform"] == "emu"


def test_put_platform_rejects_an_unknown_value(client):
    response = client.put("/api/setup/platform", json={"platform": "wii"})
    assert response.status_code == 422
    assert "wii" in response.json()["detail"]


def test_put_platform_keeps_the_stored_game_version(client, tmp_path):
    from sm64_events.core.modes import ModeConfig, TrackerMode, save_mode_config
    mode_path = tmp_path / "tracker_mode.json"
    save_mode_config(ModeConfig(mode=TrackerMode.EMU, version=GameVersion.JP),
                     mode_path)
    payload = client.put("/api/setup/platform", json={"platform": "n64"}).json()
    assert payload["platform"] == "n64"
    from sm64_events.core.modes import load_mode_config
    assert load_mode_config(mode_path).version is GameVersion.JP


def test_install_reports_the_new_status(client, layer):
    payload = client.post("/api/setup/capture-layer",
                          json={"consent": True}).json()
    assert payload["emu"]["state"] == "needs_restart"
    assert layer.installs == [True]


def test_install_without_consent_is_refused(client):
    response = client.post("/api/setup/capture-layer", json={"consent": False})
    assert response.status_code == 409
    assert "consent" in response.json()["detail"]


def test_a_refused_install_names_the_reason(client, layer):
    layer.install_refusal = "close Project64 before installing"
    response = client.post("/api/setup/capture-layer", json={"consent": True})
    assert response.status_code == 409
    assert response.json()["detail"] == "close Project64 before installing"


def test_uninstall_reports_the_new_status(client, layer):
    layer.install(True)
    payload = client.delete("/api/setup/capture-layer").json()
    assert payload["emu"]["state"] == "not_installed"


def test_a_refused_uninstall_names_the_reason(client, layer):
    layer.uninstall_refusal = "close Project64 before removing"
    response = client.delete("/api/setup/capture-layer")
    assert response.status_code == 409
    assert response.json()["detail"] == "close Project64 before removing"
