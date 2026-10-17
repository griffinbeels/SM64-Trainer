"""JP ghost names borrow the US font from the player's own US cartridge.

The per-user cache file is written whole by a US atlas load and read by a JP
one; anything missing, short, altered or foreign leaves JP's own capitals.
SM64_GHOST_ROM and SM64_GHOST_JP_ROM add the real cartridges. LOCALAPPDATA
points at the test's own folder, so the player's real cache is never touched.
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


def test_jp_names_borrow_the_cached_us_font(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 native compiler")
    spec = importlib.util.spec_from_file_location("font_cache_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    assert vc, "x86 MSVC is required for the font cache witness"
    native = ROOT / "plugin/gfxwrap"
    target = tmp_path / "ghost_font_cache_host.exe"
    build._cl(vc, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", f"/I{native}", f"/I{ROOT / 'renderer/model'}",
        *(str(native / name) for name in ("ghost_font_cache.cpp", "ghost_assets.cpp", "ghost_annotation_assets.cpp")),
        str(ROOT / "tests/native/ghost_font_cache_host.cpp"), f"/Fe:{target}", f"/Fo{tmp_path}\\",
        "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    home = tmp_path / "localappdata"
    home.mkdir()
    args = [str(target), str(home)]
    roms = os.environ.get("SM64_GHOST_ROM"), os.environ.get("SM64_GHOST_JP_ROM")
    if all(roms):
        args.extend(roms)
    result = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
                            env={**os.environ, "LOCALAPPDATA": str(home)}, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["checks"] > 25 and report["rom"] == all(roms)
    if all(roms):
        assert (home / "SM64Trainer/plugin-cache/us-dialogue-font.bin").stat().st_size == 48 + 32 + 256 * 64
