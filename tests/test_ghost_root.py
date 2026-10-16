"""Execute the captured root contract without attaching to the emulator."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.memory.ghost_layout import GhostLayout
from sm64_events.memory.layout import US

ROOT = Path(__file__).resolve().parents[1]


def tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"tools/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def function(source, signature):
    start = source.index(signature)
    opened = source.index("{", start)
    depth = 1
    end = opened + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def staged_dispatch(tmp_path):
    spec = json.loads((ROOT / "renderer/build-inputs.json").read_text(encoding="utf-8"))["upstream"]
    archive = Path(os.environ["LOCALAPPDATA"]) / "SM64Trainer/build-cache/renderer" / f"GLideN64-{spec['commit']}.zip"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == spec["archive_sha256"]
    with zipfile.ZipFile(archive) as source:
        prefix = f"GLideN64-{spec['commit']}/src/"
        gsp = source.read(prefix + "gSP.cpp")
        rsp = source.read(prefix + "RSP.cpp").decode()
        matrix = source.read(prefix + "RSP_LoadMatrixX86.cpp").decode()
    actual_loop = function(rsp, "void _ProcessDList()")
    assert actual_loop.index("RSP.PC[RSP.PCi] += 8;") < actual_loop.index("GBI.cmd[RSP.cmd]")
    stage, boundary = tool("stage_ghost_renderer"), tool("stage_link_witness")
    patched = stage.display_list_edit(gsp, boundary.replace_once).decode()
    (tmp_path / "actual_root_dispatch.inc").write_text(function(patched, "void gSPDisplayList("), encoding="utf-8")
    (tmp_path / "actual_root_matrix.inc").write_text(function(matrix, "void RSP_LoadMatrix("), encoding="utf-8")
    link = (ROOT / "plugin/gfxwrap/ghost_link.cpp").read_text(encoding="utf-8")
    (tmp_path / "actual_root_link.inc").write_text(function(link, "void display_list("), encoding="utf-8")


@pytest.mark.parametrize("host", ["ghost_root", "ghost_root_dispatch"])
def test_executed_root_after_transient_throw_pointer_clears(tmp_path, host):
    native = Path(os.environ.get("SM64_GHOST_ROOT_BASELINE", ROOT / "plugin/gfxwrap"))
    baseline = "SM64_GHOST_ROOT_BASELINE" in os.environ
    if baseline and host != "ghost_root":
        pytest.skip("baseline has no renderer root instrumentation")
    if host == "ghost_root_dispatch":
        staged_dispatch(tmp_path)
    spec = importlib.util.spec_from_file_location("root_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "root-transform witness requires x86 MSVC"
    sources = [native / "ghost_capture.cpp", native / "ghost_clock.cpp"]
    if not baseline:
        sources.append(native / "ghost_root.cpp")
    target = tmp_path / "ghost_root_host.exe"
    build._cl(vcvars, ["/nologo", "/std:c++17", "/EHsc", "/MT", "/W4", "/WX",
        f"/I{native}", f"/I{tmp_path}", *map(str, sources), str(ROOT / f"tests/native/{host}_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED",
        "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    config = bytearray(GhostLayout.from_layout(US).to_bytes())
    if baseline:
        config[4:8] = (3).to_bytes(4, "little")
    path = tmp_path / "layout.bin"
    path.write_bytes(config)
    result = subprocess.run([str(target), str(path)], cwd=tmp_path, capture_output=True,
                            text=True, timeout=15, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout
