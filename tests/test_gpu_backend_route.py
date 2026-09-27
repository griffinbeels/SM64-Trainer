"""How the recorder reaches a picture: the helper it starts, the camera it keeps.

Four failures from the 2026-09-25 performance audit, each invisible on the
developer's machine:

- The installed app refused to start the encoder helper at all (v1.9.1), so
  it recorded nothing and EMU setup could never see pictures.
- An adapter without a usable NVIDIA encoder (AMD, Intel, a software adapter,
  a driver older than the encoder's API, no codec at all) retried the GPU
  route every 10-60 s forever and recorded nothing either.
- A source run started Python through the venv's launcher, which drops the
  flag that keeps the pointer from showing the busy spinner.
- Stage timings were logged only for failed runs.
"""

from dataclasses import replace
import logging
import os
from functools import partial
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS

import pytest

from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
from sm64_events.ghost.window.controller import parent_map
from sm64_events.memory.layout import US as US_LAYOUT
from sm64_events.replay import gpuadapter, gpusettings, sourcefactory
from sm64_events.replay import gpucapture_session as session_module
from sm64_events.replay.capturecontrol import CAP_GPU, PASSIVE
from sm64_events.replay.gpucapture import GpuBackendUnsupported, GpuCapture, discover
from sm64_events.replay.gpucapture_session import CaptureSession, helper_launch
from sm64_events.replay.gpudemand import DemandSnapshot
from sm64_events.replay.gpuencoder import Result
from sm64_events.replay.gpuprocess import process_controller, process_worker
from sm64_events.replay.gpuprocess.process_controller import Controller
from sm64_events.replay.gpuretry import RetryGate
from sm64_events.replay.gpusettings import GpuSettings
from test_gpu_codec_choice import ONE, Replies, session_for

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def fresh_memo():
    gpusettings.forget_adapter_codecs()
    yield
    gpusettings.forget_adapter_codecs()


def eventually(read, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if value:
            return value
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def finish(controller):
    if not controller.status()["done"]:
        controller.stop("test cleanup")
    eventually(lambda: controller.status()["done"])
    disposal = controller.status()["disposal"]
    assert disposal["empty"] and disposal["handle_closed"] and disposal["active_processes"] == 0


class Demand:
    """A GPU request that is always granted."""

    def __init__(self, *_):
        self.snapshot = DemandSnapshot("active")
        self.identity = NS(nonce=bytes(16), control_generation=1, producer_pid=42,
                           producer_birth=7)

    def start(self):
        pass

    def request_stop(self, *_, **__):
        pass

    def reconcile_lifecycle(self):
        return False

    def close(self, timeout):
        pass


# --- the installed app ------------------------------------------------------

def test_the_installed_app_starts_the_helper_as_itself(monkeypatch):
    """No script and no `-m` exist in a frozen build: the exe re-enters
    through its flag, and the controller appends nonce and bounds."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    command, identity, env = helper_launch()
    assert command == [sys.executable, process_worker.GPU_HELPER_FLAG]
    assert env is None and identity["entry"] == str(Path(sys.executable).resolve())
    spawned = []

    class Job:
        child = resume_evidence = None

        def spawn(self, command, cwd, env=None):
            spawned.append(command)
            raise RuntimeError("recorded, never started")

        def stop(self):
            pass

        def snapshot(self):
            return {"empty": True, "handle_closed": True, "active_processes": 0}

    monkeypatch.setattr(process_controller, "OwnedJob", Job)
    limits = GpuSettings().helper()
    controller = Controller(command=command, identity=identity, env=env, limits=limits)
    eventually(lambda: controller.status()["done"])
    assert spawned == [[sys.executable, process_worker.GPU_HELPER_FLAG, controller.nonce,
                        str(limits.max_json), str(limits.max_packet)]]


def test_the_installed_app_starts_gpu_capture(monkeypatch):
    """v1.9.1 refused here with 'GPU helper executable is not bundled in this
    build', before any session could start."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    sessions = []
    capture = GpuCapture(42, US_LAYOUT, nominal_rate=30, demand_factory=Demand,
                         session_factory=lambda owner, demand: NS(run=lambda: sessions.append(demand)))
    capture.create_sink(NS(picture_feed=True), object(), object(), lambda *a: None)
    stopped = threading.Event()
    capture.start(None, stopped.set)
    assert stopped.wait(5)
    capture.finish()
    assert len(sessions) == 1 and capture.error is None


def test_the_installed_app_hands_its_helper_flag_to_the_helper(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO))
    import gui_entry

    seen = []
    monkeypatch.setattr(process_worker, "main", seen.append)
    monkeypatch.setitem(sys.modules, "sm64_events.desktop.app",
                        NS(main=lambda: pytest.fail("the helper flag started the app")))
    monkeypatch.setattr(sys, "argv", ["SM64Trainer.exe", process_worker.GPU_HELPER_FLAG,
                                      "nonce", "32768", "1048576"])
    gui_entry.main()
    assert seen == [["nonce", "32768", "1048576"]]


