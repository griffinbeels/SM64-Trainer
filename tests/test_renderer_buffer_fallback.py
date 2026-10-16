"""Compile the pinned drawer's real class and methods against inert GL calls.

No renderer DLL, driver, context, or device is built/loaded. A cached verified
archive or SM64_BUFFER_DRAWER_SOURCE supplies the unmodified upstream bytes.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
GL = "src/Graphics/OpenGLContext/"
FILES = {
    GL + "opengl_BufferedDrawer.cpp": "e5f6cc7d626f766ca9111b21d1ea59e78a80cd62ef34c4dc149bba92855995f6",
    GL + "opengl_BufferedDrawer.h": "565d622edfc12b015a5bc35e329c9e35ca3618a1393a5cf3a227901867d24274",
    GL + "opengl_CachedFunctions.h": "b12acffbb7bbcbe7ae7c8bf26bb9deb7adb1c348cd3b739344af84f5ce64decd",
    "src/Graphics/Parameter.h": "7a4422a889082a6a8886ea38b4f9cc9eda065cb58da618c1ed84d8d231516a6f",
    "src/Graphics/ObjectHandle.h": "0e8df26bb80f9389019629377f7a3ec8676a4ed02557d98f5e44c6abd19d1c16",
}


def tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def drawer_source():
    explicit = os.environ.get("SM64_BUFFER_DRAWER_SOURCE")
    if explicit:
        data = {name: (Path(explicit) / name).read_bytes() for name in FILES}
    else:
        upstream = json.loads((ROOT / "renderer/build-inputs.json").read_text())["upstream"]
        cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        archive = cache / "SM64Trainer/build-cache/renderer" / f"GLideN64-{upstream['commit']}.zip"
        if not archive.is_file():
            pytest.skip("requires separately staged pinned LINK source input: SM64_BUFFER_DRAWER_SOURCE or cached archive")
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == upstream["archive_sha256"]
        with zipfile.ZipFile(archive) as handle:
            prefix = f"GLideN64-{upstream['commit']}/"
            data = {name: handle.read(prefix + name) for name in FILES}
    for name, expected in FILES.items():
        assert hashlib.sha256(data[name]).hexdigest() == expected, f"pristine pinned source drift: {name}"
    return data


def without_includes(data):
    # External dependencies become inert types/functions; method text stays whole.
    return b"\n".join(line for line in data.splitlines() if not line.startswith(b"#include")) + b"\n"


@pytest.mark.parametrize("variant", ["candidate", "baseline", "missing_binding_reset"])
def test_actual_buffered_drawer_fallback(tmp_path, drawer_source, variant):
    source = drawer_source[GL + "opengl_BufferedDrawer.cpp"]
    if variant != "baseline":
        source = tool("stage_link_witness").buffered_drawer_overlay(source)
    if variant == "missing_binding_reset":
        assert source.count(b"m_bindBuffer->reset();") == 1
        source = source.replace(b"m_bindBuffer->reset();", b"/* deliberately omitted */")
    (tmp_path / "drawer_methods.inc").write_bytes(without_includes(source))
    (tmp_path / "drawer_class.inc").write_bytes(without_includes(drawer_source[GL + "opengl_BufferedDrawer.h"]))
    for name in ("Parameter", "ObjectHandle"):
        (tmp_path / f"{name}.inc").write_bytes(without_includes(drawer_source[f"src/Graphics/{name}.h"]))
    cached = drawer_source[GL + "opengl_CachedFunctions.h"]
    start = cached.index(b"\ttemplate<class T1, class T2>")
    end = cached.index(b"\tclass Cached4", start)
    bind_start = cached.index(b"\ttemplate<typename Bind>")
    bind_end = cached.index(b"\ttypedef CachedBind<decltype(GET_GL_FUNCTION(glBindFramebuffer))>", bind_start)
    (tmp_path / "drawer_cached_bind.inc").write_bytes(
        b"namespace opengl {\n#define CACHED_USE_CACHE2\n" + cached[start:end] + cached[bind_start:bind_end] +
        b"using CachedBindBuffer = CachedBind<decltype(&glBindBuffer)>;\n}\n")
    compiler = tool("build_plugin")
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 C++ compiler required for the inert drawer host"
    target = tmp_path / "drawer_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{tmp_path}", str(ROOT / "tests/native/renderer_buffer_fallback_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED",
        "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    result = subprocess.run([str(target), variant], cwd=tmp_path, capture_output=True, text=True,
                            timeout=10, check=False, **quiet_spawn_kwargs())
    (tmp_path / "drawer-host.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if variant == "candidate":
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PASS 10 lifetimes: map masks 0..7, mutable, capability change" in result.stdout
    elif variant == "baseline":
        assert result.returncode == 1 and "invalid mapped memcpy" in result.stderr, result.stdout + result.stderr
    else:
        assert result.returncode == 1 and "allocation without live binding" in result.stderr, result.stdout + result.stderr
