"""SourceV3 sidecar ownership without changing the SourceV2 record ABI."""
import importlib.util
import subprocess
from pathlib import Path

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_descriptor_ticket_scope(tmp_path):
    spec = importlib.util.spec_from_file_location("descriptor3_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    assert vc, "SourceV3 witness requires x86 MSVC"
    source = ROOT / "plugin/gfxwrap"
    executable = tmp_path / "source_descriptor3_host.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{source}",
        str(source / "renderer_boundary.cpp"), str(ROOT / "tests/native/source_descriptor3_host.cpp"),
        f"/Fe:{executable}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(executable)], capture_output=True, text=True, cwd=tmp_path,
        timeout=20, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "wrong-thread=refused ABA=refused cancellation=refused" in result.stdout
