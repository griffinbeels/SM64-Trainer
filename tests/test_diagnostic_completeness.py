"""Producer formats survive private diagnostic projection, including no-channel failures."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import pytest

from sm64_events.core.diagnostic_privacy import MAX_SNAPSHOT_BYTES, safe_snapshot
from sm64_events.core.native_diagnostics import NUMBER_FIELDS, technical_events
from sm64_events.replay.gpudiagnostics import CaptureTimings
from sm64_events.replay.gpufielddiagnostics import STARTUP, startup_receipt

BUILD = "a" * 64 + "-gpu-runtime"
BIRTH = int((datetime(2026, 10, 21, 18, tzinfo=timezone.utc).timestamp() + 11644473600) * 10000000)
ROOT = Path(__file__).resolve().parents[1]


def test_video_and_pose_lifetimes_are_visible_as_booleans_without_raw_fallback_text():
    state = {"recording_enabled": False, "gpu_fallback_active": True, "pose_source_owned": True,
             "pose_capture_paused": False, "gpu_fallback": "PrivateDriver C:\\Users\\PrivateName"}
    result = safe_snapshot({"capture": {"recorder": state}})
    assert result["capture"]["recorder"]["gpu_fallback_active"] is True
    assert result["capture"]["recorder"]["pose_source_owned"] is True
    assert result["capture"]["recorder"]["pose_capture_paused"] is False
    assert "Private" not in json.dumps(result)
    assert safe_snapshot(result) == result
    state.update(gpu_fallback_active="PrivateName", pose_source_owned=42, pose_capture_paused=None)
    result = safe_snapshot({"capture": {"recorder": state}})
    assert all(key not in result["capture"]["recorder"] for key in
               ("gpu_fallback_active", "pose_source_owned", "pose_capture_paused"))


def producer_line(path, event, strings):
    """Render the real native printf format without running a renderer or GPU."""
    source = (ROOT / path).read_text(encoding="utf-8")
    event_at = source.index("event=" + event + " ")
    start = source.rfind('"%04u', 0, event_at)
    end = source.index('",', event_at) + 1
    quoted = re.findall(r'"((?:[^"\\]|\\.)*)"', source[start:end])
    template = "".join(json.loads('"' + part + '"') for part in quoted)
    numbers = iter((2026, 10, 21, 18, 1, 0, 0, 42, 7))
    strings = iter(strings)
    def replace(match):
        if match[0].endswith("s"):
            return next(strings)
        value = next(numbers, 1)
        width = re.match(r"%0([0-9]+)", match[0])
        return str(value).zfill(int(width[1])) if width else str(value)
    return re.sub(r"%(?:0?[0-9]+)?(?:\.[0-9]+)?(?:ll|l)?[sudif]", replace, template)


@pytest.mark.parametrize(("path", "event", "strings", "expected"), [
    ("plugin/gfxwrap/diagnostics.h", "callback_progress", (BUILD, "UpdateScreen", "in_flight"),
     {"callback": "UpdateScreen", "state": "in_flight", "age_ticks": 1, "started_qpc": 1}),
    ("plugin/gfxwrap/callback_pacing_log.h", "callback_pacing", (BUILD, "ProcessDList"),
     {"callback": "ProcessDList", "duration_max_ticks": 1, "gap_max_ticks": 1, "cpu_invalid": 1}),
])
def test_real_native_callback_formats_reach_the_automatic_schema(path, event, strings, expected):
    line = producer_line(path, event, strings)
    rows = technical_events(line, producer_pid=42, producer_birth=BIRTH, build_id=BUILD)
    row, = rows
    assert row["event"] == event and row["producer_matches"] is True
    assert expected.items() <= row["fields"].items()
    result = safe_snapshot({"native": {"events": rows}})
    assert result["native"]["events"] == rows
    assert safe_snapshot(result) == result
    assert "tid_role" not in json.dumps(result) and "producer_created" not in json.dumps(result)


def test_progress_state_is_explicit_and_unrecognized_header_roles_are_rejected():
    line = producer_line("plugin/gfxwrap/diagnostics.h", "callback_progress", (BUILD, "UpdateScreen", "no_new_callback"))
    row, = technical_events(line)
    assert row["fields"]["state"] == "no_new_callback"
    pacing = producer_line("plugin/gfxwrap/callback_pacing_log.h", "callback_pacing", (BUILD, "UpdateScreen"))
    assert technical_events(pacing.replace("tid_role=submitter", "tid_role=PrivateAccount")) == []


def test_real_capture_timing_stage_names_and_receipt_availability_survive():
    now = [0.0]
    timings = CaptureTimings(clock=lambda: now[0], wall_clock=lambda: 1000)
    def work():
        now[0] += .001
    for stage in timings.stages:
        timings.measure(stage, work)
    record = {"stage_timings": timings.summary(), "native_receipt_available": False}
    result = safe_snapshot({"capture": {"gpu": {"latest": record}}})
    gpu = result["capture"]["gpu"]["latest"]
    assert set(gpu["stage_timings"]) == set(timings.stages)
    assert gpu["stage_timings"]["sink_prepare"]["total_ms"] == 1
    assert gpu["stage_timings"]["media_setup"]["max_ms"] == 1
    assert gpu["native_receipt_available"] is False
    assert safe_snapshot(result) == result


def test_real_startup_receipt_keeps_progress_and_capability_without_driver_or_request_identity():
    raw = STARTUP.pack(b"SM64GPU1", 1, STARTUP.size, 7, 5, 640, 480, 1, 22, 33, 9, 101, 102,
                       b"PrivateVendor", b"PrivateRenderer", b"4.6.123 PrivateDriver C:\\Users\\PrivateName",
                       b"glAcquireKeyedMutexWin32EXT")
    receipt = startup_receipt(raw)
    result = safe_snapshot({"native": {"control": {"available": True, "gpu_startup": receipt}},
                            "capture": {"gpu": {"latest": {"startup": receipt}}}})
    expected = {"epoch": 7, "load_error": 5, "width": 640, "height": 480, "control_generation": 9,
                "gl_version": "4.6", "missing_entry_point": "glAcquireKeyedMutexWin32EXT", "entry_point_missing": True}
    assert result["native"]["control"]["gpu_startup"] == expected
    assert result["capture"]["gpu"]["latest"]["startup"] == expected
    text = json.dumps(result)
    assert "Private" not in text and "nonce" not in text and "adapter_luid" not in text
    assert safe_snapshot(result) == result


@pytest.mark.parametrize(("width", "height"), [(0, 0), (640, 480)])
def test_accepted_request_without_a_surface_is_distinct_from_surface_arrival(width, height):
    receipt = {"epoch": 4, "load_error": 0, "width": width, "height": height, "control_generation": 3}
    result = safe_snapshot({"native": {"control": {"gpu_startup": receipt}}})
    assert result["native"]["control"]["gpu_startup"] == receipt


def test_direct_snapshot_cannot_smuggle_text_or_identity_through_new_fields():
    line = producer_line("plugin/gfxwrap/diagnostics.h", "callback_progress", (BUILD, "UpdateScreen", "in_flight"))
    row, = technical_events(line)
    row["fields"].update(callback="PrivatePlayer", state="PrivateAccount", phase="UpdateScreen", coverage="PrivateROM",
                         age_ticks="token=PrivateCredential", samples=10 ** 400, cpu_invalid=float("nan"))
    startup = {"load_error": "PrivateError", "width": True, "height": float("inf"), "epoch": -1,
               "control_generation": 2 ** 40, "gl_vendor": "PrivateVendor", "gl_renderer": "PrivateRenderer",
               "gl_version": "PrivateDriver", "missing_entry_point": "glPrivateAccount", "nonce": "PrivateNonce"}
    snapshot = {"native": {"control": {"gpu_startup": startup}, "events": [row]},
                "capture": {"gpu": {"latest": {"startup": startup, "native_receipt_available": "PrivateReceipt",
                            "stage_timings": {"sink_prepare": {"count": "PrivateCount", "max_ms": float("nan")},
                                              "PrivateStage": {"count": 1}}}}}}
    result = safe_snapshot(snapshot)
    text = json.dumps(result, allow_nan=False)
    assert "Private" not in text and "nonce" not in text
    assert result["native"]["control"]["gpu_startup"] == {"entry_point_missing": True}
    assert result["capture"]["gpu"]["latest"]["stage_timings"] == {"sink_prepare": {}}
    assert "native_receipt_available" not in result["capture"]["gpu"]["latest"]
    assert safe_snapshot(result) == result


def test_expanded_native_metrics_stay_inside_the_snapshot_byte_budget():
    line = producer_line("plugin/gfxwrap/diagnostics.h", "callback_progress", (BUILD, "UpdateScreen", "in_flight"))
    row, = technical_events(line)
    row["fields"].update({key: 2 ** 63 + 17 for key in NUMBER_FIELDS})
    result = safe_snapshot({"native": {"events": [row] * 100}})
    assert len(json.dumps(result).encode()) <= MAX_SNAPSHOT_BYTES
    assert result["truncated"] is True
    assert len(result["native"]["events"]) <= 8
    assert safe_snapshot(result) == result


def test_oversized_allowed_scalars_leave_an_explicit_bounded_evidence_failure():
    row = {key: "\u2605" * 1000 for key in ("source", "file", "error_type")}
    result = safe_snapshot({"captured_utc": "2026-10-21T18:01:00Z", "native": {"logs": [row] * 24},
        "capture": {"gpu": {"latest": {key: "x" * 1000 for key in (
            "stage", "reason", "codec", "requested_codec", "encoder_backend", "capture_build", "native_reason",
            "native_result", "native_stage", "native_operation", "native_hresult", "encoder_error", "encoder_result",
            "driver_error", "gpu_vendor_id", "gpu_name", "gpu_driver", "observed_unix_s")}}}})
    assert len(json.dumps(result).encode()) <= MAX_SNAPSHOT_BYTES
    assert result["truncated"] is True and result["error_type"] == "Oversize"
    assert result["captured_utc"] == "2026-10-21T18:01:00Z"
    assert safe_snapshot(result) == result