# --- a python child is the interpreter itself --------------------------------

STARTUP_PROBE = """\
import ctypes, ctypes.wintypes as w, os
class Info(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR),
                ("title", w.LPWSTR), ("geometry", w.DWORD * 7), ("flags", w.DWORD),
                ("show", w.WORD), ("extra", w.WORD), ("extra_bytes", ctypes.c_void_p),
                ("handles", w.HANDLE * 3)]
info = Info(cb=ctypes.sizeof(Info))
ctypes.windll.kernel32.GetStartupInfoW(ctypes.byref(info))
print(info.flags, os.getppid())
"""


def test_a_python_child_keeps_the_quiet_pointer_and_is_our_own_child(tmp_path, monkeypatch):
    """The venv launcher hands the real interpreter startup flags 0x100: the
    no-busy-pointer flag (0x80) is gone, and the process we own is not the
    one that runs, so its memory hides a generation down."""
    (tmp_path / "startup_flags_probe.py").write_text(STARTUP_PROBE, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    command, env = python_child("startup_flags_probe", "--frozen-only-flag")
    out = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60,
                         **quiet_spawn_kwargs())
    assert out.returncode == 0, out.stderr
    flags, parent = map(int, out.stdout.split())
    assert flags & 0x80, hex(flags)
    assert parent == os.getpid()


def test_the_source_helper_starts_through_the_recorders_launch_and_answers():
    command, identity, env = helper_launch()
    assert command[1:] == ["-m", process_worker.__name__]
    assert identity["entry_sha256"] and "__PYVENV_LAUNCHER__" not in env
    controller = Controller(command=command, identity=identity, env=env,
                            limits=GpuSettings().helper())
    try:
        pid = eventually(lambda: controller.status()["pid"])
        assert parent_map().get(pid) == os.getpid()
        assert type(controller.enqueue({"op": "Poll"})) is int
        reply = eventually(controller.take_result)
        # The real worker, through the real protocol: nothing is open yet.
        assert "Open required first" in reply.metadata["error"]
    finally:
        finish(controller)


# --- an adapter that can never record ------------------------------------------

AMD = gpuadapter.AdapterFacts("AMD Radeon(TM) Graphics", 0x1002, False, "32.0.21045.5002")
NVIDIA = gpuadapter.AdapterFacts("NVIDIA GeForce RTX 5090", 0x10DE, False, "32.0.16.1692")

CODEC_REFUSING_HELPER = """\
import sys
from sm64_events.replay.gpuencoder import EncoderFailure, Result
from sm64_events.replay.gpuprocess import process_worker

class NoSuchCodec:
    def __init__(self, **_config):
        raise EncoderFailure("encoder open failed", result=Result.CODEC)

process_worker.EncoderClient = NoSuchCodec
process_worker.main(sys.argv[1:])
"""


