"""Privacy and useful failure evidence through the actual SDK/export boundaries."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import threading
import time
from types import SimpleNamespace as NS

import pytest
from sentry_sdk.envelope import Envelope

from sm64_events.core import error_reporting as reporting
from sm64_events.core.diagnostic_privacy import safe_snapshot
from sm64_events.core.diagnostic_snapshot import DiagnosticSnapshot
from sm64_events.core.error_reporting_privacy import safe_event, scrub_text
from sm64_events.core.native_diagnostics import NativeDiagnostics, bounded_tail, technical_events

BUILD = "a" * 64 + "-gpu-runtime"
BIRTH = int((datetime(2026, 10, 21, 18, tzinfo=timezone.utc).timestamp() + 11644473600) * 10000000)


def native_line(*, pid=42, stamp="2026-10-21T18:01:00.000Z", event="gpu_failure", details="reason=10005 detail=4 phase=wait"):
    return f"{stamp} pid={pid} tid=7 build={BUILD} event={event} {details}\n"


@dataclass
class ControlStatus:
    producer_pid: int = 42
    producer_created_lo: int = BIRTH & 0xffffffff
    producer_created_hi: int = BIRTH >> 32
    build_id: str = BUILD
    state: int = 2
    reason: int = 0


class Control:
    def __init__(self):
        self.closes = 0

    def status(self):
        return ControlStatus()

    def close(self):
        self.closes += 1


def test_recognized_native_records_never_retain_arbitrary_details():
    raw = native_line(details='reason=10005 detail=4 phase=wait author=PrivateActor password="private-password" path=C:\\Users\\PrivateName\\ROM.z64')
    rows = technical_events(raw, producer_pid=42, producer_birth=BIRTH, build_id=BUILD)
    assert rows[0]["producer_matches"] is True
    assert rows[0]["fields"] == {"reason": 10005, "detail": 4, "phase": "wait"}
    assert "Private" not in json.dumps(rows) and "password" not in json.dumps(rows)
    assert technical_events(native_line(event="message", details="private freeform text")) == []


@pytest.mark.parametrize("line", [native_line(pid=43), native_line(stamp="2026-10-21T17:59:00.000Z")])
def test_native_receipt_rejects_wrong_process_or_reused_pid_history(line):
    row, = technical_events(line, producer_pid=42, producer_birth=BIRTH, build_id=BUILD)
    assert row["identity_bound"] is True and row["producer_matches"] is False


def test_native_reader_is_passive_bounded_and_keeps_missing_sources_explicit(tmp_path):
    (tmp_path / "sm64_trainer_gfx.log").write_text(native_line(), encoding="utf-8")
    control = Control()
    reader = NativeDiagnostics(control_factory=lambda: control)
    result = reader.snapshot({"plugin_dir": str(tmp_path)})
    assert result["control"]["available"] and control.closes == 1
    assert result["events"][0]["fields"]["reason"] == 10005
    assert any(not row["available"] and row["error_type"] == "FileNotFoundError" for row in result["logs"])
    reader.close()
    absent = NativeDiagnostics(control_factory=lambda: pytest.fail("cannot discover an unobserved installation"))
    assert absent.snapshot()["error_type"] == "InstallationNotObserved"


def test_file_read_is_bounded_before_decoding_and_discards_a_torn_prefix(tmp_path):
    path = tmp_path / "native.log"
    path.write_bytes(b"private prefix" * 100 + b"\n" + native_line().encode())
    tail, offset = bounded_tail(path, limit=300)
    assert offset > 0 and "private prefix" not in tail and "event=gpu_failure" in tail


def test_failed_passive_close_retains_owner_and_blocks_replacement(tmp_path):
    control = Control()
    refused = [True]
    calls = []
    def close():
        if refused[0]:
            raise OSError("simulated CloseHandle failure")
    control.close = close
    reader = NativeDiagnostics(control_factory=lambda: (calls.append(1) or control))
    layer = {"plugin_dir": str(tmp_path)}
    assert reader.snapshot(layer)["control"]["error_type"] == "CleanupPending"
    assert reader._pending is control
    assert reader.snapshot(layer)["control"]["error_type"] == "CleanupPending" and len(calls) == 1
    refused[0] = False
    reader.close()
    assert reader._pending is None


def test_partial_process_probe_inside_status_keeps_both_owners(tmp_path):
    from sm64_events.replay.ownedclose import CleanupPending
    control, child = Control(), Control()
    refused = [True]
    def child_close():
        if refused[0]:
            raise OSError("simulated process handle release failure")
    def status():
        raise CleanupPending(child, "process identity query cleanup")
    child.close, control.status = child_close, status
    reader = NativeDiagnostics(control_factory=lambda: control)
    assert reader.snapshot({"plugin_dir": str(tmp_path)})["control"]["error_type"] == "CleanupPending"
    assert reader._pending.owners == [child, control] and control.closes == 0
    refused[0] = False
    reader.close()
    assert reader._pending is None and control.closes == 1


def sample_snapshot():
    return {"captured_utc": "2026-10-21T18:01:00+00:00",
        "capture": {"recorder": {"recording": False, "pictures_fed": 0, "window_title": "PrivateWindow"},
                    "gpu": {"latest": {"stage": "channel", "native_reason": 0, "capture_build": BUILD,
                              "stage_timings": {"channel": {"total_ms": 5000, "count": 1}}}}},
        "ghost": {"enabled": True, "has_target": True, "label": "PrivatePlayer",
                  "native": {"state": 5, "reason": 8, "displayed": False, "clip_path": "private.z64"}},
        "setup": {"state": "needs_restart", "configuration_consistent": False, "pj64_dir": "C:\\Users\\PrivateName", "runtime": {
                  "checks": {"pictures": False, "game": True}, "rom": {"region": "jp", "name": "PrivateROM"}}},
        "resources": {"rss_mib": 300, "machine": {"top_cpu": ["PrivateProgram"]}},
        "native": {"events": technical_events(native_line()), "raw": "PrivateLog"}}


def test_snapshot_schema_is_idempotent_and_excludes_private_nested_fields():
    first = safe_snapshot(sample_snapshot())
    assert safe_snapshot(first) == first
    assert first["ghost"]["native"]["reason"] == 8
    assert first["capture"]["gpu"]["latest"]["stage_timings"]["channel"]["total_ms"] == 5000
    assert "Private" not in json.dumps(first) and "private.z64" not in json.dumps(first)
    assert len(json.dumps(first).encode()) < 16 * 1024


@pytest.mark.parametrize("value", [None, [], "private", 42])
def test_malformed_nested_sources_do_not_discard_the_original_error(value):
    raw = {name: value for name in ("capture", "ghost", "setup", "pipeline", "resources", "native")}
    event = safe_event({"message": "Technical failure", "extra": {"diagnostics": raw}})
    assert event["message"] == "Technical failure" and event["extra"]["diagnostics"]["schema"] == 1


def test_one_failed_source_does_not_hide_other_sources_or_leak_its_exception():
    def denied():
        raise OSError("C:\\Users\\PrivateName password=private-password")
    native = NS(snapshot=lambda layer: {"available": False}, close=lambda: None)
    snapshot = DiagnosticSnapshot(capture=denied, ghost=lambda: {"enabled": True}, native=native)
    result = snapshot.collect()
    assert result["sources"]["capture"] == {"available": False, "error_type": "OSError"}
    assert result["ghost"]["enabled"] is True and "Private" not in json.dumps(result)
    snapshot._lock.acquire()
    try:
        assert snapshot.collect()["error_type"] == "Busy"
    finally:
        snapshot._lock.release()
        snapshot.close()


@pytest.mark.parametrize("private", ['{"token":"private-short"}', "192.0.2.41", "[2001:db8::41]", "/opt/private/customer.txt"])
def test_common_identity_and_credential_forms_are_redacted(private):
    text = scrub_text("failure " + private)
    assert "private-short" not in text and "192.0.2.41" not in text
    assert "2001:db8" not in text and "customer.txt" not in text


@pytest.fixture
def reporter(tmp_path, monkeypatch):
    monkeypatch.setattr("sm64_events.core.error_reporting_transport.Delivery", lambda dsn: lambda payload: (False, 0))
    reporting.configure(dsn="https://public@example.invalid/123", release="synthetic-diagnostics", environment="test", directory=tmp_path)
    yield tmp_path
    reporting.set_diagnostic_source(None)
    reporting.shutdown()


def captured_event(path):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        reports = list(path.glob("*.envelope"))
        if reports:
            return Envelope.deserialize(reports[0].read_bytes()).get_event()
        time.sleep(0.01)
    pytest.fail("No persisted event")


def test_real_sdk_carries_current_fault_evidence_through_both_scrubbers(reporter):
    unbind = reporting.set_diagnostic_source(sample_snapshot)
    reporting.capture_problem("Synthetic capture startup failure")
    event = captured_event(reporter)
    assert event["extra"]["diagnostics"]["ghost"]["native"]["reason"] == 8
    assert event["extra"]["diagnostics"]["setup"]["configuration_consistent"] is False
    assert "Private" not in json.dumps(event)
    unbind()


def test_source_refusal_detail_survives_actual_sdk_and_transport_projection(reporter):
    sample = sample_snapshot()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    sample["native"]["events"] = technical_events(native_line(stamp=stamp, details="reason=10034 detail=1397882882"))
    unbind = reporting.set_diagnostic_source(lambda: sample)
    try:
        reporting.capture_problem("Synthetic source admission refusal")
        event = captured_event(reporter)
        fields = event["extra"]["diagnostics"]["native"]["events"][0]["fields"]
        assert fields == {"reason": 10034, "detail": 0x53520002}
    finally:
        unbind()


def test_direct_transport_and_reloaded_envelopes_cannot_bypass_the_schema(tmp_path):
    from sm64_events.core.error_reporting_transport import PersistentTransport
    raw = Envelope(headers={"event_id": "b" * 32})
    raw.add_event({"event_id": "b" * 32, "message": "Technical failure", "user": {"email": "private@example.invalid"},
                   "extra": {"diagnostics": {**sample_snapshot(), "native_log_tails": ["PrivateRawLog"]}}})
    path = tmp_path / ("b" * 32 + ".envelope")
    path.write_bytes(raw.serialize())
    sent = []
    transport = PersistentTransport({"dsn": "https://public@example.invalid/123"}, tmp_path,
                                    delivery=lambda payload: (sent.append(payload) is None and False, 0))
    try:
        deadline = time.monotonic() + 3
        while not sent and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sent
        output = sent[0].decode()
        assert "Private" not in output and "private@example.invalid" not in output
        assert "native_log_tails" not in output and '"reason":8' in output.replace(" ", "")
    finally:
        transport.kill()
    assert "Private" not in path.read_text(encoding="utf-8")


def test_gameplay_machine_inventory_and_opted_out_breadcrumbs_never_reappear(reporter):
    loggers = [logging.getLogger(name) for name in ("sm64.events", "sm64.procmem", "sm64.replay")]
    previous = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.INFO)
    try:
        loggers[0].info("PrivateGameplayWire")
        loggers[1].info("PrivateUnrelatedProgram")
        loggers[2].info("PrivateBeforeOptOut")
        reporting.set_enabled(False)
        loggers[2].info("PrivateDuringOptOut")
        reporting.set_enabled(True)
        loggers[2].info("Capture startup requested")
        reporting.capture_problem("Synthetic technical failure")
        serialized = json.dumps(captured_event(reporter))
        assert "Private" not in serialized and "Capture startup requested" in serialized
    finally:
        for logger, level in zip(loggers, previous, strict=True):
            logger.setLevel(level)


def test_persistent_worker_cannot_replay_breadcrumbs_across_consent_changes(reporter):
    ready, disabled, after_disabled, resumed = (threading.Event() for _ in range(4))
    logger = logging.getLogger("sm64.replay")
    previous = logger.level
    logger.setLevel(logging.INFO)
    def worker():
        logger.info("PrivateWorkerBeforeOptOut")
        ready.set()
        disabled.wait(3)
        logger.info("PrivateWorkerDuringOptOut")
        after_disabled.set()
        resumed.wait(3)
        logger.info("Capture worker resumed")
        reporting.capture_problem("Technical worker failure after consent cycle")
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert ready.wait(3)
        reporting.set_enabled(False)
        disabled.set()
        assert after_disabled.wait(3)
        reporting.set_enabled(True)
        resumed.set()
        thread.join(3)
        serialized = json.dumps(captured_event(reporter))
        assert "PrivateWorker" not in serialized and "Capture worker resumed" in serialized
    finally:
        disabled.set()
        resumed.set()
        thread.join(3)
        logger.setLevel(previous)
    assert not thread.is_alive()


def test_native_history_does_not_backfill_the_disabled_period(monkeypatch):
    since = datetime(2026, 10, 21, 18, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(reporting, "_consent_since", since)
    snapshot = sample_snapshot()
    snapshot["native"]["events"] += technical_events(native_line(stamp="2026-10-21T17:59:00.000Z"))
    snapshot["capture"]["gpu"]["first_failure"] = {"observed_unix_s": since - 1, "reason": "old fault"}
    result = reporting._current_history(snapshot)
    assert len(result["native"]["events"]) == 1
    assert result["capture"]["gpu"]["first_failure"] == {}


def test_consent_change_during_scrubbing_cannot_relabel_an_old_breadcrumb(reporter, monkeypatch):
    def racing_scrub(crumb, hint):
        reporting.set_enabled(False)
        reporting.set_enabled(True)
        return {"category": "sm64.replay", "message": "PrivateOldBreadcrumb"}
    monkeypatch.setattr(reporting, "safe_breadcrumb", racing_scrub)
    assert reporting._before_breadcrumb({"category": "sm64.replay"}, None) is None


def test_silent_native_playback_fault_reports_once_and_normal_waits_do_not(monkeypatch):
    from sm64_events.ghost.capture import NativeCapture
    capture = NativeCapture.__new__(NativeCapture)
    capture._reported_native_fault = None
    errors = []
    monkeypatch.setattr(reporting, "capture_problem", lambda message, **kwargs: errors.append((message, kwargs)))
    for reason in (0, 4, 5, 10):
        capture._report_native_fault(5, reason)
    assert not errors
    capture._report_native_fault(5, 8)
    capture._report_native_fault(5, 8)
    assert len(errors) == 1 and errors[0][1]["context"]["native_reason"] == 8
