"""Production renderer setup caller, using inert CPU shells and fault injection."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from test_ghost_presentation import _function

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("restore_latched_failure", [False, True])
def test_actual_renderer_setup_retries_and_preserves_owned_context(tmp_path, restore_latched_failure):
    native = ROOT / "plugin/gfxwrap"
    text = (native / "ghost_draw.cpp").read_text(encoding="utf-8")
    if restore_latched_failure:
        old = "if (!same_context(identity, owner) || !compositor || !compositor_ready)"
        assert text.count(old) == 1
        text = text.replace(old, "if (!same_context(identity, owner))")
    functions = [_function(text, name) for name in (
        "bool faulted()", "void fault(uint32_t step)",
        "uint32_t result_code(ghost_compositor::Result result)",
        "bool same_context(const renderer::Identity &a, const renderer::Identity &b)",
        "bool same(const renderer::Identity &a, const renderer::Identity &b)",
        "void begin(uint32_t rom, uint32_t config_id, const gc_sample *sample)")]
    (tmp_path / "actual_ghost_draw_begin.inc").write_text("\n".join(functions), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("draw_setup_compiler", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    target = tmp_path / "ghost_draw_setup.exe"
    compiler._cl(compiler.find_vcvars32(), ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{native}", f"/I{ROOT / 'renderer/model'}", f"/I{tmp_path}",
        str(ROOT / "tests/native/ghost_draw_setup_host.cpp"), f"/Fe:{target}", f"/Fo{tmp_path}\\",
        "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=15,
        check=False, **quiet_spawn_kwargs())
    if restore_latched_failure:
        assert result.returncode != 0 and "gateway failure recovers" in result.stderr
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert "gateway=recovered create=recovered observation=retained ineligible=skipped" in result.stdout
