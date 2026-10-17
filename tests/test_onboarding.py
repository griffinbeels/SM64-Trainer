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
                   wrapped_name="GLideN64_SM64Trainer.dll", renderer_present=True,
                   renderer_current=True, **kwargs)


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


def _jp_header():
    jp = bytearray(LIVE_USAMUNE_BE)
    jp[0x20:0x34] = b"SM64 USAMUNE v1.93j ".ljust(20)
    jp[0x3E] = ord("J")
    return bytes(jp)


def test_verified_jp_is_supported_like_us():
    observed = observation(_jp_header())
    assert observed["rom"]["state"] == "supported" and observed["rom"]["warning"] is None
    observed["checks"]["game"] = False
    assert readiness(installed(), observed)["step"] == "verify"


def test_an_unverified_jp_layout_is_detected_without_claiming_tracking(monkeypatch):
    import sm64_events.core.onboarding as onboarding
    monkeypatch.setattr(onboarding, "_layout_ready", lambda version: False)
    observed = observation(_jp_header())
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
    """A reader of one Project64: which pids it was attached to, and whether
    the process it holds is still running."""
    def __init__(self, raw):
        self.raw, self.closed, self.attaches, self.running = raw, False, [], True

    def attach(self, pid=None):
        self.attaches.append(pid)
        self.closed = False
        return self.raw is not False

    def alive(self):
        return not self.closed and self.running

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
    assert memory.attaches == [123] and not memory.closed  # kept for the process
    assert readiness(installed(), probe(installed()))["ready"]  # second tab
    now[0] = 2
    recorder["frame_source"] = "desktop"
    assert readiness(installed(), probe(installed()))["ready"]  # a gap, held
    now[0] = 4.1
    assert not readiness(installed(), probe(installed()))["ready"]  # held no longer
    now[0] = 5
    recorder["frame_source"] = "plugin"
    target["pid"] = 124
    assert not probe(installed())["checks"]["plugin"]
    assert memory.attaches == [123, 124]
    now[0] = 12
    assert not any(probe(installed())["checks"].values())


def test_a_brief_gap_never_moves_setup_back_but_a_new_game_starts_over():
    """His report, 2026-10-17: the wizard went "Setup checked", then back to
    "Checking your setup", then forward again. The recorder resuming from
    idle restarts its picture count, and one poll read "no pictures" (GET
    /api/setup: `pictures` false for 0.6 s at each turn). A proven check now
    holds through a gap shorter than SteadyChecks.hold_s; a stall longer than
    that, or another Project64 or ROM, still reads as not set up."""
    probe, now, target, inputs, recorder, memory, poller = runtime()
    probe(installed())

    def tick(at, pictures=1):
        now[0] = at
        inputs["frames"] += 30
        poller.latest.global_timer += 30
        recorder["frame_source_health"]["delivered"] += pictures
        return readiness(installed(), probe(installed()))

    assert tick(1)["ready"]
    recorder["frame_source_health"]["delivered"] = 0    # the capture starts a new count
    assert tick(2, pictures=0)["ready"]
    assert tick(3)["ready"]                             # the new count moves again
    # A real stall: pictures stop for good. Movement stays fresh for the
    # counter window's 3 s and the proven check for 3 s more.
    for at in (4, 5, 6, 7):
        assert tick(at, pictures=0)["ready"]
    assert not tick(8, pictures=0)["ready"]
    assert tick(9)["ready"]
    memory.raw = LIVE_USAMUNE_BE.replace(b"USAMUNE", b"USAMUNX")   # another ROM
    assert not tick(10)["ready"]


def test_one_unreadable_header_keeps_the_game_it_just_read():
    probe, now, target, *_, memory, _ = runtime()
    assert probe(installed())["rom"]["region"] == "us"

    def failing():
        raise OSError("partial copy")
    memory.rom_header = failing
    now[0] = 1
    assert probe(installed())["rom"]["region"] == "us"
    now[0] = 4.5
    assert probe(installed())["rom"]["state"] == "missing"


