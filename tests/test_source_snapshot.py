"""SourceV2 descriptor to real owned FRONT texture; no PJ64 or recorder."""
import importlib.util
import subprocess
from pathlib import Path

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_source_front_snapshot(tmp_path):
    spec = importlib.util.spec_from_file_location("source_snapshot_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "SourceV2 witness needs x86 MSVC"
    flags = [f for f in build.COMMON_FLAGS if not f.startswith("/std:")]
    native = ROOT / "plugin/gfxwrap"
    exe = tmp_path / "source_snapshot.exe"
    build._cl(vcvars, flags + ["/std:c++17", "/EHsc", f"/I{native}",
        str(native / "gl_snapshot.cpp"), str(native / "source_snapshot.cpp"),
        str(native / "source_snapshot_host.cpp"), f"/Fe:{exe}", f"/Fo{tmp_path}\\",
        "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "source snapshot passed: FRONT overwrite and retained crop" in result.stdout
