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
                 wrapper_selected=False, layer_alive=False,
                 consented_at=None, problems=[], state=state)
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


class FakeFixups:
    def __init__(self, error=None):
        self.error, self.reason, self.folders = error, None, []

    def status(self):
        return {"version": "9.9", "error": self.error, "reason": self.reason}

    def install(self, plugin_dir):
        from sm64_events.core.fixups import FixupError
        self.folders.append(str(plugin_dir))
        if self.error:
            raise FixupError(self.error)


@pytest.fixture
def layer():
    layer = FakeCaptureLayer()
    layer.fixups = FakeFixups()   # main.py hangs the real one on the layer the same way
    return layer


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


def _installed(running=False):
    return _status(state="needs_restart", pj64_dir="C:/Games/PJ64", plugin_dir="C:/Games/PJ64/Plugin",
                   pj64_running=running, wrapper_present=True, wrapper_current=True, wrapper_selected=True)


def test_install_also_adds_the_fixups_and_their_failure_never_fails_it(client, layer):
    layer.fixups.error = "We could not download the PJ64 Fixups."
    layer.install = lambda consent: _installed()
    response = client.post("/api/setup/capture-layer", json={"consent": True})
    assert response.status_code == 200 and len(layer.fixups.folders) == 1
    assert response.json()["emu"]["fixups"]["error"] == "We could not download the PJ64 Fixups."


def test_the_fixups_route_waits_for_a_closed_project64_and_reports_a_failure(client, layer):
    layer._status = _installed(running=True)
    refused = client.post("/api/setup/fixups")
    assert refused.status_code == 409 and "Close Project64" in refused.json()["detail"]
    assert layer.fixups.folders == []
    layer._status = _installed()
    assert client.post("/api/setup/fixups").status_code == 200 and len(layer.fixups.folders) == 1
    layer.fixups.error = "Windows blocked the change."
    failed = client.post("/api/setup/fixups")
    assert failed.status_code == 409 and failed.json()["detail"] == "Windows blocked the change."


def test_a_protected_folder_gets_the_fixups_through_installs_permission_prompt(client, layer):
    """Windows refuses the plain copy, so the route runs Install: its one
    administrator prompt covers the plugin and the fixups."""
    from sm64_events.core.fixups import FixupError
    layer._status = _installed()
    calls = []

    def install(plugin_dir):
        calls.append("fixups")
        if "install" not in calls:
            layer.fixups.error, layer.fixups.reason = "Windows blocked the change.", "access_denied"
            raise FixupError("Windows blocked the change.", reason="access_denied")
        layer.fixups.error = layer.fixups.reason = None

    layer.fixups.install = install
    layer.fixups.status = lambda: {"version": "9.9", "error": layer.fixups.error, "reason": layer.fixups.reason}
    layer.install = lambda consent: calls.append("install") or _installed()
    assert client.post("/api/setup/fixups").status_code == 200
    assert calls == ["fixups", "install", "fixups"]


def test_accepting_the_warning_records_only_gate_items(client):
    payload = client.post("/api/setup/baseline-risk",
                          json={"items": ["rsp_plugin", "audio_plugin", "emulator_build"]}).json()
    risk = payload["onboarding"]["baseline_risk"]
    assert risk["items"] == ["emulator_build", "rsp_plugin"] and risk["accepted_at"]
    assert client.get("/api/setup").json()["onboarding"]["baseline_risk"] == risk


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