def test_closed_emulator_clears_rom_identity_even_if_poller_has_old_data():
    probe, now, target, *_ = runtime()
    assert probe(installed())["rom"]["region"] == "us"
    now[0] = 0.1  # source changes invalidate the cache before its normal timeout
    target.update(state="missing", pid=None)
    assert probe(replace(installed(), pj64_running=False))["rom"]["state"] == "missing"


def test_one_reader_serves_every_observation_of_a_process():
    """Live 2026-09-28: the setup screen polls once a second and each
    observation attached a fresh reader -- a process-list snapshot, a
    debug-privileged open and a walk of Project64's memory -- 38 times in a
    minute of play. One reader per process now, attached by pid."""
    probe, now, target, *_, memory, _ = runtime()
    for second in range(6):
        now[0] = second
        assert probe(installed())["rom"]["region"] == "us"
    assert memory.attaches == [123] and not memory.closed

    memory.running = False                  # that Project64 exited; its pid came back
    now[0] = 6
    probe(installed())
    assert memory.attaches == [123, 123]

    target.update(state="missing", pid=None)
    now[0] = 7
    probe(replace(installed(), pj64_running=False))
    assert memory.closed, "no Project64: no reader left open"


def test_a_project64_with_no_game_booted_is_walked_at_most_every_retry():
    """Without RDRAM there is nothing to attach to, and the attempt walks the
    process's memory: retried every ATTACH_RETRY_S, not every observation."""
    probe, now, target, *_, memory, _ = runtime()
    memory.raw = False                      # attach finds no RDRAM
    for tenth in range(15):
        now[0] = tenth * 0.1
        probe(replace(installed(), plugin_pid=tenth))   # a fresh observation each time
    assert memory.attaches == [123]
    now[0] = SetupRuntime.ATTACH_RETRY_S
    probe(replace(installed(), plugin_pid=99))
    assert memory.attaches == [123, 123]


def test_idle_without_a_gpu_observation_needs_fresh_pictures():
    """Only the GPU observation carries an idle picture receipt
    (tests/test_setup_gpu.py); without one, a stalled delivery count fails
    even when the source's health names this very process."""
    probe, now, _, inputs, recorder, _, poller = runtime()
    recorder["idle"] = True
    recorder["frame_source_health"]["plugin_pid"] = 123
    probe(installed())
    now[0] = 1
    inputs["frames"] += 30
    poller.latest.global_timer += 30
    assert not probe(installed())["checks"]["pictures"]


def test_an_adapter_that_cannot_record_finishes_setup_on_desktop_pictures():
    """AMD, Intel, a software adapter or a too-old NVIDIA driver: the recorder
    keeps the desktop grab for good (replay/sourcefactory.py), holds no GPU
    lease, and the control page's heartbeat never moves. Setup takes the
    desktop's written pictures and the moving game as proof instead; before
    2026-09-27 such a machine could never finish setup."""
    from sm64_events.core.setup_gpu import GpuSetupEvidence

    probe, now, _, inputs, recorder, _, poller = runtime()
    recorder.update(frame_source="desktop", frame_source_health=None, pictures_fed=100,
                    gpu_unsupported="AMD Radeon(TM) Graphics has no NVIDIA video encoder")
    layer = replace(installed(gpu_observation=GpuSetupEvidence(123, None, None, False, False, False)),
                    layer_alive=False)
    probe(layer)
    now[0] = 1
    inputs["frames"] += 1
    poller.latest.global_timer += 1
    assert not probe(layer)["checks"]["pictures"]  # no picture written yet
    now[0] = 2
    recorder["pictures_fed"] += 1
    inputs["frames"] += 1
    poller.latest.global_timer += 1
    assert readiness(layer, probe(layer))["ready"]
    now[0] = 5.1                        # past SteadyChecks' hold of the proof above
    recorder["gpu_unsupported"] = None  # a capable adapter still needs GPU pictures
    recorder["pictures_fed"] += 1       # ... however many desktop pictures move
    inputs["frames"] += 1
    poller.latest.global_timer += 1
    assert not probe(layer)["checks"]["pictures"]


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
