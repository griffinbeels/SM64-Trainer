"""Exercise the actual source command body, without running PJ64."""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def source_build():
    value = os.environ.get("SM64_LINK_WITNESS_SOURCE")
    if not value:
        pytest.skip("requires separately staged pinned LINK source with --boundary")
    source = Path(value).resolve() / "src"
    gl = source / "Graphics/OpenGLContext"
    assert (gl / "renderer_boundary.h").read_bytes() == (ROOT / "plugin/gfxwrap/renderer_boundary.h").read_bytes()
    spec = importlib.util.spec_from_file_location("source_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", "/Gy", "/DUNICODE", "/D_UNICODE", "/DOS_WINDOWS",
        "/DSM64_REPLAY_GL_WITNESS", "/DSM64_REPLAY_SOURCE", f"/I{source}", f"/I{source / 'inc'}", f"/I{gl}"]
    return source, gl, build, vcvars, flags


@pytest.mark.parametrize("mutation", [None, "observe_early", "drop_ticket"])
def test_actual_source_command(tmp_path, source_build, mutation):
    source, gl, build, vcvars, flags = source_build
    common = (source / "common/CommonAPIImpl_common.cpp").read_bytes()
    command = common[common.index(b"class ProcessUpdateScreenCommand :"):common.index(b"class FBReadCommand :")]
    method = common[common.index(b"void PluginAPI::UpdateScreenCaptured"):common.index(b"void PluginAPI::_initiateGFX")]
    method = method[:method.rfind(b"#endif")]
    api = (ROOT / "plugin/gfxwrap/link_source_api.cpp").read_bytes()
    update = api[api.index(b"void __cdecl update("):api.index(b"const rs_api api_v2")]
    if mutation == "observe_early":
        command = command.replace(b"\t\tVI_UpdateScreen();\n", b"")
        command = command.replace(b"\t\treturn true;", b"\t\tVI_UpdateScreen();\n\t\treturn true;")
    elif mutation == "drop_ticket":
        command = command.replace(b"m_capture(request)", b"m_capture{0,0}")
    (tmp_path / "source_update_command.inc").write_bytes(command + method + update)
    target = tmp_path / "source_host.exe"
    build._cl(vcvars, flags + ["/DRSPTHREAD", str(ROOT / "plugin/gfxwrap/renderer_boundary.cpp"),
        str(ROOT / "plugin/gfxwrap/renderer_source_host.cpp"), f"/I{tmp_path}",
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/OPT:REF", "/MANIFEST:EMBED",
        "/MANIFESTUAC:level='asInvoker'", "bcrypt.lib", *build.LIBS], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=15,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    (tmp_path / "witness.txt").write_bytes((result.stdout + result.stderr).encode())
    if mutation:
        assert result.returncode == 1 and "source boundary line" in result.stderr, result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "source boundary passed:" in result.stdout


def test_complete_source_units_compile(tmp_path, source_build):
    source, gl, build, vcvars, flags = source_build
    build._cl(vcvars, flags + ["/c", "/DTXFILTER_LIB", "/DGL_USE_UNIFORMBLOCK", f"/I{source / 'GLideNHQ'}",
        str(source / "common/CommonAPIImpl_common.cpp"), str(gl / "link_source_api.cpp"),
        f"/Fo{tmp_path}\\"], tmp_path)
