"""Hidden native playback ownership/selection witness; no graphics or emulator."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_native_playback_ownership_and_selection(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 renderer component")
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for the native playback witness"
    target = tmp_path / "ghost_playback_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        f"/I{ROOT / 'renderer/model'}", f"/I{ROOT / 'plugin/gfxwrap'}",
        str(ROOT / "plugin/gfxwrap/ghost_playback.cpp"), str(ROOT / "plugin/gfxwrap/ghost_origin.cpp"),
        str(ROOT / "plugin/gfxwrap/ghost_annotation_assets.cpp"),
        str(ROOT / "tests/native/ghost_playback_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/OPT:REF",
        "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *compiler.LIBS], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=30, check=False, **quiet_spawn_kwargs())
    (tmp_path / "playback-witness.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["checks"] > 50 and report["models"] >= 3
    # Several ghosts shared one origin; each clip was prepared exactly once per copy.
    assert report["multi"] == {"ghosts": 3, "max_visible": 3, "prepares": 5}
    assert report["render_allocations"] == report["render_frees"] == 0
    # Every evaluation ran as the renderer runs it: the list's plan first (it
    # gates the translucent layer), matching what evaluate then drew. The
    # witness reached drawn lists, hidden ones and pose refusals.
    plans = report["plans"]
    assert plans["drawn"] > 0 and plans["hidden"] > 0 and plans["refused"] > 0, plans
    # A dance-tail ghost fades from its last dance frame to hidden over the
    # tuning constant, one equal step a frame.
    print(json.dumps(report["dance"]))
    fade = report["dance"]["fade"]
    steps = report["dance"]["hidden_after"]
    assert steps >= 1 and len(fade) == steps + 1 and fade[0] == 1 and fade[-1] == 0
    assert all(abs(fade[k] - (steps - k) / steps) < 1e-6 for k in range(steps + 1))