def test_a_refusal_by_codec_leaves_the_helper_answering_for_the_other_codec(tmp_path, monkeypatch):
    """The session asks the SAME helper for H.264 after an AV1 refusal. Until
    2026-09-27 the controller rejected result 16 as a malformed reply and the
    worker exited on it, so no adapter without AV1 could ever record."""
    (tmp_path / "codec_refusing_helper.py").write_text(CODEC_REFUSING_HELPER, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    command, env = python_child("codec_refusing_helper", "--frozen-only-flag")
    controller = Controller(command=command, identity={}, env=env,
                            limits=GpuSettings().helper())
    open_command = {"op": "Open", "dll_path": "C:/fixture/SM64GpuEncoderV1.dll",
                    "adapter_luid": [0, 1], "names": ["a", "b"], "options": {}}
    try:
        for _codec in ("av1", "h264"):
            request = controller.enqueue(open_command)
            assert type(request) is int, request
            reply = eventually(controller.take_result)
            assert reply.metadata["result"] == Result.CODEC
            assert reply.metadata["error"] is None
        assert controller.status()["fault"] is None
    finally:
        finish(controller)


def test_the_required_nvenc_api_is_the_one_the_encoder_is_built_against():
    header = (REPO / "plugin/gfxwrap/vendor/nvEncodeAPI.h").read_text(encoding="utf-8")
    major, minor = (int(re.search(rf"#define NVENCAPI_{part}_VERSION (\d+)", header).group(1))
                    for part in ("MAJOR", "MINOR"))
    assert gpuadapter.REQUIRED_NVENC_API == (major, minor)


def test_only_an_nvidia_hardware_adapter_with_a_new_enough_driver_may_record(monkeypatch):
    api = [(13, 1)]
    monkeypatch.setattr(gpuadapter, "nvenc_api_version", lambda: api[0])
    assert gpuadapter.unsupported_reason(None) is None  # unknown: the encoder decides
    assert gpuadapter.unsupported_reason(NVIDIA) is None
    assert "no NVIDIA video encoder" in gpuadapter.unsupported_reason(AMD)
    software = gpuadapter.AdapterFacts("Microsoft Basic Render Driver", 0x1414, True, None)
    assert "software adapter" in gpuadapter.unsupported_reason(software)
    api[0] = (12, 2)
    assert "too old" in gpuadapter.unsupported_reason(NVIDIA)
    api[0] = None
    assert "driver" in gpuadapter.unsupported_reason(NVIDIA)


def test_the_verdict_is_asked_once_per_adapter_and_driver(monkeypatch):
    facts, asked = [NVIDIA], []
    monkeypatch.setattr(gpuadapter, "adapter_facts", lambda luid: facts[0])
    monkeypatch.setattr(gpuadapter, "nvenc_api_version", lambda: asked.append(1) or (13, 1))
    assert gpusettings.backend_unsupported(ONE) is None
    assert gpusettings.backend_unsupported(ONE) is None
    assert len(asked) == 1
    facts[0] = replace(NVIDIA, driver="32.0.16.9999")  # a driver update
    gpusettings.backend_unsupported(ONE)
    assert len(asked) == 2


def test_an_adapter_that_cannot_encode_is_a_verdict_before_any_helper(monkeypatch):
    monkeypatch.setattr(gpuadapter, "adapter_facts", lambda luid: AMD)
    session = session_for(Replies())
    session.controller_factory = lambda **_: pytest.fail("a helper for an adapter that cannot encode")
    with pytest.raises(GpuBackendUnsupported, match="no NVIDIA video encoder"):
        session._open_encoder()


def test_no_usable_codec_is_a_verdict_and_a_real_fault_is_not(monkeypatch):
    monkeypatch.setattr(gpuadapter, "adapter_facts", lambda luid: None)
    with pytest.raises(GpuBackendUnsupported, match="neither AV1 nor H.264"):
        session_for(Replies(Result.CODEC, Result.CODEC))._open_encoder()
    assert gpusettings.backend_unsupported(ONE) is not None  # remembered for this adapter
    gpusettings.forget_adapter_codecs()
    with pytest.raises(RuntimeError) as fault:
        session_for(Replies(Result.ENCODER))._open_encoder()
    assert not isinstance(fault.value, GpuBackendUnsupported)
    assert gpusettings.backend_unsupported(ONE) is None


class Control:
    """The wrapper's control page as discovery and the retry gate read it."""
    status_value = NS(producer_pid=42, producer_created_lo=7, producer_created_hi=0,
                      generation=1, build_id="fixture", state=PASSIVE,
                      capabilities=CAP_GPU, rom_open=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def status(self):
        return self.status_value


def factory_and_first_capture(monkeypatch, session_factory):
    desktops = []
    factory = sourcefactory.SourceFactory(
        US_LAYOUT, NS(fps=30), lambda win, fps: desktops.append(win) or NS(stop=lambda: None))
    factory.retry_gate = RetryGate(control_factory=Control)
    monkeypatch.setattr(sourcefactory, "discover", partial(discover, control_factory=Control))
    win = NS(pid=42)
    gpu = factory.create(win)
    assert isinstance(gpu, GpuCapture)
    gpu._demand_factory, gpu._session_factory = Demand, session_factory
    gpu.create_sink(NS(picture_feed=True), object(), object(), lambda *a: None)
    stopped = threading.Event()
    gpu.start(None, stopped.set)
    assert stopped.wait(5)
    gpu.finish()
    return factory, win, gpu, desktops


def test_an_adapter_that_cannot_encode_keeps_the_desktop_without_a_retry(monkeypatch):
    """Today's cost without it: a GPU attempt every 10-60 s, forever, and no
    replay at all. Now: one attempt, no cooldown armed, the desktop grab."""
    monkeypatch.setattr(gpuadapter, "adapter_facts", lambda luid: AMD)
    channel = NS(header=NS(luid_high=ONE[0], luid_low=ONE[1]), close=lambda: None)
    factory, win, gpu, desktops = factory_and_first_capture(monkeypatch, lambda owner, demand: CaptureSession(
        owner, demand, channel_factory=lambda **_: channel,
        controller_factory=lambda **_: pytest.fail("no helper for this adapter")))
    gate = factory.retry_gate
    assert gpu.error is None and gpu.status()["state"] == "unsupported"
    assert gate._failures == 0 and gate.blocked(gate.identity_for(42)) is None
    assert (ONE, AMD.driver) in gpusettings._ADAPTER_VERDICTS
    camera = factory.create(win)
    assert camera.frame_source == "desktop" and desktops == [win]
    assert "no NVIDIA video encoder" in camera.gpu_unsupported
    assert camera.gpu_unsupported in camera.frame_source_note


def test_a_transient_failure_still_retries_the_gpu_route(monkeypatch):
    def failing(owner, demand):
        return NS(run=lambda: (_ for _ in ()).throw(RuntimeError("GPU encoder Open failed")))

    factory, win, gpu, desktops = factory_and_first_capture(monkeypatch, failing)
    gate = factory.retry_gate
    assert gpu.error and gate._failures == 1 and gate.blocked(gate.identity_for(42))
    assert isinstance(factory.create(win), GpuCapture) and desktops == []


# --- logs ------------------------------------------------------------------------

def test_stage_timings_are_logged_on_a_clean_stop_too(caplog):
    owner = NS(settings=GpuSettings(), want_capture=lambda: True, end_audio=lambda h: None)
    demand = NS(snapshot=DemandSnapshot("stopped"), request_stop=lambda reason: None)
    with caplog.at_level(logging.INFO, "sm64.replay"):
        CaptureSession(owner, demand).run()
    lines = [r.getMessage() for r in caplog.records if "stage timings at stop" in r.getMessage()]
    assert len(lines) == 1 and "error=None" in lines[0] and "'channel'" in lines[0]


def test_the_active_line_names_the_codec_and_what_startup_cost(monkeypatch, caplog):
    import sm64_events.replay.channelencoder as channelencoder

    monkeypatch.setattr(session_module, "OfferDecoder",
                        lambda *a: NS(timestamp=lambda qpc: 0.0, namespace="fixture"))
    monkeypatch.setattr(session_module, "MediaRun", NS(starting_at=lambda t: NS(id="run-1")))
    monkeypatch.setattr(session_module, "GpuMedia", lambda *a, **k: None)
    monkeypatch.setattr(session_module, "GpuAudio", lambda *a, **k: None)
    monkeypatch.setattr(session_module, "ChannelSelection", lambda *a: None)
    monkeypatch.setattr(channelencoder, "ChannelEncoder",
                        lambda *a, **k: NS(attach_open=lambda *a, **k: None))
    owner = NS(settings=GpuSettings(), layout=None, clock=None, nominal_rate=30,
               ledger=NS(selection_only=lambda add_row: None))
    session = CaptureSession(owner, NS())
    session.codec = "av1"
    session.channel = NS(header=NS(width=320, height=240, luid_high=0, luid_low=5))
    session.output = NS(bind=lambda run: None, add_row=None, publish_picture=None)
    session.timings.measure("encoder", lambda: None)
    with caplog.at_level(logging.INFO, "sm64.replay"):
        session._create_media(("Open", 1, NS(metadata={"identity": None})), [NS(boundary_qpc=0)])
    line = next(r.getMessage() for r in caplog.records if "GPU recording active" in r.getMessage())
    assert "codec=av1" in line and "'encoder'" in line and "max_started_unix_s" in line
