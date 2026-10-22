"""Optional GL entry points require advertised context capability, not a stub."""
import importlib.util
from pathlib import Path
import subprocess

from sm64_events.core.childproc import quiet_spawn_kwargs
from test_ghost_presentation import _function

ROOT = Path(__file__).resolve().parents[1]


def test_actual_multibind_capability_and_context_cache(tmp_path):
    native = ROOT / "plugin/gfxwrap"
    source = (native / "ghost_dispatch.cpp").read_text(encoding="utf-8")
    (tmp_path / "actual_multibind.inc").write_text(
        _function(source, "bool multibind(const renderer::Identity &identity)"), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("dispatch_compiler", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    target = tmp_path / "ghost_dispatch_capability.exe"
    compiler._cl(compiler.find_vcvars32(), ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{native}", f"/I{tmp_path}", str(ROOT / "tests/native/ghost_dispatch_capability_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=15,
        check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "gl43=scalar arb=enabled gl44=enabled query=once_per_context bounded=yes" in result.stdout
