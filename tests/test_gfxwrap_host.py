"""THE CAPTURE LAYER's wrapper without Project64: the 32-bit test host
(plugin/gfxwrap/host.c) stands in for the emulator, loads the wrapper DLL
against a fake wrapped plugin, fakes RDRAM and the VI registers, and drives
ProcessDList / UpdateScreen the way PJ64 does. The fake has no SourceV2
export, so this proves the wrapper's plugin surface -- exports, label, the
recursion guards, failed initialization, the bounded diagnostics -- not
capture; capture is driven by tests/test_wrapper_runtime.py (composition)
and tests/test_gpu_delivery.py (the delivery worker).

Skipped (not failed) on a machine without the x86 MSVC toolchain."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import build_plugin  # noqa: E402

from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402

pytestmark = pytest.mark.skipif(not build_plugin.toolchain_available(),
                                reason="no x86 MSVC toolchain (vcvars32.bat)")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("gfxwrap")
    host, fake = build_plugin.build_test_host(out)
    wrapper = build_plugin.build_wrapper(out)
    return {"host": host, "fake": fake, "wrapper": wrapper, "dir": out}


QUIET = {**os.environ, "SM64_TRAINER_GFX_NO_DIALOGS": "1"}   # no message box under test


def drive(host: Path, wrapper: Path, frames: int, stream_name: str, *extra) -> str:
    result = subprocess.run([str(host), "--drive", str(wrapper), str(frames),
                             "--stream", stream_name, *extra],
                            capture_output=True, text=True, timeout=60, env=QUIET,
                            creationflags=_NO_WINDOW, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def unique_name() -> str:
    return f"sm64_trainer_gfx_test_{os.getpid()}_{np.random.randint(1 << 30)}"


def test_the_wrapper_exports_everything_pj64_16_requires_and_shows_the_product_label(built):
    info = subprocess.run([str(built["host"]), "--info", str(built["wrapper"])],
                          capture_output=True, text=True, creationflags=_NO_WINDOW,
                          check=True).stdout
    # The dialog is answered from the ini alone (loading the wrapped plugin
    # inside PJ64's enumerate-every-DLL pass would leak a reference per pass);
    # the label is the product name, never the renderer's (his decision,
    # 2026-09-16: "Final name is SM64 Trainer v1.0").
    assert "name SM64 Trainer v1.0\n" in info
    assert "version 0x0103" in info and "bswaped 1" in info


def test_a_renderer_without_the_capture_export_is_forwarded_to_and_never_captured(built):
    """The fake is a stock plugin: the runtime refuses to configure, every
    call still reaches it, and the session closes cleanly. PJ64 1.6's shape
    (window thread pumping, plugin calls on a second thread) included."""
    output = drive(built["host"], built["wrapper"], 5, unique_name(), "--no-context", "--cpu-thread")
    assert "drove 5 frames" in output
    log = (built["dir"] / "sm64_trainer_gfx.log").read_text(encoding="utf-8")
    activation = log.rsplit("event=init_begin", 1)[-1]
    assert "event=wrapped_info " in activation and "runtime=0" in activation
    assert "event=rom_open_end" in activation and "event=close_end" in activation


def test_native_diagnostics_identify_forwarded_stalls_without_a_reader(built):
    name = unique_name()
    result = subprocess.run(
        [str(built["host"]), "--drive", str(built["wrapper"]), "3",
         "--stream", name, "--no-context", "--cpu-thread"],
        capture_output=True, text=True, timeout=60,
        env={**QUIET, "SM64_FAKE_DLIST_DELAY": "1"}, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    log = (built["dir"] / "sm64_trainer_gfx.log").read_text(encoding="utf-8")
    activation = log.rsplit("event=init_begin", 1)[-1]
    assert "event=wrapper_loaded path=" in activation
    assert "event=wrapped_loaded path=" in activation
    stall = next(line for line in activation.splitlines()
                 if "event=callback_stall callback=ProcessDList" in line)
    assert re.search(r"pid=\d+ tid=\d+ build=\S+", stall)
    assert f"build={build_plugin.wrapper_build_id()}" in stall
    assert float(re.search(r" wrapped_ms=([\d.]+)", stall)[1]) >= 20
    assert float(re.search(r" extra_ms=([\d.]+)", stall)[1]) < 20
    assert "event=close_end" in activation and "event=diagnostics_stop" in activation


def test_failed_wrapped_initialization_is_reported_not_advertised(built):
    result = subprocess.run(
        [str(built["host"]), "--drive", str(built["wrapper"]), "1", "--stream", unique_name(), "--no-context"],
        capture_output=True, text=True, timeout=60,
        env={**QUIET, "SM64_FAKE_INIT_FAIL": "1"}, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 5, result.stdout + result.stderr
    log = (built["dir"] / "sm64_trainer_gfx.log").read_text(encoding="utf-8")
    assert "event=init_result success=0" in log.rsplit("event=init_begin", 1)[-1]


def test_a_renamed_copy_of_the_wrapper_is_rejected_before_recursive_initialization(built):
    copy = built["dir"] / "renamed-wrapper.dll"
    copy.write_bytes(built["wrapper"].read_bytes())
    result = subprocess.run(
        [str(built["host"]), "--drive", str(built["wrapper"]), "1",
         "--stream", unique_name(), "--no-context", "--wrapped", copy.name],
        capture_output=True, text=True, timeout=60, env=QUIET, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 5, result.stdout + result.stderr


def test_an_ini_naming_the_wrapper_itself_leaves_it_unwrapped_instead_of_recursing(built):
    """Fresh-context review finding: forwarding to ourselves would recurse
    until the stack died. The wrapper notices its own module and reports
    the wrapped plugin as missing."""
    name = unique_name()
    result = subprocess.run([str(built["host"]), "--drive", str(built["wrapper"]), "2",
                             "--stream", name, "--wrapped", "sm64_trainer_gfx.dll"],
                            capture_output=True, text=True, timeout=60, env=QUIET,
                            creationflags=_NO_WINDOW, check=False)
    # InitiateGFX refuses (the host reports it and exits 5) -- no recursion,
    # no crash, and the reason is in the log beside the wrapper
    assert result.returncode == 5, result.stdout + result.stderr
    assert "InitiateGFX failed" in result.stderr
    log = built["wrapper"].parent / "sm64_trainer_gfx.log"
    assert log.exists() and "names the capture layer itself" in log.read_text()
