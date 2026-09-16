"""Real setup classes over injected control/recorder facts, never live state."""
from dataclasses import replace
import pytest

from sm64_events.core import setup_gpu as G
from sm64_events.core.capturelayer import ACTIVE, RENDERER_DLL, CaptureLayer, WRAPPER_DLL, WRAPPER_INI
from sm64_events.core.onboarding import readiness
from sm64_events.replay import capturecontrol as C
from test_capturelayer import FakeProcesses, FakeRegistry, GRAPHICS_DLL_VALUE, REGISTRY_DLL_SUBKEY
from test_onboarding import installed, runtime


def fixture():
    now = [0.0]
    control = [C.CaptureStatus(123, 7, C.ACTIVE, 0, 91, 10, C.CAP_GPU,
                               True, 8, 9, "GPU fixture")]
    receipt = dict(producer_pid=123, producer_birth=8 | (9 << 32),
                   control_generation=7, token=91, source_epoch=12, delivered=10)
    recorder = dict(recording=True, frame_source="plugin", idle=False,
                    frame_source_health=dict(kind="gpu", capture_receipt=receipt))
    probe = G.GpuSetupProbe(lambda: recorder, lambda: control[0], clock=lambda: now[0])
    return probe, now, control, recorder, receipt


def advance(now, control, receipt):
    now[0] += 1
    control[0] = replace(control[0], ack_heartbeat=control[0].ack_heartbeat + 1)
    receipt["delivered"] += 1


def test_active_needs_two_moving_counters_and_expires():
    probe, now, control, _, receipt = fixture()
    assert not probe().alive and not probe().pictures
    advance(now, control, receipt)
    observed = probe()
    assert observed.alive and observed.pictures
    assert probe() == observed
    now[0] = 3.999
    assert probe().pictures
    now[0] = 4
    assert not probe().alive and not probe().pictures


def test_retained_encoder_heartbeats_cannot_keep_picture_check_alive():
    probe, now, control, recorder, receipt = fixture()
    probe()
    advance(now, control, receipt)
    assert probe().pictures
    now[0] += 3
    control[0] = replace(control[0], ack_heartbeat=100)
    recorder["frame_source_health"]["video_packets"] = 10000
    assert probe().alive and not probe().pictures


@pytest.mark.parametrize("change", ["pid", "birth", "generation", "token", "epoch", "reset"])
def test_new_identity_or_reset_cannot_inherit_recent_receipt(change):
    probe, now, control, _, receipt = fixture()
    probe()
    advance(now, control, receipt)
    assert probe().pictures
    if change == "epoch":
        receipt["source_epoch"] += 1
    elif change == "reset":
        receipt["delivered"] = 0
    else:
        field = dict(pid="producer_pid", birth="producer_created_lo",
                     generation="generation", token="ack_token")[change]
        control[0] = replace(control[0], **{field: getattr(control[0], field) + 1})
    assert not probe().pictures


def test_first_open_while_idle_uses_receipt_without_native_poll_timer():
    probe, now, control, recorder, _ = fixture()
    recorder["idle"] = True
    control[0] = replace(control[0], state=C.PASSIVE)
    assert probe().pictures
    now[0] = 600
    assert probe().alive and probe().pictures
    recorder["idle"] = False
    assert not probe().pictures


@pytest.mark.parametrize("fault", ["stopped", "desktop", "empty", "missing", "token",
                                  "closed", "reason", "rom", "capability", "absent"])
def test_idle_rejects_missing_or_wrong_native_and_receipt_evidence(fault):
    probe, _, control, recorder, receipt = fixture()
    recorder["idle"] = True
    control[0] = replace(control[0], state=C.PASSIVE)
    assert probe().pictures
    if fault == "stopped":
        recorder["recording"] = False
    elif fault == "desktop":
        recorder["frame_source"] = "desktop"
    elif fault == "empty":
        receipt["delivered"] = 0
    elif fault == "missing":
        recorder["frame_source_health"]["capture_receipt"] = None
    elif fault == "absent":
        control[0] = None
    else:
        field, value = dict(token=("ack_token", 92), closed=("state", C.CLOSED),
                            reason=("reason", C.LEASE_EXPIRED), rom=("rom_open", False),
                            capability=("capabilities", C.CAP_PASSIVE))[fault]
        control[0] = replace(control[0], **{field: value})
    assert not probe().pictures


def test_readonly_discovery_never_requests_lease(monkeypatch):
    calls = []
    class Control:
        def __enter__(self):
            calls.append("open")
            return self
        def status(self):
            calls.append("status")
            return "result"
        def __exit__(self, *_):
            calls.append("close")
        def acquire(self, **_):
            pytest.fail("setup requested capture")
    monkeypatch.setattr(G, "CaptureControl", Control)
    assert G.read_gpu_control() == "result"
    assert calls == ["open", "status", "close"]


