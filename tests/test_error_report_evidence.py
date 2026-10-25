"""What an automatic error carries besides itself: the redacted Debug report as
an attachment, warning-and-above operational log lines as Sentry Logs, and the
INFO lead-up as breadcrumbs. Synthetic transports only; nothing leaves the test."""
import json
import logging
import socket
import threading
import time

import pytest
from sentry_sdk.envelope import Envelope, Item, PayloadRef

from sm64_events.core import diagnostics, error_reporting as reporting
from sm64_events.core import error_reporting_transport as spool

DSN = "https://public@example.invalid/123"
PRIVATE_LOG = (
    "2026-10-24T22:37:40Z INFO sm64.replay dwm surface capture: 646x553 at C:\\Users\\SyntheticPrivate\\Videos\n"
    "2026-10-24T22:37:41Z WARNING sm64.replay GPU native terminal password=synthetic-secret reason=10034\n"
    "2026-10-24T22:37:42Z INFO sm64.community signed in as SyntheticAccount\n"
)


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


@pytest.fixture
def sent(monkeypatch):
    """Every payload the spool hands to the network, acknowledged at once."""
    payloads = []
    monkeypatch.setattr(spool, "Delivery", lambda dsn: lambda payload: (payloads.append(payload) is None, 0))
    yield payloads
    reporting.shutdown()


def configure(tmp_path):
    reporting.configure(dsn=DSN, release="synthetic@beta", environment="test", directory=tmp_path / "spool")
    assert reporting.status()["configured"]


def report_source(tmp_path, threads=None):
    """The real Debug report builder over a log holding private values."""
    log = tmp_path / "server.log"
    log.write_text(PRIVATE_LOG, encoding="utf-8")
    def build():
        if threads is not None:
            threads.append(threading.current_thread().name)
        return diagnostics.build_report(
            version="synthetic", frozen=True, port=1, data_dir=str(tmp_path), log_file=log,
            ui_log_file=tmp_path / "ui.jsonl", perf_log_file=tmp_path / "perf.jsonl",
            health=lambda: {"status": "ok"}, events=lambda: [], attached=True)
    return build


def items(payloads, kind):
    return [item for payload in payloads for item in Envelope.deserialize(payload).items if item.type == kind]


def reports(payloads):
    return [item.payload.get_bytes().decode("utf-8") for item in items(payloads, "attachment")]


def test_first_error_of_an_issue_carries_the_redacted_debug_report(tmp_path, sent):
    configure(tmp_path)
    threads = []
    unbind = reporting.set_report_source(report_source(tmp_path, threads))
    try:
        for attempt in range(2):  # one issue: same type and culprit, different text
            try:
                raise RuntimeError(f"Synthetic capture failure {attempt}")
            except RuntimeError as error:
                reporting.capture_exception(error)
        wait_for(lambda: len(items(sent, "event")) == 2)
    finally:
        unbind()
    attached, = items(sent, "attachment")
    assert attached.headers["filename"] == "debug-report.md"
    text, = reports(sent)
    assert text.startswith("# SM64 Trainer debug report") and "Attached to an automatic error report." in text
    assert "dwm surface capture: 646x553" in text and "reason=10034" in text
    assert not any(private in text for private in ("SyntheticPrivate", "synthetic-secret", "SyntheticAccount"))
    assert threads == ["automatic-diagnostics"]


def test_the_running_app_attaches_its_own_debug_report_and_unbinds_it_on_exit(tmp_path, sent):
    from fastapi.testclient import TestClient
    from sm64_events.server.app import create_app
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.server.poller import Poller
    from sm64_events.storage.db import Database
    from sm64_events.tracking.service import TrackerService

    class OfflineMemory:
        attached = False
        def attach(self): return False
        def detach(self): pass

    configure(tmp_path)
    broadcaster = Broadcaster()
    service = TrackerService(Database(tmp_path / "t.db"), broadcaster)
    app = create_app(Poller(OfflineMemory(), [], service), broadcaster, service=service)
    with TestClient(app):
        reporting.capture_problem("Synthetic failure inside the running app")
        wait_for(lambda: reports(sent), timeout=5)
    text, = reports(sent)
    assert "Attached to an automatic error report." in text
    assert all(section in text for section in ("## Health", "## Server log", "## Recent game events"))
    assert reporting._report_source is None


