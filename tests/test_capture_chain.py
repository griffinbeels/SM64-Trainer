"""Real image/stamp custody across the native renderer and GPU boundary."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def native_build():
    spec = importlib.util.spec_from_file_location("chain_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    if vcvars is None:
        pytest.skip("x86 MSVC unavailable for GPU capture-chain witness")
    return build, vcvars


def compile_host(tmp_path, native_build, mutation=None):
    build, vcvars = native_build
    source = ROOT / "plugin/gfxwrap"
    boundary = (source / "renderer_boundary.cpp").read_text(encoding="utf-8")
    snapshot = (source / "gl_snapshot.cpp").read_text(encoding="utf-8")
    if mutation == "drop_cancelled_attachment":
        marker = "// The attachment still owns its GPU work even after cancellation."
        assert boundary.count(marker) == 1
        boundary = boundary.replace(marker, "r.image = {}; // broken custody")
    elif mutation == "new_pool_bound":
        marker = "ticket.slot < max_slots"
        assert snapshot.count(marker) == 1
        snapshot = snapshot.replace(marker, "ticket.slot < count")
    elif mutation == "early_completion":
        marker = "slot.state.store(tag | returning);"
        assert snapshot.count(marker) == 1
        snapshot = snapshot.replace(marker, "slot.completed_serial.store(ticket.serial); " + marker)
    (tmp_path / "renderer_boundary.cpp").write_bytes(boundary.encode())
    (tmp_path / "gl_snapshot.cpp").write_bytes(snapshot.encode())
    target = tmp_path / "capture_chain_host.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vcvars, flags + ["/std:c++17", "/EHsc", "/DRB_TEST_HOST",
        str(tmp_path / "renderer_boundary.cpp"), str(tmp_path / "gl_snapshot.cpp"),
        str(source / "capture_chain_host.cpp"), f"/I{source}", f"/Fe:{target}",
        f"/Fo{tmp_path}\\", "/link", "bcrypt.lib", *build.LIBS], tmp_path)
    return target


@pytest.mark.parametrize("mutation", [None, "drop_cancelled_attachment", "early_completion", "new_pool_bound"])
def test_image_stamp_custody(tmp_path, native_build, mutation):
    target = compile_host(tmp_path, native_build, mutation)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=20,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    (tmp_path / "witness.txt").write_bytes((result.stdout + result.stderr).encode())
    if mutation:
        assert result.returncode == 1 and "snapshot contract line" in result.stderr, result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "capture-chain passed: exact-pictures=6" in result.stdout
        assert "cancel-owned=yes return-fence-guard=yes old-pool-retirement=yes" in result.stdout


@pytest.fixture(scope="module")
def chain_host(tmp_path_factory, native_build):
    return compile_host(tmp_path_factory.mktemp("capture-chain"), native_build)


def test_quarantined_attachment_cannot_be_discarded(chain_host):
    result = subprocess.run([str(chain_host), "quarantine"], capture_output=True, text=True,
                            timeout=20, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "quarantine attachment retained=yes" in result.stdout
