"""Actual wrapper exports/IPC with CPU-only source and delivery fixtures."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay import capturecontrol as C
from sm64_events.replay import gpurequest as R

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"
LIMITS = R.RequestLimits(8, 128 << 20, 8 << 20, 16 << 20, 8, 1 << 20,
                        4 << 20, 1 << 20, 256, 2000, 3000)
TABLE = [("counter", 0, 4), ("guarded", 4092, 16)]


def load_build():
    spec = importlib.util.spec_from_file_location("wrapper_build", ROOT / "tools/build_plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def binaries(tmp_path_factory):
    work = tmp_path_factory.mktemp("wrapper_runtime")
    source = work / "source"
    shutil.copytree(NATIVE, source)
    # The fake is explicit and local: this DLL tests composition, never GPU delivery.
    shutil.copy2(source / "wrapper_runtime_fake.cpp", source / "gpu_delivery.cpp")
    build = load_build()
    build.SOURCE = source
    # Graphics internals have independent real-GPU witnesses. This host exercises
    # the actual production composition modules through the fixture gd facade.
    build.RUNTIME_SOURCES = ("wrapper_runtime", "stamp_adapter", "runtime_control",
                             "runtime_delivery", "gpu_request", "gpu_delivery")
    vcvars = build.find_vcvars32()
    assert vcvars
    wrapper = build.build_wrapper(work, vcvars)
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", f"/I{source}"]
    link = ["/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *build.LIBS]
    for name, extra in [("original", []), ("unsupported", ["/DWR_UNSUPPORTED"])]:
        build._cl(vcvars, flags + extra + ["/LD", str(source / "wrapper_runtime_original.cpp"),
            str(source / "renderer_boundary.cpp"), f"/Fe:{work / (name + '.dll')}", f"/Fo{work}\\", *link], work)
    host = work / "wrapper_runtime.exe"
    build._cl(vcvars, flags + [str(source / "wrapper_runtime_host.cpp"),
        f"/Fe:{host}", f"/Fo{work}\\", *link], work)
    return work, wrapper, host


def eventual(call, predicate=bool, timeout=4):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            result = call()
            if predicate(result):
                return result
        except (FileNotFoundError, BlockingIOError):
            pass
        time.sleep(0.01)
    raise AssertionError("wrapper response timed out")


def command(child, text):
    child.stdin.write(text + "\n")
    child.stdin.flush()
    return child.stdout.readline().strip()


@pytest.fixture
def start(binaries, tmp_path):
    work, wrapper, host = binaries
    for path in [wrapper, host, work / "original.dll", work / "unsupported.dll"]:
        shutil.copy2(path, tmp_path / path.name)
    running = []

    def launch(*, unsupported=False, fail=False):
        name = "sm64_wrapper_runtime_" + uuid.uuid4().hex
        original = "unsupported.dll" if unsupported else "original.dll"
        ini = tmp_path / "sm64_trainer_gfx.ini"
        ini.write_text(f"wrapped={original}\nstream={name}\n", encoding="ascii")
        env = dict(os.environ)
        if fail:
            env["WR_FAIL_INIT"] = "1"
        child = subprocess.Popen([str(tmp_path / host.name), str(tmp_path / wrapper.name)],
            cwd=tmp_path, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, **quiet_spawn_kwargs())
        running.append((child, None))
        assert child.stdout.readline().strip() == f"ready {int(not fail)}"
        control = None if fail else eventual(lambda: C.CaptureControl(name))
        running[-1] = (child, control)
        return child, control, name, ini

    yield launch
    for child, control in running:
        if control:
            control.close()
        stdout, stderr = child.communicate("quit\n", timeout=8)
        assert child.returncode == 0, stdout + stderr


def record(child):
    return tuple(map(int, command(child, "record").split()))


def test_delivery_diagnostic_reaches_actual_bounded_plugin_log(start):
    child, _, _, ini = start()
    path = ini.parent / "sm64_trainer_gfx.log"
    eventual(lambda: path.exists())
    assert command(child, "diagnostic") == "1"
    text = eventual(lambda: path.read_text(encoding="utf-8"),
                    lambda value: "event=gpu_phase" in value)
    assert "event=gpu_failure" in text
    assert "reason=10022" in text and "observed_qpc=20000000 frequency=10000000" in text
    assert "source=8" in text
    assert "first_refusal=unavailable" in text
    assert "phase=sample calls=3 total_ticks=1700000 max_ticks=1500000" in text


def test_passive_then_hot_capture_has_stamps_and_guarded_rows(start):
    child, control, _, _ = start()
    assert control.status().capabilities & 2
    assert command(child, "frame 1 1") == "1 1"
    assert record(child)[0] == 0
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda s: s.state == 4)
        assert command(child, "frame 1 1") == "2 2"
        row = record(child)
        assert row[0] == 1 and row[2:7] == (1, 2, 4, 0, 1)
        assert command(child, "frame 2 1") == "4 3"
        assert record(child)[2:7] == (2, 4, 4, 0, 1)
    eventual(control.status, lambda s: s.state == C.PASSIVE)
    assert command(child, "frame 1 1") == "5 4"
    assert record(child)[0] == 0


def test_startup_zero_lists_and_same_origin_preserve_pending_lists(start):
    child, control, _, _ = start()
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda s: s.state == 4)
        command(child, "frame 0 1")
        assert record(child)[2:7] == (0, 0, 0, 0, 1)
        command(child, "frame 1 0")
        assert record(child)[6:8] == (0, 0x10000)
        command(child, "frame 0 1")
        assert record(child)[2:7] == (1, 1, 4, 0, 1)


def test_rom_close_revokes_before_forward_and_requires_fresh_request(start):
    child, control, _, _ = start()
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda s: s.state == 4)
        assert command(child, "romclose") == "ok"
        assert command(child, "stats").split()[1] == "0"
        assert command(child, "romopen") == "ok"
        eventual(control.status, lambda s: s.state == C.UNAVAILABLE)
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda s: s.state == 4)
        command(child, "frame 1 1")
        assert record(child)[2:7] == (1, 1, 4, 0, 1)


def test_unsupported_renderer_and_failed_init_keep_original_results(start):
    child, control, _, _ = start(unsupported=True)
    assert not control.status().capabilities & 2
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda s: s.state == C.UNAVAILABLE)
        assert command(child, "stats") == "0 0"
        assert command(child, "frame 1 1") == "10 1"


def test_failed_original_initialization_does_not_create_control_or_backend(start):
    child, control, name, _ = start(fail=True)
    assert control is None and command(child, "stats") == "0 0"
    with pytest.raises(FileNotFoundError):
        C.CaptureControl(name)


def test_foreign_renderer_reinitialization_revokes_capability(start):
    child, control, name, ini = start()
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda s: s.state == 4)
        command(child, "frame 1 1")
        assert record(child)[0] == 1
        ini.write_text(f"wrapped=unsupported.dll\nstream={name}\n", encoding="ascii")
        assert command(child, "reinit") == "1"
        eventual(control.status, lambda s: not s.capabilities & 2)
        assert command(child, "stats").split()[1] == "0"
        assert command(child, "frame 1 1") == "11 2"
        assert record(child)[0] == 0


def test_same_renderer_reinitialization_keeps_one_registration_and_new_epoch(start):
    child, control, _, _ = start()
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda s: s.state == 4)
        command(child, "frame 1 1")
        assert record(child)[0] == 1
        generation = control.status().generation
        assert command(child, "reinit") == "1"
        eventual(control.status, lambda s: s.capabilities & 2 and s.generation != generation)
        assert command(child, "stats").split()[1] == "0"
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda s: s.state == 4)
        command(child, "frame 1 1")
        assert record(child)[2:7] == (1, 2, 4, 0, 1)


def test_close_without_romclose_revokes_before_original_and_clears_capability(start):
    child, control, _, _ = start()
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda s: s.state == 4)
        assert command(child, "close") == "ok"
        eventual(control.status, lambda s: s.state == C.CLOSED and not s.capabilities & 2)
        assert command(child, "stats").split()[1] == "0"


def test_gpu_build_refuses_incomplete_runtime(tmp_path):
    build = load_build()
    build.SOURCE = tmp_path
    with pytest.raises(RuntimeError, match="GPU runtime implementation is incomplete"):
        build._runtime_objects(None, tmp_path, tmp_path / "identity.h")


def test_a_real_run_on_vanilla_sm64_runs_the_plain_renderer(start):
    """His ruling, 2026-09-16: on vanilla SM64 -- or any ROM that is not a
    practice ROM -- the plugin functions identically to the baseline renderer.
    Every frame forwards, the control page says baseline, and even a live
    capture request is never admitted. Reopening Usamune captures again."""
    child, control, _, _ = start()
    assert command(child, "romclose") == "ok"
    assert command(child, "cart vanilla") == "ok"
    assert command(child, "romopen") == "ok"
    status = eventual(control.status, lambda s: s.baseline_rom)
    assert not status.rom_open
    lists, vi = map(int, command(child, "frame 1 1").split())
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        assert tuple(map(int, command(child, "frame 2 1").split())) == (lists + 2, vi + 1)
        time.sleep(0.3)
        assert control.status().state == C.PASSIVE
        assert command(child, "stats").split()[0] == "0"   # no request reached delivery
        assert record(child)[0] == 0                       # and no picture was staged
    assert command(child, "romclose") == "ok"
    assert command(child, "cart usamune") == "ok"
    assert command(child, "romopen") == "ok"
    status = eventual(control.status, lambda s: s.rom_open)
    assert not status.baseline_rom
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda s: s.state == 4)
        command(child, "frame 1 1")
        assert record(child)[0] == 1
