"""Ghost sound: real queue, audio thread and SM64 audio engine into captured PCM.

Needs a local US ROM (SM64_MODEL_ROM) and the cached pinned libsm64 archive
(SM64_MODEL_SEED_DIR); it skips, saying so, without them. The waveOut smoke
inside only plays silence and only when a device exists.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def tool(name):
    spec = importlib.util.spec_from_file_location(f"audio_{name}", ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ghost_sound_engine_queue_and_volume(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 audio engine")
    rom, seed = os.environ.get("SM64_MODEL_ROM"), os.environ.get("SM64_MODEL_SEED_DIR")
    if not rom or not seed:
        pytest.skip("set SM64_MODEL_ROM (local US ROM) and SM64_MODEL_SEED_DIR (pinned libsm64)")
    compiler = tool("build_plugin")
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain required"
    library = tool("build_model").build_model(tmp_path / "model", seed=Path(seed), vcvars32=vcvars)
    source = tmp_path / "model/source/src"
    native = ROOT / "plugin/gfxwrap"
    target = tmp_path / "ghost_audio_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{native}", f"/I{ROOT / 'renderer/model'}", f"/I{source}", f"/I{source / 'decomp/include'}",
        str(native / "ghost_audio.cpp"), str(native / "ghost_assets.cpp"),
        str(ROOT / "tests/native/ghost_audio_host.cpp"), str(library),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED",
        "/MANIFESTUAC:level='asInvoker'", "winmm.lib", "bcrypt.lib", *compiler.LIBS], tmp_path)
    result = subprocess.run([str(target), rom], cwd=tmp_path, capture_output=True, text=True,
                            timeout=120, check=False, **quiet_spawn_kwargs())
    (tmp_path / "audio-witness.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    print(json.dumps(report))
    assert report["ok"] and report["cues"] == 4 and report["nonzero_samples"] > 10000
    assert 0.24 < report["half_energy_ratio"] < 0.26
    # Request to first audible sample, with the device a tick deep.
    assert report["stale_samples_when_left_on"] > 1000  # the resume check is not vacuous
    assert report["capture_max_latency_ms"] < 80
    # Placement: the engine pans and distances each ghost; gain scales amplitude.
    assert report["right_placed_right_left_energy"] > 4 and report["left_placed_right_left_energy"] < 0.25
    assert 0.055 < report["gain_quarter_energy_ratio"] < 0.07
    assert report["camera_5000_energy_ratio"] < 0.5 and report["second_ghost_routed_identical"]
    # Every class of sound Mario's recordings carry obeys one rule: the ghost
    # volume and the ghost's gain (distance x overlap). The menu bank (the star
    # and cap grab) and a constant-frequency moving sound once skipped the gain.
    classes = report["classes"]
    assert {"action", "moving", "voice", "general", "object", "object2",
            "menu_star", "menu_hole", "menu_pipe", "moving_constant_frequency"} <= set(classes)
    for name, measured in classes.items():
        assert 0.03 < measured["gain"] < 0.1 and 0.24 < measured["volume"] < 0.26, (name, measured)
    if report["device"]:
        assert report["device_underruns"] == 0 and report["device_max_latency_ms"] < 80, report
