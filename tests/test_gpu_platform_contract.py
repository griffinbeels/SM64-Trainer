"""Native dispatch must refuse mixed/unknown policies before touching a GPU."""
import importlib.util
from pathlib import Path
import subprocess

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_native_platform_contract(tmp_path):
    spec = importlib.util.spec_from_file_location("platform_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    source = ROOT / "plugin/gfxwrap"
    exe = tmp_path / "platform_fault.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(build.find_vcvars64(), flags + ["/std:c++17", "/EHsc", "/WX",
        f"/I{source / 'vendor'}", str(source / "gpu_platform_fault_host.cpp"),
        str(source / "gpu_platform_encoder.cpp"), str(source / "gpu_mf_encoder.cpp"),
        str(source / "gpu_bridge_encoder.cpp"), f"/Fe:{exe}", f"/Fo{tmp_path}\\",
        "/link", *build.ENCODER_LIBS], tmp_path)
    result = subprocess.run([str(exe)], cwd=tmp_path, capture_output=True, text=True,
        timeout=10, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "refuse before device access" in result.stdout
    # Inspect the produced loader contract, not the linker command string:
    # Windows N must still load the NVIDIA helper without Media Foundation.
    import pefile
    helper = build.build_encoder_helper(tmp_path)
    with pefile.PE(str(helper)) as image:
        ordinary = {entry.dll.lower() for entry in image.DIRECTORY_ENTRY_IMPORT}
        delayed = {entry.dll.lower() for entry in image.DIRECTORY_ENTRY_DELAY_IMPORT}
    assert b"mfplat.dll" not in ordinary
    assert b"mfplat.dll" in delayed
