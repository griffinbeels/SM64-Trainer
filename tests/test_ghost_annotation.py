"""CPU-only annotation validation, exact-ROM font orientation and bounded layout."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_native_annotations(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 native compiler")
    spec = importlib.util.spec_from_file_location("annotation_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    assert vc, "x86 MSVC is required for the annotation witness"
    target = tmp_path / "ghost_annotation_host.exe"
    build._cl(vc, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", f"/I{ROOT / 'plugin/gfxwrap'}",
        str(ROOT / "plugin/gfxwrap/ghost_annotation_assets.cpp"), str(ROOT / "tests/native/ghost_annotation_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    args = [str(target)]
    if rom := os.environ.get("SM64_GHOST_ROM"):
        args.extend([rom, str(tmp_path / "annotation-atlas.rgba"), str(tmp_path / "annotation-layout.bin")])
    result = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=20,
        check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["checks"] > 50
