"""CPU-only annotation validation, exact-ROM font orientation and bounded layout."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.native_session import LABEL_CHARACTERS

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
    # Each supplied cartridge reads its own places: SM64_GHOST_ROM (Usamune US)
    # and SM64_GHOST_JP_ROM (Usamune JP). No ROM byte is distributed.
    runs = [("synthetic", None)] + [(name, os.environ[key]) for name, key in
                                    (("us", "SM64_GHOST_ROM"), ("jp", "SM64_GHOST_JP_ROM")) if os.environ.get(key)]
    atlases = {}
    for name, rom in runs:
        args = [str(target)]
        if rom:
            atlas = tmp_path / f"annotation-atlas-{name}.rgba"
            args.extend([rom, str(atlas), str(tmp_path / f"annotation-layout-{name}.bin")])
            atlases[name] = atlas
        result = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=20,
            check=False, **quiet_spawn_kwargs())
        assert result.returncode == 0, f"{name}: {result.stdout}{result.stderr}"
        report = json.loads(result.stdout)
        assert report["ok"] and report["checks"] > 50 and report["rom"] == (rom is not None)
        assert {chr(c) for c in report["label_characters"]} == LABEL_CHARACTERS
    if len(atlases) == 2:
        # Below the font cells (rows 128 on) every cartridge holds the same art:
        # HUD numerals and letters, Usamune's buttons, ring and base, the chevron.
        us, jp = (atlases[name].read_bytes() for name in ("us", "jp"))
        row = 256 * 4
        assert us[128 * row:] == jp[128 * row:] and us[:128 * row] != jp[:128 * row]
