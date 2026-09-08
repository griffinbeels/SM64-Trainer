"""Readiness must fail on stale, missing, and mismatched evidence."""
from dataclasses import replace
from types import SimpleNamespace
import os
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

from sm64_events.core.onboarding import CounterWindow, SetupRecord, identify_rom, readiness
from sm64_events.core.setup_runtime import SetupRuntime
from sm64_events.server.setup_api import create_setup_router
from fastapi import FastAPI
from test_setup_api import FakeCaptureLayer, _status
from test_version_probe import LIVE_USAMUNE_BE, _header, _word_swapped


def installed(**kwargs):
    return _status("active", consented_at="yesterday", wrapper_present=True,
                   wrapper_current=True, wrapper_selected=True, pj64_dir="C:/PJ64",
                   pj64_running=True, layer_alive=True, plugin_pid=123,
                   wrapped_name="GLideN64.dll", **kwargs)


def observation(raw=LIVE_USAMUNE_BE):
    return {"target": {"state": "ready", "pid": 123}, "rom": identify_rom(raw),
            "checks": dict(plugin=True, pictures=True, inputs=True, game=True)}


def test_detects_the_loaded_header_in_both_byte_orders():
    assert identify_rom(LIVE_USAMUNE_BE)["state"] == "supported"
    assert identify_rom(_word_swapped(LIVE_USAMUNE_BE))["region"] == "us"
    assert identify_rom(_header(b"E"))["state"] == "unsupported"
    assert identify_rom(None)["region"] is None
    assert identify_rom(b"broken")["state"] == "missing"


@pytest.mark.skipif(os.name != "nt", reason="Windows executable resource API")
def test_executable_version_reads_a_real_resource_without_launching_it():
    from sm64_events.core.capturelayer_win import executable_version
    version = executable_version(Path(sys.executable))
    assert version is not None
    assert version.split(".")[:2] == [str(sys.version_info.major), str(sys.version_info.minor)]


def test_jp_is_detected_without_claiming_tracking():
    jp = bytearray(LIVE_USAMUNE_BE)
    jp[0x3E] = ord("J")
    observed = observation(bytes(jp))
    observed["checks"].update(inputs=False, game=False)
    verdict = readiness(installed(pictures_flowing=True), observed)
    assert verdict["ready"] and verdict["limited"]
    assert "later patch" in observed["rom"]["warning"]
    observed["checks"]["pictures"] = False
    assert not readiness(installed(), observed)["ready"]


@pytest.mark.parametrize("missing", ["plugin", "pictures", "inputs", "game"])
def test_us_requires_every_live_check(missing):
    observed = observation()
    observed["checks"][missing] = False
    assert readiness(installed(), observed)["step"] == "verify"


def test_just_opening_project64_never_proves_a_rom_or_completion():
    observed = observation(None)
    assert readiness(installed(), observed)["step"] == "rom"
    assert readiness(replace(installed(), pj64_running=False), observation())["step"] == "reopen"


def test_fresh_install_order_and_regression():
    layer = replace(installed(), consented_at=None, wrapper_present=False)
    assert readiness(layer, observation())["step"] == "close"
    assert readiness(replace(layer, pj64_running=False), observation())["step"] == "install"
    assert readiness(replace(installed(), wrapper_current=False), observation())["step"] == "close"


def test_matching_existing_installation_does_not_require_local_consent():
    layer = replace(installed(), consented_at=None)
    assert readiness(layer, observation())["ready"]
    assert readiness(layer, observation(None))["step"] == "rom"
    assert not readiness(replace(layer, wrapped_name=None), observation())["installed"]


def test_movement_is_not_consumed_by_another_client_and_expires():
    now = [0.0]
    window = CounterWindow(lambda: now[0])
    assert not window.observe(1, frames=100)["frames"]
    now[0] = 1
    assert window.observe(1, frames=101)["frames"]
    assert window.observe(1, frames=101)["frames"]
    now[0] = 4
    assert not window.observe(1, frames=101)["frames"]
    assert not window.observe(2, frames=500)["frames"]
    assert window.observe(2, frames=501)["frames"]
    assert not window.observe(2, frames=0)["frames"]


