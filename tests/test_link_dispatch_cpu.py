"""The real bindings witness breaks and heals over a fake driver: no GPU.

tests/native/link_dispatch_cpu_host.cpp compiles plugin/gfxwrap/link_dispatch.cpp
whole against tests/native/link_fake_gl/ (a stand-in GLFunctions.h and a fake
driver whose true state a scenario moves behind the witness). The witness heals
only from RECOVERABLE sites, asks the driver's answers rather than its own
record, stays bounded under a per-frame error, and keeps the renderer's error
for it. tests/test_link_source_witness.py checks the same paths against a real
driver on the NVIDIA machine.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"
FAKE = ROOT / "tests/native/link_fake_gl"


def build(tmp_path, dispatch=NATIVE / "link_dispatch.cpp"):
    spec = importlib.util.spec_from_file_location("link_cpu_build", ROOT / "tools/build_plugin.py")
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)
    vcvars = tools.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for the CPU witness"
    flags = [flag for flag in tools.COMMON_FLAGS if not flag.startswith("/std:")]
    target = tmp_path / "link_dispatch_cpu.exe"
    tools._cl(vcvars, flags + ["/std:c++17", "/EHsc", f"/I{FAKE}", f"/I{NATIVE}", str(dispatch),
        str(FAKE / "fake_gl.cpp"), str(ROOT / "tests/native/link_dispatch_cpu_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'",
        "kernel32.lib", "user32.lib", "gdi32.lib"], tmp_path)
    return target


def run(target):
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=10,
                            cwd=target.parent, check=False, **quiet_spawn_kwargs())
    return result


def scenarios(result):
    assert result.returncode == 0, result.stdout + result.stderr
    rows = {}
    for line in result.stdout.splitlines():
        name, *pairs = line.split()
        if name == "structural":
            name = "structural " + pairs.pop(0)
        elif name == "fatal_drain":
            name = "fatal_drain " + pairs[0].split("=", 1)[1]
        rows[name] = dict(pair.split("=", 1) for pair in pairs)
    return rows


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("Win32 renderer component")
    return scenarios(run(build(tmp_path_factory.mktemp("link_cpu"))))


def test_a_pending_renderer_error_heals_from_the_drivers_answers(report):
    row = report["first_gateway_error"]
    # The rebuilt witness names what the DRIVER holds: the renderer moved unit,
    # texture, draw framebuffer, viewport, program, blend and scissor while the
    # witness was not recording.
    assert row["rebuilt"] == "1" and row["read"] == "0" and row["selector"] == "0x405"  # GL_BACK
    assert (row["unit"], row["unit3"], row["texture"], row["unit0_known"]) == ("3", "42", "42", "0")
    assert (row["draw"], row["viewport"], row["program"], row["array"]) == ("9", "800x600", "5", "6")
    assert (row["blend"], row["enabled_blend"], row["depth_mask"], row["scissor"]) == (
        "0x302,0x303", "1", "0", "1,2,300,200")
    # One bounded batch of GL 3.3 queries, then the renderer's own read gets
    # the original error exactly once and no longer breaks the witness.
    assert row["queries"] == "27"
    assert (row["renderer_error"], row["then"], row["still"], row["gateway"]) == ("0x502", "0x0", "1", "1")
    assert (row["site_after"], row["attempts"], row["successes"], row["repaired"], row["repaired_error"]) == (
        "0", "1", "1", "14", "0x502")


def test_our_own_close_error_costs_one_rebuild_and_a_drained_layer_costs_nothing(report):
    assert report["close_error"] == {"closed": "0", "site": "15", "healed": "1", "renderer_error": "0x502"}
    assert report["layer_error"] == {"layer": "0", "source": "1", "attempts": "0", "queries": "0"}


@pytest.mark.parametrize("name", ["reinstalled", "other_thread", "bad_window", "lost_layer",
                                  "recoverable_then_other_thread", "not_current", "no_units",
                                  "install_elsewhere", "incomplete"])
def test_structural_sites_never_rebuild_and_ask_the_driver_nothing(report, name):
    row = report[f"structural {name}"]
    assert row["site"] == row["expected"]
    assert (row["rebuilt"], row["again"], row["queries"]) == ("0", "0", "0")


@pytest.mark.parametrize("error", ["0x505", "0x507"])  # GL_OUT_OF_MEMORY, GL_CONTEXT_LOST
def test_a_fatal_driver_error_ends_rebuilding_for_the_context(report, error):
    # The drain finds memory exhausted or the context lost: state is undefined,
    # so neither this attempt nor a later one rebuilds or asks anything, and
    # the renderer still gets the first error the gateway kept for it.
    assert report[f"fatal_drain {error}"] == {"error": error, "first": "0", "later": "0", "queries": "0",
                                              "renderer_error": "0x500", "structural": "1"}


def test_query_errors_stay_ours_and_the_rate_limit_bounds_a_per_frame_error(report):
    # The rebuild's own query error is drained, never handed to the renderer,
    # and the next try waits a second.
    assert report["query_error"] == {"first": "0", "leftover": "0", "soon": "0", "later": "1",
                                     "attempts": "2", "successes": "1", "renderer_error": "0x502"}
    # An error every frame for two seconds at 60 Hz: two query batches, not 120.
    assert report["every_frame"] == {"frames": "120", "attempts": "2", "successes": "2",
                                     "refused": "118", "queries": "54"}


def test_the_default_selector_is_asked_only_while_it_is_bound_for_reading(report):
    assert report["read_framebuffer_bound"] == {"rebuilt": "1", "qualified": "0", "nominal": "1",
                                                "default_known": "0", "soon": "0", "later": "1"}


def test_a_resize_while_invalid_advances_the_rebuilt_drawable_and_vanilla_asks_nothing(report):
    assert report["resize_while_invalid"] == {"healed": "1", "drawable_generation": "+1", "context_generation": "+0"}
    assert report["vanilla"] == {"rebuilt": "0", "queries": "0"}
