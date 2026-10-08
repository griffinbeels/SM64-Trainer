"""Exercise the shipped reader inside PJ64's manifest-less x86 host class."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 native file reader")
def test_native_reader_handles_long_local_paths_without_weakening_refusals(tmp_path):
    spec = importlib.util.spec_from_file_location("assets_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "native asset reader needs the x86 compiler"
    native = ROOT / "plugin/gfxwrap"
    target = tmp_path / "assets.exe"
    build._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{native}", str(native / "ghost_assets.cpp"), str(ROOT / "tests/native/ghost_assets_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:NO", *build.LIBS], tmp_path)
    short = tmp_path / "short.ghost"
    short.write_bytes(b"ghost")
    folder = tmp_path
    while len(str(folder / "long.ghost")) < 280:
        folder /= "recordings-" + "x" * 35
    folder.mkdir(parents=True)
    long = folder / "long.ghost"
    long.write_bytes(short.read_bytes())
    empty = tmp_path / "empty.ghost"
    empty.write_bytes(b"")
    oversized = tmp_path / "large.ghost"
    oversized.write_bytes(b"x" * 65537)
    paths = [str(short), str(long), str(long).replace("\\", "/"),
        str(folder / ".." / folder.name / "long.ghost"), "short.ghost", "file:///" + str(short),
        "\\\\?\\" + str(long), str(tmp_path / "missing.ghost"), str(empty), str(oversized)]
    result = subprocess.run([str(target), *paths], cwd=tmp_path, capture_output=True,
        text=True, timeout=10, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] != "plain_error=0", "host unexpectedly bypassed the legacy path limit"
    assert lines[1:] == ["1 5"] * 4 + ["0 0"] * 6
