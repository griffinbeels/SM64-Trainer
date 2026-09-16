"""Drive the shipping GPU setup route over injected facts, never live PJ64."""
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent / "tools/ui_fixture.py").is_file())
sys.path.insert(0, str(ROOT / "tools"))
from find_uilab import find_uilab

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver
import ui_fixture
from ui_fixture import serve_ui
from uilab_project import _SETUP_SETUP
from sm64_events.core.capturelayer import CaptureLayer, WRAPPER_DLL, WRAPPER_INI
from sm64_events.core.setup_gpu import GpuSetupProbe
from sm64_events.replay import capturecontrol as C
from test_onboarding import runtime
from test_ui_setup_modal import body, wait_page, wait_step


class GpuFacts:
    """Controlled server-clock progression, with immutable native status values."""
    def __init__(self, directory):
        (self.runtime, self.now, _, self.inputs, self.recorder,
         self.memory, self.poller) = runtime()
        self.phase = "baseline"
        self.control = C.CaptureStatus(123, 7, C.ACTIVE, 0, 91, 10, C.CAP_GPU,
                                       True, 8, 9, "offline GPU renderer")
        self.receipt = dict(producer_pid=123, producer_birth=8 | (9 << 32),
                            control_generation=7, token=91, source_epoch=12, delivered=10)
        self.recorder.update(idle=False, frame_source_health=dict(
            kind="gpu", capture_receipt=self.receipt))
        plugin = directory / "PJ64/Plugin"
        plugin.mkdir(parents=True)
        bundle = directory / WRAPPER_DLL
        bundle.write_bytes(b"matching offline GPU wrapper")
        (plugin / WRAPPER_DLL).write_bytes(bundle.read_bytes())
        (plugin / WRAPPER_INI).write_text("wrapped=renderer-source-v2.dll")
        registry = SimpleNamespace(get=lambda key, name: WRAPPER_DLL if name == "Graphics Dll" else None)
        processes = SimpleNamespace(pj64_image_path=lambda: str(plugin.parent / "Project64.exe"))
        gpu = GpuSetupProbe(lambda: self.recorder, self.read_control, clock=lambda: self.now[0])
        self.layer = CaptureLayer(registry, processes, directory / "capture.json", bundle,
                                  gpu_observation=gpu)

    @staticmethod
    def forbid_stream():
        pytest.fail("GPU setup attempted a legacy FrameStreamHeader read")

    def read_control(self):
        # Each external poll observes four more seconds of the chosen fixture
        # phase. Idle checks therefore cannot borrow active picture freshness.
        self.now[0] += 4
        if self.phase != "baseline":
            self.inputs["frames"] += 120
            if self.phase != "stalled_game":
                self.poller.latest.global_timer += 120
        if self.phase == "active":
            self.control = replace(self.control, ack_heartbeat=self.control.ack_heartbeat + 1)
            self.receipt["delivered"] += 120
        return self.control

    def idle(self):
        self.phase = "idle"
        self.recorder["idle"] = True
        self.control = replace(self.control, state=C.PASSIVE)


def capture(page, artifacts, name, records):
    response = page.evaluate("fetch('/api/setup').then(async r => ({status:r.status, body:await r.json()}))")
    assert response["status"] == 200
    record = dict(api=response, wizard=body(page), problems=page.problems(),
                  viewport=page.evaluate("({width:innerWidth,height:innerHeight})"))
    records[name] = record
    (artifacts / (name + ".png")).write_bytes(page.screenshot())
    assert not record["problems"]
    assert "gpu_observation" not in response["body"]["emu"]
    return response["body"]["emu"]


def install_fixture(monkeypatch, facts):
    monkeypatch.setattr(ui_fixture, "_FixtureCaptureLayer", lambda *a, **kw: facts.layer)
    fixture_routes = ui_fixture._fixture_replay_routes
    def replay_routes(app, database):
        fixture_routes(app, database)
        # serve_ui has no recorder/saved clips. Match replay_api.available's
        # real empty response for the unrelated Rank control interaction.
        @app.get("/api/replay/available")
        def available():
            return {"available": []}
    monkeypatch.setattr(ui_fixture, "_fixture_replay_routes", replay_routes)


def assert_fixture_closed(url, artifacts):
    # Only the fixture's own listener is checked; no live server is queried.
    address = urlsplit(url)
    with socket.socket() as probe:
        probe.settimeout(.5)
        assert probe.connect_ex((address.hostname, address.port)) != 0
    (artifacts / "cleanup.json").write_text(json.dumps(dict(fixture_listener_closed=True)) + "\n")


def open_setup(page, url):
    page.goto(url + "/ui/index.html")
    # Unrelated control interaction establishes that the real app can
    # transition before a setup failure could implicate the fixture.
    page.wait_for('button.nav-item[title="Rank"]')
    page.click('button.nav-item[title="Rank"]')
    page.wait_for('.rank-page')
    assert page.count('button.nav-item[title="Rank"][aria-current="page"]') == 1
    page.evaluate(_SETUP_SETUP)
    wait_page(page, "install")
    wait_step(page, "verify")


def test_gpu_setup_active_idle_and_invalidated_evidence(tmp_path, monkeypatch):
    """One browser, real API/wizard, two viewport sizes and both failure causes."""
    artifacts = Path(os.environ.get("SM64_SETUP_GPU_ARTIFACTS", str(tmp_path / "render")))
    artifacts.mkdir(parents=True, exist_ok=True)
    facts, records = GpuFacts(tmp_path), {}
    install_fixture(monkeypatch, facts)
    with serve_ui(setup_observer=facts.runtime) as url, get_driver().launch(
            headless=True, viewport=(1440, 900)) as page:
        try:
            open_setup(page, url)
            baseline = capture(page, artifacts, "baseline-1440x900", records)
            assert baseline["installation_verified"] and not baseline["verification"]["ready"]

            facts.phase = "active"
            wait_step(page, "ready")
            active = capture(page, artifacts, "active-1440x900", records)
            assert all(active["checks"].values()) and active["pictures_via"] == "gpu"
            assert active["plugin_pid"] == 123 and active["verification"]["ready"]
            assert "Setup checked" in body(page)

            facts.idle()
            receipt, heartbeat = dict(facts.receipt), facts.control.ack_heartbeat
            # More than a freshness window elapses while native ack and picture
            # counts stay fixed; only actual fixture game/input counters move.
            page.wait_ms(4500)
            page.set_viewport(850, 600)
            page.wait_ms(400)
            wait_step(page, "ready")
            idle = capture(page, artifacts, "idle-850x600", records)
            assert idle["verification"]["ready"] and all(idle["checks"].values())
            assert facts.receipt == receipt and facts.control.ack_heartbeat == heartbeat
            assert page.count('.setup-arrow-ready[aria-label="Forward"]') == 1

            facts.phase = "stalled_game"
            wait_step(page, "verify")
            stalled = capture(page, artifacts, "stalled-game-850x600", records)
            assert not stalled["verification"]["ready"] and not stalled["checks"]["game"]
            assert page.count('.setup-arrow-ready') == 0

            facts.phase = "idle"
            wait_step(page, "ready")
            facts.receipt.update(source_epoch=13, delivered=0)
            wait_step(page, "verify")
            replaced = capture(page, artifacts, "replaced-epoch-850x600", records)
            assert not replaced["verification"]["ready"] and not replaced["checks"]["pictures"]
            assert "Checking your setup" in body(page)
            assert page.count('.setup-completion') == 0
            assert page.problems() == []
        finally:
            (artifacts / "observations.json").write_text(json.dumps(records, indent=2) + "\n")
    assert_fixture_closed(url, artifacts)