def test_each_session_attaches_at_most_five_reports(tmp_path, sent):
    configure(tmp_path)
    unbind = reporting.set_report_source(report_source(tmp_path))
    try:
        for index in range(7):
            reporting.capture_problem(f"Synthetic distinct failure {index}")
        wait_for(lambda: len(items(sent, "event")) == 7)
    finally:
        unbind()
    assert len(reports(sent)) == spool.REPORTS_PER_SESSION == 5


def test_delivery_holds_the_error_until_its_report_is_attached(tmp_path, sent):
    configure(tmp_path)
    def slow_report():
        time.sleep(0.5)
        return "# SM64 Trainer debug report\n\nSynthetic slow evidence\n"
    unbind = reporting.set_report_source(slow_report)
    try:
        before = time.monotonic()
        reporting.capture_problem("Synthetic failure with slow evidence")
        assert time.monotonic() - before < 0.2  # the capturing thread never builds the report
        wait_for(lambda: sent)
    finally:
        unbind()
    assert "Synthetic slow evidence" in reports(sent[:1])[0]


def test_a_delivery_turn_racing_the_report_build_never_sends_the_error_bare(tmp_path):
    building = threading.Event()
    def slow_report():
        building.set()
        time.sleep(0.5)
        return "# SM64 Trainer debug report\n\nSynthetic raced evidence\n"
    delivered = []
    transport = spool.PersistentTransport({"dsn": DSN}, tmp_path, report=slow_report,
                                          delivery=lambda payload: (delivered.append(payload) is None, 0))
    try:
        held = Envelope()
        held.add_event({"event_id": "d" * 32, "message": "Synthetic held failure"})
        transport.capture_envelope(held)
        assert building.wait(2)
        transport._deliver_one()  # the turn that would otherwise find the bare event
        assert delivered == []
        wait_for(lambda: delivered)
    finally:
        transport.kill()
    assert "Synthetic raced evidence" in reports(delivered)[0]


def test_opt_out_during_a_report_build_sends_neither_event_nor_report(tmp_path, sent):
    configure(tmp_path)
    building, release, threads = threading.Event(), threading.Event(), []
    def blocked_report():
        threads.append(threading.current_thread().name)
        building.set()
        release.wait(3)
        return "# SM64 Trainer debug report\n\nSynthetic evidence\n"
    unbind = reporting.set_report_source(blocked_report)
    try:
        reporting.capture_problem("Synthetic failure before opting out")
        assert building.wait(2)
        reporting.set_enabled(False)
        release.set()
        time.sleep(0.6)
        reporting.capture_problem("Synthetic failure while opted out")
        time.sleep(0.6)
    finally:
        release.set()
        unbind()
    assert sent == [] and not list((tmp_path / "spool").glob("*.envelope"))
    assert threads == ["automatic-diagnostics"]


def test_a_queued_report_survives_restart_and_foreign_attachments_do_not(tmp_path):
    report = b"# SM64 Trainer debug report\n\nSynthetic queued evidence\n"
    stored = Envelope(headers={"event_id": "c" * 32})
    stored.add_event({"event_id": "c" * 32, "message": "Synthetic queued failure"})
    stored.add_item(Item(payload=report, type="attachment", content_type="text/plain", filename="debug-report.md"))
    stored.add_item(Item(payload=b"private screenshot", type="attachment", content_type="image/png", filename="private.png"))
    (tmp_path / ("c" * 32 + ".envelope")).write_bytes(stored.serialize())
    delivered = []
    transport = spool.PersistentTransport({"dsn": DSN}, tmp_path,
                                          delivery=lambda payload: (delivered.append(payload) is None, 0))
    try:
        wait_for(lambda: delivered)
    finally:
        transport.kill()
    assert reports(delivered) == [report.decode()]
    assert b"private screenshot" not in delivered[0]


