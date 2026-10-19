"""Synthetic reports only: SDK entry points and durable/offline delivery."""
import json
import logging
import threading
import time

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope

from sm64_events.core import error_reporting as reporting
from sm64_events.core.error_reporting_transport import Delivery as HttpDelivery, PersistentTransport

DSN = "https://public@example.invalid/123"


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


@pytest.fixture(autouse=True)
def no_external_delivery(monkeypatch):
    monkeypatch.setattr("sm64_events.core.error_reporting_transport.Delivery", lambda dsn: lambda payload: (False, 0))
    yield
    reporting.shutdown()


def configure(tmp_path):
    reporting.configure(dsn=DSN, release="synthetic@beta", environment="test", directory=tmp_path)
    assert reporting.status()["configured"]


def read_events(tmp_path):
    return [Envelope.deserialize(path.read_bytes()).get_event() for path in tmp_path.glob("*.envelope")]


def test_caught_exception_keeps_type_stack_build_and_real_error_number(tmp_path):
    configure(tmp_path)
    try:
        raise PermissionError(13, "Cannot install at C:\\Users\\SyntheticPrivate\\Project64\\Plugin\\wrapper.dll")
    except PermissionError as error:
        reporting.capture_exception(error, context={"operation": "copy_plugin", "winerror": 5, "path": error.filename})
    wait_for(lambda: len(read_events(tmp_path)) == 1)
    event = read_events(tmp_path)[0]
    assert event["release"] == "synthetic@beta"
    assert event["environment"] == "test"
    assert event["contexts"]["os"]["name"] == "win32"
    assert event["contexts"]["runtime"]["name"] == "cpython"
    error = event["exception"]["values"][0]
    assert error["type"] == "PermissionError"
    assert error["stacktrace"]["frames"][-1]["function"] == "test_caught_exception_keeps_type_stack_build_and_real_error_number"
    assert event["extra"]["winerror"] == 5
    assert "SyntheticPrivate" not in json.dumps(event)


def test_sdk_log_errors_are_collected_and_safe_app_logs_are_breadcrumbs(tmp_path):
    configure(tmp_path)
    logger = logging.getLogger("sm64_events.synthetic")
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        logger.info("Synthetic install started")
        logger.error("Synthetic write failed")
    finally:
        logger.setLevel(previous)
    wait_for(lambda: len(read_events(tmp_path)) == 1)
    event = read_events(tmp_path)[0]
    assert event["logentry"]["formatted"] == "Synthetic write failed"
    assert any(crumb["message"] == "Synthetic install started" for crumb in event["breadcrumbs"]["values"])


def test_sdk_unhandled_hook_captures_before_original_hook(tmp_path, monkeypatch):
    import sys
    configure(tmp_path)
    hook = sys.excepthook
    # SDK's installed hook delegates to pytest's hook; call the SDK event path
    # with its actual unhandled mechanism without terminating this test process.
    from sentry_sdk.integrations.excepthook import _make_excepthook
    delegated = []
    hook = _make_excepthook(lambda *args: delegated.append(args))
    try:
        raise RuntimeError("Synthetic unhandled failure")
    except RuntimeError as error:
        hook(type(error), error, error.__traceback__)
    wait_for(lambda: len(read_events(tmp_path)) == 1)
    assert delegated
    assert read_events(tmp_path)[0]["exception"]["values"][0]["mechanism"]["handled"] is False


def test_privacy_allowlist_excludes_locals_identity_requests_and_attachments(tmp_path):
    configure(tmp_path)
    with sentry_sdk.new_scope() as scope:
        scope.set_user({"email": "synthetic@example.invalid", "id": "private-user"})
        scope.set_extra("database", "raw private database")
        scope.add_attachment(bytes=b"private screenshot", filename="private.png")
        sentry_sdk.capture_event({
            "message": "password=secret-example email synthetic@example.invalid file C:\\Users\\PrivateName\\secret.txt",
            "request": {"headers": {"Authorization": "Bearer private-token"}, "data": "ROM"},
            "contexts": {"device": {"name": "private-machine"}},
            "exception": {"values": [{"type": "Synthetic", "value": "token=private-token", "stacktrace": {
                "frames": [{"filename": "C:\\Users\\PrivateName\\src\\sm64_events\\core\\synthetic.py", "function": "install",
                            "vars": {"rom": "private-rom"}, "context_line": "secret = 'private'", "lineno": 10}]
            }}]},
        })
    wait_for(lambda: len(read_events(tmp_path)) == 1)
    payload = next(tmp_path.glob("*.envelope")).read_text()
    assert not any(private in payload for private in (
        "PrivateName", "private-user", "synthetic@example.invalid", "secret-example", "private-token",
        "private screenshot", "private-machine", "private-rom", "context_line", "Authorization", "attachment", "database",
    ))
    assert "sm64_events/core/synthetic.py" in payload


