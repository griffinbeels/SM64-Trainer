"""Native occurrence, pose and queue contracts; no emulator or installation.

The actual staged-list test additionally needs SM64_GHOST_WITNESS_SOURCE set
to a current tools/stage_link_witness.py --boundary --context-lifetime --ghost
output. An absent staged renderer is an explicit skip, not integration proof.
"""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def _build_host(tmp_path, host, sources, includes=()):
    spec = importlib.util.spec_from_file_location("ghost_pass_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    if not vcvars:
        pytest.skip("requires x86 MSVC native toolchain")
    target = tmp_path / f"{host}_host.exe"
    build._cl(vcvars, ["/nologo", "/std:c++17", "/EHsc", "/MT", "/W4", "/WX",
        f"/I{ROOT / 'plugin/gfxwrap'}", f"/I{ROOT / 'renderer/model'}",
        *[f"/I{path}" for path in includes],
        *[str(ROOT / "plugin/gfxwrap" / name) for name in sources],
        str(ROOT / f"tests/native/{host}_host.cpp"), f"/Fe:{target}", f"/Fo{tmp_path}\\",
        "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=15, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout


@pytest.mark.parametrize("host,sources", [
    ("ghost_pass", ["ghost_pass.cpp"]),
    ("ghost_stream", ["ghost_pass.cpp", "ghost_clock.cpp", "ghost_root.cpp", "ghost_stream.cpp"]),
    ("ghost_pose", ["ghost_pose.cpp"]),
])
def test_native_world_and_stream_contracts(tmp_path, host, sources):
    _build_host(tmp_path, host, sources)


def test_actual_staged_list_lifetime(tmp_path):
    value = os.environ.get("SM64_GHOST_WITNESS_SOURCE")
    if not value:
        pytest.skip("requires current pinned renderer staging (SM64_GHOST_WITNESS_SOURCE)")
    source = Path(value).resolve() / "src"
    gl = source / "Graphics/OpenGLContext"
    for name in ("ghost_link.cpp", "ghost_link.h", "ghost_stream.cpp", "ghost_pass.cpp"):
        assert (gl / name).read_bytes() == (ROOT / "plugin/gfxwrap" / name).read_bytes(), name
    rsp = (source / "RSP.cpp").read_bytes()
    start = rsp.index(b"\tstruct GhostList {")
    end = rsp.index(b"\t} ghostList;", start) + len(b"\t} ghostList;")
    (tmp_path / "actual_ghost_list.inc").write_bytes(rsp[start:end])
    _build_host(tmp_path, "ghost_list", [], [tmp_path])