def test_completion_record_is_durable_and_bad_data_recovers(tmp_path):
    record = SetupRecord(tmp_path / "onboarding.json")
    record.path.write_text("broken")
    assert record.read() == {}
    record.complete("emu", True)
    saved = SetupRecord(record.path).read()
    assert saved["completed_at"] and saved["limited"]
    record.write(completed_at=None)
    assert record.read()["completed_at"] is None


class Memory:
    def __init__(self, raw):
        self.raw, self.closed = raw, False

    def attach(self):
        return True

    def rom_header(self):
        return self.raw

    def detach(self):
        self.closed = True


def runtime():
    now = [0.0]
    target = {"state": "ready", "pid": 123}
    counter = {"frames": 100}
    recorder = {"recording": True, "frame_source": "plugin", "frame_source_health": {"delivered": 100}}
    memory = Memory(LIVE_USAMUNE_BE)
    poller = SimpleNamespace(input_sampler=SimpleNamespace(health=lambda: counter),
                             latest=SimpleNamespace(global_timer=100), paused=False, hold_reason=None)
    processes = SimpleNamespace(setup_target=lambda: target,
                                check_folder=lambda folder: {"state": "ready", "pid": None})
    runtime = SetupRuntime(processes, lambda: memory, poller,
                           SimpleNamespace(recorder=SimpleNamespace(status=lambda: recorder)), lambda: now[0])
    return runtime, now, target, counter, recorder, memory, poller


def test_runtime_requires_fresh_counters_from_same_process_and_recorder():
    probe, now, target, inputs, recorder, memory, poller = runtime()
    assert not readiness(installed(), probe(installed()))["ready"]
    now[0] = 1
    inputs["frames"] += 1
    recorder["frame_source_health"]["delivered"] += 1
    poller.latest.global_timer += 1
    assert readiness(installed(), probe(installed()))["ready"]
    assert memory.closed
    assert readiness(installed(), probe(installed()))["ready"]  # second tab
    now[0] = 2
    recorder["frame_source"] = "desktop"
    assert not readiness(installed(), probe(installed()))["ready"]
    now[0] = 3
    recorder["frame_source"] = "plugin"
    target["pid"] = 124
    assert not probe(installed())["checks"]["plugin"]
    now[0] = 10
    assert not any(probe(installed())["checks"].values())


def test_closed_emulator_clears_rom_identity_even_if_poller_has_old_data():
    probe, now, target, *_ = runtime()
    assert probe(installed())["rom"]["region"] == "us"
    now[0] = 0.1  # source changes invalidate the cache before its normal timeout
    target.update(state="missing", pid=None)
    assert probe(replace(installed(), pj64_running=False))["rom"]["state"] == "missing"


def test_completion_endpoint_rechecks_and_preserves_a_failed_attempt(tmp_path):
    observed = observation()
    layer = FakeCaptureLayer(installed())
    app = FastAPI()
    app.include_router(create_setup_router(layer, tmp_path / "mode.json", lambda status: observed))
    client = TestClient(app)
    observed["checks"]["pictures"] = False
    assert client.post("/api/setup/complete", json={"platform": "emu"}).status_code == 409
    assert not (tmp_path / "onboarding.json").exists()
    observed["checks"]["pictures"] = True
    result = client.post("/api/setup/complete", json={"platform": "emu"})
    assert result.status_code == 200
    assert result.json()["onboarding"]["completed_at"]
    assert not result.json()["onboarding"]["limited"]


def test_console_can_finish_without_emulator(tmp_path):
    app = FastAPI()
    app.include_router(create_setup_router(FakeCaptureLayer(), tmp_path / "mode.json"))
    result = TestClient(app).post("/api/setup/complete", json={"platform": "n64"})
    assert result.status_code == 200
    assert result.json()["platform"] == "n64"
    assert result.json()["onboarding"]["limited"]