def test_browser_stack_remains_useful_with_private_urls_removed(tmp_path):
    configure(tmp_path)
    reporting.capture_problem("Synthetic browser error", context={"component": "browser", "stack": "Error: Synthetic\n at install (http://localhost:8064/ui/setup.js:42:4?token=private)", "source": "unhandledrejection"})
    wait_for(lambda: len(read_events(tmp_path)) == 1)
    assert "at install" in read_events(tmp_path)[0]["extra"]["stack"]
    assert "ui/setup.js:42:4" in read_events(tmp_path)[0]["extra"]["stack"]
    assert "private" not in json.dumps(read_events(tmp_path))


def test_disable_clears_queue_and_persists_choice_across_restart(tmp_path):
    configure(tmp_path)
    reporting.capture_problem("Synthetic problem")
    wait_for(lambda: bool(list(tmp_path.glob("*.envelope"))))
    reporting.set_enabled(False)
    assert reporting.status()["pending"] == 0
    assert not list(tmp_path.glob("*.envelope"))
    assert reporting.capture_problem("Disabled problem") is None
    reporting.shutdown()
    configure(tmp_path)
    assert reporting.status()["enabled"] is False
    reporting.capture_exception(RuntimeError("Disabled exception"))
    assert reporting.status()["pending"] == 0


def test_no_dsn_has_no_worker_or_reports_but_remembers_preference(tmp_path):
    reporting.configure(dsn="", release="synthetic", environment="test", directory=tmp_path)
    assert reporting.status()["configured"] is False
    assert reporting.capture_problem("No DSN") is None
    assert not list(tmp_path.glob("*.envelope"))
    reporting.set_enabled(False)
    reporting.configure(dsn=None, release="synthetic", environment="test", directory=tmp_path)
    assert reporting.status()["enabled"] is False
    assert "public" not in json.dumps(reporting.status())


def envelope(message="Synthetic problem"):
    result = Envelope()
    result.add_event({"event_id": "a" * 32, "message": message})
    return result


def test_offline_spool_restarts_and_retries_until_http_acknowledgment(tmp_path):
    first = PersistentTransport({"dsn": DSN}, tmp_path, delivery=lambda payload: (False, 0))
    try:
        first.capture_envelope(envelope())
        wait_for(lambda: first.status()["delivery_errors"] == 1)
        assert len(list(tmp_path.glob("*.envelope"))) == 1
    finally:
        first.kill()
    delivered = []
    second = PersistentTransport({"dsn": DSN}, tmp_path, delivery=lambda payload: (delivered.append(payload) is None, 0))
    try:
        wait_for(lambda: second.status()["sent"] == 1)
        assert second.status()["pending"] == 0
        wait_for(lambda: not list(tmp_path.glob("*.envelope")))
        assert Envelope.deserialize(delivered[0]).get_event()["message"] == "Synthetic problem"
    finally:
        second.kill()


def test_capture_does_not_wait_for_network_and_shutdown_preserves_pending(tmp_path):
    started, release = threading.Event(), threading.Event()
    def blocked_delivery(payload):
        started.set()
        release.wait(2)
        return False, 0
    transport = PersistentTransport({"dsn": DSN}, tmp_path, delivery=blocked_delivery)
    try:
        transport.capture_envelope(envelope())
        assert started.wait(1)
        before = time.monotonic()
        transport.capture_envelope(envelope("Another synthetic problem"))
        assert time.monotonic() - before < 0.1
    finally:
        release.set()
        transport.kill()
    assert len(list(tmp_path.glob("*.envelope"))) == 2


def test_cooldown_and_daily_caps_survive_restart(tmp_path):
    first = PersistentTransport({"dsn": DSN}, tmp_path, delivery=lambda payload: (False, 0))
    for _ in range(20):
        first.capture_envelope(envelope())
    for index in range(20):
        first.capture_envelope(envelope(f"Different synthetic problem {index}"))
    first.kill()
    assert first.status()["accepted"] == 10
    second = PersistentTransport({"dsn": DSN}, tmp_path, delivery=lambda payload: (False, 0))
    try:
        second.capture_envelope(envelope("New problem after restart"))
        assert second.status()["accepted"] == 0
        assert second.status()["dropped"] == 1
    finally:
        second.kill()


def test_retry_backoff_prevents_error_storm_and_handles_sender_exception(tmp_path):
    calls = []
    def unavailable(payload):
        calls.append(payload)
        raise OSError("synthetic network down")
    transport = PersistentTransport({"dsn": DSN}, tmp_path, delivery=unavailable)
    try:
        transport.capture_envelope(envelope())
        wait_for(lambda: len(calls) == 1)
        time.sleep(0.3)
        assert len(calls) == 1
        assert transport.status()["pending"] == 1
    finally:
        transport.kill()


