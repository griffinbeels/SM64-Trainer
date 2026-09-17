"""Real native occurrence ordering, before any GPU/media publication."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mutation", [None, "array_order", "skip_pending"])
def test_source_occurrence_order(tmp_path, mutation):
    spec = importlib.util.spec_from_file_location("order_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "Source order witness needs x86 MSVC"
    native = ROOT / "plugin/gfxwrap"
    code = (native / "renderer_boundary.cpp").read_bytes().replace(b"\r\n", b"\n")
    if mutation == "array_order":
        marker = b"if ((expected>>3)!=next_delivery) continue;"
        assert code.count(marker) == 1
        code = code.replace(marker, b"if ((expected & TAG_MASK)!=READY) continue;")
    elif mutation == "skip_pending":
        marker = b"if ((expected & TAG_MASK)!=READY) return nullptr;"
        assert code.count(marker) == 1
        code = code.replace(marker, b"if ((expected & TAG_MASK)!=READY) { ++next_delivery; continue; }")
    source = tmp_path / "boundary.cpp"
    source.write_bytes(code)
    exe = tmp_path / "source_order.exe"
    flags = [f for f in build.COMMON_FLAGS if not f.startswith("/std:")]
    build._cl(vcvars, flags + ["/std:c++17", "/EHsc", "/DRB_TEST_HOST", f"/I{native}", str(source),
        str(native / "source_order_host.cpp"), f"/Fe:{exe}", f"/Fo{tmp_path}\\",
        "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    if mutation:
        assert result.returncode == 1 and "source order line" in result.stderr, result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "source order passed:" in result.stdout
