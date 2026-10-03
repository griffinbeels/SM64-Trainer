"""The overlay warm-up takes an injected overlay's start-up freeze off the picture.

His report, 2026-10-03: "the entire picture freezes" when Discord's overlay
attaches, and "we shouldn't assume that players will disable that". The
wrapper makes one D3D11 device while the game is loading
(plugin/gfxwrap/overlay_warmup.h); this witness drives the real unit the way
gfxwrap.c does and times what an overlay's hook then costs a busy GL renderer,
in fresh processes, against a renderer without it. The policy runs anywhere;
the timing needs a real GPU with WGL_NV_DX_interop: SM64_TEST_OVERLAY_WARMUP=1.
"""
import importlib.util
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("Win32 witness")
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    if vcvars is None:
        pytest.skip("x86 MSVC unavailable")
    out = tmp_path_factory.mktemp("overlay_warmup")
    exe = out / "overlay_warmup_host.exe"
    build._cl(vcvars, ["/nologo", "/O2", "/MT", "/EHsc", "/std:c++17", f"/I{ROOT / 'plugin/gfxwrap'}",
                       str(ROOT / "plugin/gfxwrap/overlay_warmup.cpp"),
                       str(ROOT / "tests/native/overlay_warmup_host.cpp"), f"/Fe:{exe}", f"/Fo{out}\\",
                       "/link", "opengl32.lib", "user32.lib", "gdi32.lib", "winmm.lib"], out)
    return exe


def run(exe, mode):
    result = subprocess.run([str(exe), mode], capture_output=True, text=True, timeout=60, check=False,
                            **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_it_warms_once_inside_a_load_and_never_in_ordinary_play(host):
    assert run(host, "logic")["ok"]


@pytest.mark.skipif(os.environ.get("SM64_TEST_OVERLAY_WARMUP") != "1",
                    reason="GPU witness: SM64_TEST_OVERLAY_WARMUP=1 (hidden window, real driver)")
def test_the_overlays_start_up_freeze_moves_into_the_load(host):
    runs = {"cold": [], "warm": []}
    for _ in range(3):  # one fresh process per run: the driver's start-up is once per process
        for mode in runs:
            report = run(host, mode)
            assert report["hidden"] and report["focus_unchanged"], report
            runs[mode].append(report)
    print(json.dumps(runs))
    for report in runs["warm"]:
        assert report["attempts"] >= 1 and report["absorbed"] and 1500 <= report["warm_at_ms"] < 1750, report
        assert report["playing_max_ms"] < 20, "nothing it does lands outside the load"
    cold = statistics.median(r["overlay_window_ms"] for r in runs["cold"])
    warm = statistics.median(r["overlay_window_ms"] for r in runs["warm"])
    assert cold > 60, "without it an overlay's start-up freezes the renderer"
    assert warm < cold / 2 and warm < 50, (cold, warm)