def test_missing_control_clears_window_and_legacy_remains_legacy():
    probe, now, control, recorder, receipt = fixture()
    probe()
    advance(now, control, receipt)
    assert probe().pictures
    good = control[0]
    control[0] = None
    assert not probe().pictures
    control[0] = good
    assert not probe().pictures
    recorder["frame_source_health"] = {"delivered": 10, "plugin_pid": 123}
    control[0] = None
    assert probe() is None


def test_capturelayer_uses_gpu_observation_without_pixel_ring(tmp_path):
    probe, now, control, _, receipt = fixture()
    directory = tmp_path / "PJ64"
    plugin = directory / "Plugin"
    plugin.mkdir(parents=True)
    source = tmp_path / "bundle.dll"
    source.write_bytes(b"fixture wrapper")
    renderer = tmp_path / "renderer.dll"
    renderer.write_bytes(b"fixture renderer")
    (plugin / WRAPPER_DLL).write_bytes(source.read_bytes())
    (plugin / RENDERER_DLL).write_bytes(renderer.read_bytes())
    (plugin / WRAPPER_INI).write_text("wrapped=" + RENDERER_DLL)
    registry = FakeRegistry({(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE): WRAPPER_DLL})
    layer = CaptureLayer(registry, FakeProcesses(str(directory / "Project64.exe")),
                         tmp_path / "settings.json", source, renderer_source=renderer,
                         gpu_observation=probe)
    assert not layer.status().pictures_flowing
    advance(now, control, receipt)
    status = layer.status()
    assert status.state == ACTIVE and status.installation_verified
    assert status.plugin_pid == 123 and status.pictures_flowing
    assert status.pictures_via == "gpu"
    assert "gpu_observation" not in status.as_dict()


def test_gpu_runtime_requires_game_inputs_and_survives_only_intentional_idle():
    gpu, now, control, gpu_recorder, receipt = fixture()
    observe, runtime_now, _, inputs, recorder, _, poller = runtime()
    recorder.clear()
    recorder.update(gpu_recorder)
    def layer():
        return replace(installed(), gpu_observation=gpu())
    assert not readiness(layer(), observe(layer()))["ready"]
    advance(now, control, receipt)
    runtime_now[0] = now[0]
    inputs["frames"] += 1
    poller.latest.global_timer += 1
    assert readiness(layer(), observe(layer()))["ready"]
    gpu_recorder["idle"] = recorder["idle"] = True
    control[0] = replace(control[0], state=C.PASSIVE)
    runtime_now[0] = now[0] = 600
    inputs["frames"] += 1
    poller.latest.global_timer += 1
    assert readiness(layer(), observe(layer()))["ready"]
    runtime_now[0] = now[0] = 604
    assert not readiness(layer(), observe(layer()))["ready"]


def test_runtime_rejects_receipt_replaced_after_layer_read():
    gpu, _, _, gpu_recorder, receipt = fixture()
    observe, _, _, _, recorder, _, _ = runtime()
    gpu_recorder["idle"] = True
    recorder.update(gpu_recorder)
    observation = gpu()
    receipt["source_epoch"] += 1
    result = observe(replace(installed(), gpu_observation=observation))
    assert not result["checks"]["plugin"] and not result["checks"]["pictures"]


def test_epoch_change_invalidates_runtime_cache_immediately():
    gpu, gpu_now, _, gpu_recorder, receipt = fixture()
    gpu_recorder["idle"] = True
    observe, now, _, inputs, recorder, _, poller = runtime()
    recorder.update(gpu_recorder)
    observe(replace(installed(), gpu_observation=gpu()))
    now[0] = gpu_now[0] = 1
    inputs["frames"] += 1
    poller.latest.global_timer += 1
    result = observe(replace(installed(), gpu_observation=gpu()))
    assert result["checks"]["pictures"]
    receipt["delivered"] = 0
    receipt["source_epoch"] += 1
    assert not observe(replace(installed(), gpu_observation=gpu()))["checks"]["pictures"]


def test_active_picture_movement_cannot_hide_frozen_control_ack():
    probe, now, control, _, receipt = fixture()
    probe()
    advance(now, control, receipt)
    assert probe().pictures
    now[0] += 3
    receipt["delivered"] += 1
    assert not probe().alive and not probe().pictures


def test_gpu_passive_jp_cannot_bypass_absent_game_progress():
    gpu, _, _, gpu_recorder, _ = fixture()
    gpu_recorder["idle"] = True
    observe, _, _, _, recorder, memory, _ = runtime()
    recorder.update(gpu_recorder)
    jp = bytearray(memory.raw)
    jp[0x3E] = ord("J")
    memory.raw = bytes(jp)
    layer = replace(installed(), gpu_observation=gpu())
    result = readiness(layer, observe(layer))
    assert result["limited"] and not result["ready"]
