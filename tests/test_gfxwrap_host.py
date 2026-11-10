"""THE GRAPHICS PLUGIN's capture layer without Project64: the 32-bit test host
(plugin/gfxwrap/host.c) stands in for the emulator, loads a TEST PLUGIN (the
real capture layer linked with the stand-in renderer plugin/gfxwrap/fake_gfx.c,
tools/build_plugin.py build_test_plugin), fakes RDRAM and the VI registers,
and drives ProcessDList / UpdateScreen the way PJ64 does. The stand-in has no
SourceV2 export, so this proves the plugin's surface -- exports, label, the
renderer-only mode, failed initialization, the bounded diagnostics, and that
nothing is loaded or read from an ini -- not capture; capture is driven by
tests/test_wrapper_runtime.py (composition) and tests/test_gpu_delivery.py
(the delivery worker).

Skipped (not failed) on a machine without the x86 MSVC toolchain."""
from __future__ import annotations

import os
import re
import shutil
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
    host = build_plugin.build_test_host(out)
    plugin = build_plugin.build_test_plugin(out)
    return {"host": host, "plugin": plugin, "dir": out}


QUIET = dict(os.environ)


def drive(host: Path, plugin: Path, frames: int, stream_name: str, *extra, env=None) -> str:
    result = subprocess.run([str(host), "--drive", str(plugin), str(frames),
                             "--stream", stream_name, *extra],
                            capture_output=True, text=True, timeout=60, env=env or QUIET,
                            creationflags=_NO_WINDOW, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def unique_name() -> str:
    return f"sm64_trainer_gfx_test_{os.getpid()}_{np.random.randint(1 << 30)}"


def test_the_plugin_exports_everything_pj64_16_requires_and_shows_the_product_label(built):
    info = subprocess.run([str(built["host"]), "--info", str(built["plugin"])],
                          capture_output=True, text=True, creationflags=_NO_WINDOW,
                          check=True).stdout
    # Answered without the renderer (PJ64 asks every DLL in its Plugin folder);
    # the label is the product name, never the renderer's (his decision,
    # 2026-09-16: "Final name is SM64 Trainer v1.0").
    assert "name SM64 Trainer v1.0\n" in info
    assert "version 0x0103" in info and "bswaped 1" in info


def test_a_renderer_without_the_capture_export_is_forwarded_to_and_never_captured(built):
    """The stand-in is a stock renderer: the runtime refuses to configure,
    every call still reaches it, the control page is published, and the
    session closes cleanly. PJ64 1.6's shape (window thread pumping, plugin
    calls on a second thread) included."""
    output = drive(built["host"], built["plugin"], 5, unique_name(), "--cpu-thread")
    assert "drove 5 frames" in output and "control_page 1" in output
    # A plugin that is capturing never switches to the renderer alone.
    assert "renderer_only while_initiated=0" in output
    log = (built["dir"] / "sm64_trainer_gfx.log").read_text(encoding="utf-8")
    activation = log.rsplit("event=init_begin", 1)[-1]
    assert "event=wrapped_info " in activation and "runtime=0" in activation
    assert "event=rom_open_end" in activation and "event=close_end" in activation


def test_native_diagnostics_identify_forwarded_stalls_without_a_reader(built):
    name = unique_name()
    result = subprocess.run(
        [str(built["host"]), "--drive", str(built["plugin"]), "3",
         "--stream", name, "--cpu-thread"],
        capture_output=True, text=True, timeout=60,
        env={**QUIET, "SM64_FAKE_DLIST_DELAY": "1"}, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    log = (built["dir"] / "sm64_trainer_gfx.log").read_text(encoding="utf-8")
    activation = log.rsplit("event=init_begin", 1)[-1]
    assert "event=wrapper_loaded path=" in activation
    stall = next(line for line in activation.splitlines()
                 if "event=callback_stall callback=ProcessDList" in line)
    assert re.search(r"pid=\d+ tid=\d+ build=\S+", stall)
    assert f"build={build_plugin.test_plugin_build_id()}" in stall
    # The first slow call is reported at once, with this callback's own count.
    assert " calls=1 slow_calls=1 " in stall
    assert float(re.search(r" total_ms=([\d.]+)", stall)[1]) >= 20
    assert "event=close_end" in activation and "event=diagnostics_stop" in activation


def test_failed_renderer_initialization_is_reported_not_advertised(built):
    result = subprocess.run(
        [str(built["host"]), "--drive", str(built["plugin"]), "1", "--stream", unique_name()],
        capture_output=True, text=True, timeout=60,
        env={**QUIET, "SM64_FAKE_INIT_FAIL": "1"}, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 5, result.stdout + result.stderr
    log = (built["dir"] / "sm64_trainer_gfx.log").read_text(encoding="utf-8")
    assert "event=init_result success=0" in log.rsplit("event=init_begin", 1)[-1]


def test_the_plugin_reads_no_ini_and_loads_no_other_plugin(built, tmp_path):
    """Until 2026-11-07 the plugin was a wrapper that loaded the renderer named
    by sm64_trainer_gfx.ini. An older install leaves that ini and the renderer
    file beside it: neither changes anything now. An ini naming a missing DLL
    still initializes and forwards to the linked renderer."""
    plugin = tmp_path / build_plugin.PLUGIN_NAME
    shutil.copy2(built["plugin"], plugin)
    (tmp_path / "sm64_trainer_gfx.ini").write_text("wrapped=missing_renderer.dll\nstream=elsewhere\n",
                                                  encoding="ascii")
    name = unique_name()
    output = drive(built["host"], plugin, 2, name)
    assert "drove 2 frames" in output and "control_page 1" in output
    log = (tmp_path / "sm64_trainer_gfx.log").read_text(encoding="utf-8")
    assert "name=\"Fake GFX for the capture layer's host\"" in log.rsplit("event=init_begin", 1)[-1]
    assert "missing_renderer" not in log


def test_renderer_only_mode_is_the_renderer_and_nothing_else(built, tmp_path):
    """SM64RendererOnlyV1, the scene lab's contract (tools/scene_lab/renderer_load.h):
    version 1 only, before InitiateGFX only, and from then on the renderer's own
    GetDllInfo, no control page and no plugin log."""
    plugin = tmp_path / build_plugin.PLUGIN_NAME
    shutil.copy2(built["plugin"], plugin)
    output = drive(built["host"], plugin, 3, unique_name(), "--renderer-only", "--cpu-thread")
    assert "renderer_only version2=0 first=1 again=1" in output
    assert "name Fake GFX for the capture layer's host\n" in output   # the renderer's, not the product's
    assert "renderer_only while_initiated=0" in output                 # refused while initiated
    assert "control_page 0" in output and "drove 3 frames" in output
    assert "renderer_only after_close=1" in output
    assert sorted(path.name for path in tmp_path.iterdir()) == [plugin.name], "no plugin log beside it"
