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

import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS

import pytest

from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
from sm64_events.ghost.window.controller import parent_map
from sm64_events.memory.layout import US as US_LAYOUT
from sm64_events.replay import gpusettings
from sm64_events.replay.gpucapture import GpuCapture
from sm64_events.replay.gpucapture_session import helper_launch
from sm64_events.replay.gpudemand import DemandSnapshot
from sm64_events.replay.gpuprocess import process_controller, process_worker
from sm64_events.replay.gpuprocess.process_controller import Controller
from sm64_events.replay.gpusettings import GpuSettings

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
