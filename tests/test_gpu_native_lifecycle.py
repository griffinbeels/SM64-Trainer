"""Real native CPU decisions; injected frontier is not source-saturation proof."""
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "tools/build_plugin.py").is_file())
SOURCE = Path(__file__).resolve().parents[1] / "plugin/gfxwrap"


@pytest.fixture(scope="module")
def hosts(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("native_lifecycle_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()   # vswhere first: any edition, any year
    assert vc, "the x86 MSVC toolchain (vcvars32.bat) is required"

    def quiet_run(*args, **kwargs):
        kwargs.update(quiet_spawn_kwargs())
        return subprocess.run(*args, **kwargs)

    build.subprocess = SimpleNamespace(run=quiet_run)
    flags = [f for f in build.COMMON_FLAGS if not f.startswith("/std:")]
    dependencies = ["gpu_channel.cpp", "gpu_delivery_context.cpp", "gpu_bridge.cpp",
                    "source_snapshot.cpp", "gl_snapshot.cpp", "gpu_selection.cpp", "renderer_boundary.cpp",
                    "context_lifetime.cpp", "gpu_request.cpp"]
    out = tmp_path_factory.mktemp("native-lifecycle")
    target = out / "native-lifecycle.exe"
    sources = [SOURCE / name for name in (
        "gpu_native_lifecycle_host.cpp", "runtime_control.cpp", "runtime_delivery.cpp",
        *dependencies,
    )]
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{SOURCE}",
              *map(str, sources), f"/Fe:{target}", f"/Fo{out}\\",
              "/link", *build.LIBS, "d3d11.lib", "dxgi.lib"], out)
    return target


def run(host, mode):
    return subprocess.run([str(host), mode], capture_output=True, text=True,
                          timeout=5, **quiet_spawn_kwargs())


@pytest.mark.parametrize("mode", ["recover", "cancel-deferred", "rom-deferred", "quarantine-deferred", "bootstrap",
                                 "image-gap", "record-gap", "epoch-gap", "refusal-gap"])
def test_actual_native_decision(hosts, mode):
    result = run(hosts, mode)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"native lifecycle passed: {mode}"

