"""The bridge transport (plugin/gfxwrap/gpu_transport.h): which one a context
selects, how the trainer reads the receipt that names it, and that the test
override can never reach the shipped wrapper.

The hardware witnesses for each transport are test_gpu_bridge,
test_gpu_fidelity, test_gpu_delivery, test_gpu_cadence and
test_ghost_copy_native, run per transport through SM64_TEST_GPU_TRANSPORT.
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.core.diagnostic_privacy import safe_snapshot
from sm64_events.core.error_reporting_privacy import safe_event
from sm64_events.replay import gpuchannel, gpudiagnostics
from sm64_events.replay import gpufielddiagnostics as fields

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"


def _build():
    spec = importlib.util.spec_from_file_location("transport_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    return build


def test_selection_prefers_the_keyed_mutex_then_the_semaphore_then_the_readback(tmp_path):
    """The shipped rule over capability sets, compiled from gpu_render.cpp.
    The AMD field report (keyed mutex missing, memory objects present) now
    selects a transport instead of failing 10031."""
    if sys.platform != "win32":
        pytest.skip("Windows x86 producer compiler")
    build = _build()
    vcvars = build.find_vcvars32()
    assert vcvars, "x86 compiler required"
    target = tmp_path / "select.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vcvars, flags + ["/std:c++17", "/EHsc", f"/I{NATIVE}", str(ROOT / "tests/native/gpu_transport_select_host.cpp"),
                               str(NATIVE / "gpu_render.cpp"), f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *build.LIBS],
              tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=5, check=True, **quiet_spawn_kwargs())
    rows = [line.split("-> ")[1] for line in result.stdout.splitlines()]
    # transport, then unavailable(): 0 none, 1 version_45, 2 memory_object, 3 memory_object_win32, 7 no_transport
    assert rows == [
        "1 7", "2 7", "3 7", "3 7",   # keyed, semaphore, half a semaphore, neither lock
        "2 7", "3 7", "0 7",          # the floor: keyed refused, semaphore refused, everything refused
        "3 7", "3 7",                 # forcing starts the order there; a missing one falls through
        "0 1", "0 2", "0 3", "0 7",   # no GL 4.5, no memory objects, no win32 memory (no LUID), never probed
    ]


def test_the_test_override_never_reaches_a_shipped_build():
    """SM64_TEST_GPU_TRANSPORT is read only under GD_TEST_HOST or
    GD_TRANSPORT_TEST, and the shipped wrapper defines neither."""
    source = (NATIVE / "gpu_delivery.cpp").read_text(encoding="utf-8")
    guarded = re.search(r"#if defined\(GD_TEST_HOST\) \|\| defined\(GD_TRANSPORT_TEST\)\n(.*?)#else\n(.*?)#endif",
                        source, re.S)
    assert guarded, "the override lost its guard"
    assert "SM64_TEST_GPU_TRANSPORT" in guarded[1] and "SM64_TEST_GPU_TRANSPORT" not in guarded[2]
    assert source.count("SM64_TEST_GPU_TRANSPORT") == guarded[0].count("SM64_TEST_GPU_TRANSPORT")
    assert "return GT_TRANSPORT_NONE;}" in guarded[2]
    build = (ROOT / "tools/build_plugin.py").read_text(encoding="utf-8")
    assert "GD_TRANSPORT_TEST" not in build and "GD_TEST_HOST" not in build


def transport_raw(**overrides):
    values = dict(magic=b"SM64GPT1", version=1, size=fields.STARTUP_TRANSPORT.size, epoch=7,
                  capabilities=0x8000003F & ~0x8, transport=2, floor=1, failed=0, failed_error=0)
    values.update(overrides)
    return fields.STARTUP_TRANSPORT.pack(*values.values(), *([0] * 6))


def test_the_receipt_names_what_the_driver_offers_and_the_transport_selected():
    receipt = fields.startup_transport_receipt(transport_raw())
    assert receipt == {"epoch": 7, "transport_floor": 1, "bridge_transport": "semaphore",
                       "transport_capabilities": "gl45,memory_object,memory_object_win32,semaphore,semaphore_win32"}
    refused = fields.startup_transport_receipt(transport_raw(transport=3, floor=3, failed=2, failed_error=0x502))
    assert (refused["bridge_transport"], refused["failed_transport"], refused["failed_transport_error"]) == (
        "readback", "semaphore", 0x502)
    # Not probed yet (a gateway never opened): nothing is claimed about the driver.
    assert fields.startup_transport_receipt(transport_raw(capabilities=0, transport=0)) == {"epoch": 7, "transport_floor": 1}
    assert fields.startup_transport_receipt(transport_raw(capabilities=0x80000000, transport=0))["bridge_transport"] == "none"
    for raw in (b"", bytes(fields.STARTUP_TRANSPORT.size), transport_raw(version=2), transport_raw(magic=b"SM64GPC1")):
        assert fields.startup_transport_receipt(raw) is None


def test_the_receipt_reaches_the_debug_report_and_sentry_as_names_only():
    from test_gpu_field_diagnostics import startup_raw
    receipt = fields.startup_receipt(startup_raw(error=0), None, transport_raw(failed=2, failed_error=0x502))
    assert receipt["bridge_transport"] == "semaphore"
    assert fields.startup_receipt(startup_raw(epoch=8), None, transport_raw()).get("bridge_transport") is None
    kept = safe_snapshot({"capture": {"gpu": {"latest": {"startup": receipt}}}})["capture"]["gpu"]["latest"]["startup"]
    for key in ("bridge_transport", "transport_capabilities", "transport_floor", "failed_transport", "failed_transport_error"):
        assert kept[key] == receipt[key], key
    sneaky = dict(receipt, bridge_transport="C:\\Users\\Private", transport_capabilities="gl45,Private Name")
    dropped = safe_snapshot({"capture": {"gpu": {"latest": {"startup": sneaky}}}})["capture"]["gpu"]["latest"]["startup"]
    assert "bridge_transport" not in dropped and "transport_capabilities" not in dropped
    context = fields.automatic_context({"operation": "startup", "startup": receipt})
    event = safe_event({"message": "GPU recording stopped", "extra": context})
    assert (event["extra"]["bridge_transport"], event["extra"]["failed_transport_error"]) == ("semaphore", 0x502)
    assert "Private" not in json.dumps(dropped)


def test_only_a_layer_older_than_the_transports_or_no_transport_at_all_is_a_verdict():
    """10031 now comes only from an older layer; 10036 means every transport
    failed. Both keep the desktop grab for this Project64, as before."""
    assert "older build" in gpudiagnostics.unsupported_capture_reason(10031)
    assert gpudiagnostics.unsupported_capture_reason(10036)
    assert gpudiagnostics.unsupported_capture_reason(10037) is None  # the ghost copy's, never the run's
    assert gpudiagnostics.native_reason(10037) == "the ghost copy does not record through the readback bridge"
    assert gpudiagnostics.unsupported_capture_reason(10008) is None  # a failed import retries, and demotes natively
    assert gpuchannel.TRANSPORT_NAMES == {1: "keyed_mutex", 2: "semaphore", 3: "readback"}
    header = (NATIVE / "gpu_transport.h").read_text(encoding="utf-8")
    for value, name in gpuchannel.TRANSPORT_NAMES.items():
        assert f"#define GT_TRANSPORT_{name.upper()} {value}u" in header