def test_invalid_dsn_never_prevents_startup(tmp_path):
    reporting.configure(dsn="bad-dsn", release="synthetic", environment="test", directory=tmp_path)
    assert reporting.status()["configured"] is False
    assert reporting.status()["configuration_error"] is True
    reporting.capture_problem("Synthetic problem")


def test_spool_bounds_cooldown_expiry_and_monthly_budget(tmp_path, monkeypatch):
    from sm64_events.core import error_reporting_transport as module
    monkeypatch.setattr(module, "DAILY_LIMIT", 100)
    monkeypatch.setattr(module, "MONTHLY_LIMIT", 3)
    clock = [1700000000.0]
    transport = PersistentTransport({"dsn": DSN}, tmp_path, delivery=lambda payload: (False, 0), clock=lambda: clock[0])
    try:
        transport.capture_envelope(envelope())
        transport.capture_envelope(envelope())
        assert transport.status()["accepted"] == 1
        clock[0] += 3601
        transport.capture_envelope(envelope())
        assert transport.status()["accepted"] == 2
        clock[0] += 86400
        transport.capture_envelope(envelope())
        transport.capture_envelope(envelope("Monthly cap reached"))
        assert transport.status()["accepted"] == 3
        assert transport.status()["dropped"] == 2
    finally:
        transport.kill()


def test_oversized_corrupt_stale_spool_files_are_removed(tmp_path):
    import os
    from sm64_events.core.error_reporting_transport import MAX_ENVELOPE_BYTES
    (tmp_path / "oversized.envelope").write_bytes(b"x" * (MAX_ENVELOPE_BYTES + 1))
    (tmp_path / "corrupt.envelope").write_bytes(b"not an envelope")
    stale = tmp_path / "stale.envelope"
    stale.write_bytes(envelope().serialize())
    os.utime(stale, (1, 1))
    transport = PersistentTransport({"dsn": DSN}, tmp_path, delivery=lambda payload: (False, 0))
    try:
        assert transport.status()["pending"] == 0
        assert not list(tmp_path.glob("*.envelope"))
        assert transport.status()["storage_errors"] == 1
    finally:
        transport.kill()


def test_disable_stops_transport_even_if_preference_write_fails(tmp_path, monkeypatch):
    from pathlib import Path
    configure(tmp_path)
    reporting.capture_problem("Synthetic pending")
    wait_for(lambda: bool(list(tmp_path.glob("*.envelope"))))
    original = Path.write_text
    def denied_preference(path, *args, **kwargs):
        if path.name == "preference.tmp":
            raise PermissionError("Synthetic preference denial")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", denied_preference)
    reporting.set_enabled(False)
    assert reporting.status()["preference_error"] is True
    assert reporting.status()["pending"] == 0
    assert reporting._transport.enabled is False
    assert not list(tmp_path.glob("*.envelope"))


def test_http_delivery_requires_acknowledgment_and_honors_sentry_rate_limits(monkeypatch):
    class Response:
        status = 429
        headers = {"Retry-After": "120", "X-Sentry-Rate-Limits": "300:error:organization"}
        def close(self):
            self.closed = True
        def release_conn(self):
            self.released = True
    response = Response()
    calls = []
    class Pool:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return response
    monkeypatch.setattr("sm64_events.core.error_reporting_transport.urllib3.PoolManager", lambda **kwargs: Pool())
    sender = HttpDelivery(DSN)
    assert sender(b"synthetic-envelope") == (False, 300)
    assert calls[0][0:2] == ("POST", "https://example.invalid/api/123/envelope/")
    assert calls[0][2]["redirect"] is False
    assert response.closed and response.released
    response.status = 200
    response.headers = {}
    assert sender(b"synthetic-envelope") == (True, 0)
    response.status = 503
    assert sender(b"synthetic-envelope") == (False, 0)


def test_private_strings_scrubbed_without_losing_type_or_numeric_error():
    from sm64_events.core.error_reporting_privacy import scrub_text
    assert scrub_text('password=' + '"synthetic multiple words" errno=13') == "[credential] errno=13"
    assert scrub_text("Authorization: " + "Bearer " + "synthetic-token") == "[credential]"
    assert scrub_text("network Bearer " + "synthetic-token") == "network [credential]"


def test_capture_never_waits_for_spool_disk_io(tmp_path, monkeypatch):
    from pathlib import Path
    started, release = threading.Event(), threading.Event()
    original = Path.write_bytes
    def slow_write(path, payload):
        started.set()
        release.wait(2)
        return original(path, payload)
    monkeypatch.setattr(Path, "write_bytes", slow_write)
    transport = PersistentTransport({"dsn": DSN}, tmp_path, delivery=lambda payload: (False, 0))
    try:
        transport.capture_envelope(envelope())
        assert started.wait(1)
        before = time.monotonic()
        transport.capture_envelope(envelope("Another synthetic problem"))
        assert time.monotonic() - before < 0.1
    finally:
        release.set()
        transport.kill()
