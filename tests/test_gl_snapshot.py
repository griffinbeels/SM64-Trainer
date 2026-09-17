"""Real hidden WGL producer/worker witness; no PJ64, server or encoder launch."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def native_build():
    spec = importlib.util.spec_from_file_location("snapshot_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    if vcvars is None:
        pytest.skip("x86 MSVC unavailable for real GPU snapshot witness")
    return build, vcvars


def compile_host(tmp_path, native_build, mutation=None):
    build, vcvars = native_build
    source = (ROOT / "plugin/gfxwrap/gl_snapshot.cpp").read_text(encoding="utf-8")
    if mutation == "omit_copy":
        start = source.index("        // GPU_COPY_BEGIN")
        end = source.index("        // GPU_COPY_END", start)
        source = source[:start] + source[end:]
    elif mutation == "wrong_crop":
        marker = "src.x, src.y,"
        assert source.count(marker) == 2
        source = source.replace(marker, "src.x, src.y + 1,")
    elif mutation == "early_reuse":
        marker = "slot.state.store(tag | returning);"
        assert source.count(marker) == 1
        source = source.replace(marker, "slot.state.store(free);")
    elif mutation == "publish_failed_copy":
        marker = "const bool source_ok = gl.end_capture();"
        assert source.count(marker) == 1
        source = source.replace(marker, "gl.end_capture(); const bool source_ok = true;")
    candidate = tmp_path / "gl_snapshot.cpp"
    candidate.write_bytes(source.encode())
    target = tmp_path / "snapshot_host.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vcvars, flags + ["/std:c++17", "/EHsc", "/DSNAPSHOT_TEST_HOST", str(candidate),
        str(ROOT / "plugin/gfxwrap/gl_snapshot_host.cpp"), f"/I{ROOT / 'plugin/gfxwrap'}",
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    return target


@pytest.mark.parametrize("mutation", [None, "omit_copy", "wrong_crop", "early_reuse"])
def test_owned_gpu_snapshots(tmp_path, native_build, mutation):
    target = compile_host(tmp_path, native_build, mutation)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=20,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    (tmp_path / "witness.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    if mutation:
        expected = "snapshot contract line" if mutation == "early_reuse" else "pixel mismatch"
        assert result.returncode == 1 and expected in result.stderr, result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "pictures=17 bytes=768 full=2000" in result.stdout
        assert "final-without-next-render=yes" in result.stdout


@pytest.fixture(scope="module")
def snapshot_host(tmp_path_factory, native_build):
    return compile_host(tmp_path_factory.mktemp("snapshot-host"), native_build)


@pytest.mark.parametrize("failure", ["producer-fence", "worker-wait", "worker-fence", "copy-error", "restore-error"])
def test_completion_failure_quarantines(snapshot_host, failure):
    result = subprocess.run([str(snapshot_host), failure], capture_output=True, text=True,
                            timeout=20, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"completion-failure quarantined={failure} bytes=768 no-reuse=yes" in result.stdout


def test_bound_texture_fallback(snapshot_host):
    result = subprocess.run([str(snapshot_host), "bound-copy"], capture_output=True, text=True,
                            timeout=20, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "copy_api=bound-texture" in result.stdout
    assert "gpu-witness passed:" in result.stdout


def test_existing_source_error_never_submits(snapshot_host):
    result = subprocess.run([str(snapshot_host), "source-preerror"], capture_output=True,
                            text=True, timeout=20, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "refused-before-copy clean-retirement=yes" in result.stdout


def test_copy_error_oracle_rejects_early_publication(tmp_path, native_build):
    target = compile_host(tmp_path, native_build, "publish_failed_copy")
    result = subprocess.run([str(target), "copy-error"], capture_output=True, text=True,
                            timeout=20, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "result==snapshot::Result::fault" in result.stderr