def test_operational_warnings_become_scrubbed_sentry_logs_and_sdk_loggers_never_do(tmp_path, sent):
    configure(tmp_path)
    logging.getLogger("sm64.poller").warning(
        "memory layout mismatch (impossible values read) at C:\\Users\\SyntheticPrivate\\rom.z64 password=synthetic-secret")
    logging.getLogger("sm64.poller").info("Synthetic info stays a breadcrumb")
    for name in ("urllib3.connectionpool", "sentry_sdk.errors", "sm64.community", "sm64.tracker"):
        logging.getLogger(name).warning("Synthetic warning from %s", name)
    reporting._client.flush(timeout=0)
    wait_for(lambda: items(sent, "log"))
    rows = [row for item in items(sent, "log") for row in json.loads(item.payload.get_bytes())["items"]]
    assert [row["body"] for row in rows] == ["memory layout mismatch (impossible values read) at [path]"]
    attributes = rows[0]["attributes"]
    assert attributes["logger.name"]["value"] == "sm64.poller"
    assert attributes["sentry.release"]["value"] == "synthetic@beta"
    assert not set(attributes) & {"server.address", "process.pid", "thread.id", "process.executable.name"}
    payload = b"".join(sent).decode("utf-8")
    assert "SyntheticPrivate" not in payload and "synthetic-secret" not in payload
    assert socket.gethostname() not in payload


def test_the_spool_rebuilds_log_batches_it_did_not_project_itself(tmp_path):
    delivered = []
    transport = spool.PersistentTransport({"dsn": DSN}, tmp_path,
                                          delivery=lambda payload: (delivered.append(payload) is None, 0))
    def row(name, body, **extra):
        attributes = {"logger.name": name, "sentry.severity_number": 13, "server.address": "SyntheticHost", **extra}
        return {"timestamp": time.time(), "level": "warn", "body": body,
                "attributes": {key: {"value": value, "type": "string"} for key, value in attributes.items()}}
    batch = Envelope()
    batch.add_item(Item(type="log", content_type="application/vnd.sentry.items.log+json", payload=PayloadRef(json={
        "version": 2, "items": [row("sm64.replay", "token=synthetic-secret retry", **{"sentry.message.parameter.0": "SyntheticArg"}),
                                row("sm64.replay", "token=synthetic-secret retry"),  # a repeat inside a minute
                                row("sm64.community", "SyntheticAccount")]})))
    try:
        transport.capture_envelope(batch)
        wait_for(lambda: delivered)
    finally:
        transport.kill()
    rows = [row for item in items(delivered, "log") for row in json.loads(item.payload.get_bytes())["items"]]
    assert [row["body"] for row in rows] == ["[credential] retry"]
    assert not any(value in delivered[0] for value in (b"SyntheticHost", b"SyntheticArg", b"SyntheticAccount", b"synthetic-secret"))


def test_breadcrumbs_apply_the_reports_request_rule(tmp_path, sent):
    configure(tmp_path)
    logger = logging.getLogger("uvicorn.access")
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        for status in (200, 304, 500):
            logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:50385", "GET", "/api/session", "1.1", status)
        reporting.capture_problem("Synthetic failing endpoint")
    finally:
        logger.setLevel(previous)
    wait_for(lambda: items(sent, "event"))
    event = json.loads(items(sent, "event")[0].payload.get_bytes())
    access = [crumb["message"] for crumb in event["breadcrumbs"]["values"] if crumb["category"] == "uvicorn.access"]
    assert access == ['[ip]:50385 - "GET [path] HTTP/1.1" 500']


def test_breadcrumbs_keep_the_operational_lead_up_beyond_twenty_lines(tmp_path, sent):
    configure(tmp_path)
    logger = logging.getLogger("sm64.poller")
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        for index in range(30):
            logger.info("Synthetic poll step %d at C:\\Users\\SyntheticPrivate", index)
        reporting.capture_problem("Synthetic poller failure")
    finally:
        logger.setLevel(previous)
    wait_for(lambda: items(sent, "event"))
    event = json.loads(items(sent, "event")[0].payload.get_bytes())
    messages = [crumb["message"] for crumb in event["breadcrumbs"]["values"] if crumb["category"] == "sm64.poller"]
    assert messages == [f"Synthetic poll step {index} at [path]" for index in range(30)]
