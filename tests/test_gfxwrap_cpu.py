"""Compile production lease/readback code against CPU mocks; never call GL."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("source", ["passthrough_cpu_test.c", "diagnostics_cpu_test.c"])
def test_native_inactive_capture_and_nonblocking_diagnostics(tmp_path, source):
    spec = importlib.util.spec_from_file_location("native_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    if vcvars is None:
        pytest.skip("MSVC unavailable for native callback contract check")
    target = tmp_path / "native_cpu.exe"
    build._cl(vcvars, build.COMMON_FLAGS + [
        str(ROOT / "plugin/gfxwrap" / source),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=10,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_profile_lease_and_duration_are_bounded(tmp_path):
    spec = importlib.util.spec_from_file_location("profile_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    if vcvars is None:
        pytest.skip("MSVC unavailable for native profile contract check")
    target = tmp_path / "profile_cpu.exe"
    build._cl(vcvars, build.COMMON_FLAGS + [
        str(ROOT / "plugin/gfxwrap/profile_cpu_test.c"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "kernel32.lib"], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=10,
                            **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("bug", [None, "restore_selector", "drain_errors"])
def test_capture_lease_and_framebuffer_local_state(tmp_path, bug):
    spec = importlib.util.spec_from_file_location("capture_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    if vcvars is None:
        pytest.skip("MSVC unavailable for CPU-only C contract check")
    source = (ROOT / "plugin/gfxwrap/gfxwrap.c").read_text(encoding="utf-8")
    begin = source.index("static void read_front_buffer(")
    end = source.index("/* -- the second capture point", begin)
    function = source[begin:end]
    if bug == "restore_selector":
        # Sensitivity check: put the former FBO-selector bug back into the
        # compiled production function, without editing the working source.
        save = "    glGetIntegerv(GL_READ_BUFFER, &read_buffer);\n"
        assert function.count(save) == 1
        function = function.replace(save, "").replace(
            "    glGetIntegerv(GL_PACK_ALIGNMENT", save + "    glGetIntegerv(GL_PACK_ALIGNMENT", 1)
    elif bug == "drain_errors":
        function = function.replace("    profile_end_stage(PR_GL_RESTORE, measured);",
                                    "    while (glGetError() != GL_NO_ERROR) {}\n"
                                    "    profile_end_stage(PR_GL_RESTORE, measured);")
    (tmp_path / "readback_under_test.h").write_text(function, encoding="utf-8")
    target = tmp_path / "capture_cpu.exe"
    build._cl(vcvars, build.COMMON_FLAGS + [
        str(ROOT / "plugin/gfxwrap/capture_cpu_test.c"),
        f"/I{tmp_path}", f"/Fe:{target}", f"/Fo{tmp_path}\\"], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=10,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if bug:
        assert result.returncode == 1 and "state contract failed" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("bug", ["inactive_gl", "inactive_stamp"])
def test_inactive_callback_probe_rejects_the_previous_hidden_work(tmp_path, bug):
    spec = importlib.util.spec_from_file_location("mutation_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    if vcvars is None:
        pytest.skip("MSVC unavailable for native callback sensitivity check")
    source = (ROOT / "plugin/gfxwrap/gfxwrap.c").read_text(encoding="utf-8")
    if bug == "inactive_gl":
        source = source.replace("EXPORT void CALL ProcessDList(void) {",
                                "EXPORT void CALL ProcessDList(void) {\n    wglGetCurrentContext();")
    else:
        source = source.replace("if (capture_requested()) stamp_pending();", "stamp_pending();")
    (tmp_path / "mutated_wrapper.c").write_text(source, encoding="utf-8")
    host = (ROOT / "plugin/gfxwrap/passthrough_cpu_test.c").read_text(encoding="utf-8")
    (tmp_path / "mutation_host.c").write_text(
        host.replace('#include "gfxwrap.c"', '#include "mutated_wrapper.c"'), encoding="utf-8")
    target = tmp_path / "mutation_cpu.exe"
    build._cl(vcvars, build.COMMON_FLAGS + [
        str(tmp_path / "mutation_host.c"), f"/I{ROOT / 'plugin/gfxwrap'}",
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=10,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 1 and "passthrough contract line" in result.stderr
