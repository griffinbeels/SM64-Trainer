"""Compile production lease/readback code against CPU mocks; never call GL."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("restore_bug", [False, True])
def test_capture_lease_and_framebuffer_local_state(tmp_path, restore_bug):
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
    if restore_bug:
        # Sensitivity check: put the former FBO-selector bug back into the
        # compiled production function, without editing the working source.
        save = "    glGetIntegerv(GL_READ_BUFFER, &read_buffer);\n"
        assert function.count(save) == 1
        function = function.replace(save, "").replace(
            "    glGetIntegerv(GL_PACK_ALIGNMENT", save + "    glGetIntegerv(GL_PACK_ALIGNMENT", 1)
    (tmp_path / "readback_under_test.h").write_text(function, encoding="utf-8")
    target = tmp_path / "capture_cpu.exe"
    build._cl(vcvars, build.COMMON_FLAGS + [
        str(ROOT / "plugin/gfxwrap/capture_cpu_test.c"),
        f"/I{tmp_path}", f"/Fe:{target}", f"/Fo{tmp_path}\\"], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=10,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if restore_bug:
        assert result.returncode == 1 and "state contract failed" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
