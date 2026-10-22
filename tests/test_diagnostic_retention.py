"""Owned debug artifacts expire without touching recordings or link targets."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import threading
import time

import pytest
from sentry_sdk.envelope import Envelope

from sm64_events.core import diagnostic_retention as retention
from sm64_events.core import error_reporting_transport as spool


def artifact(directory, name, *, size=1, age=0, now=200000):
    path = directory / name
    path.write_bytes(b"x" * size)
    os.utime(path, (now - age, now - age))
    return path


def test_report_age_count_and_byte_limits_preserve_neighbor_files(tmp_path):
    old = artifact(tmp_path, "debug-report-old.md", age=86401)
    oversized = artifact(tmp_path, "debug-report-oversized.md", size=retention.REPORT_MAX_BYTES + 1)
    candidates = [artifact(tmp_path, f"debug-report-{i}.md", size=retention.REPORT_MAX_BYTES, age=i)
                  for i in range(5)]
    keep = artifact(tmp_path, "recording.mp4", age=100000)
    database = artifact(tmp_path, "tracker.db", age=100000)
    other = artifact(tmp_path, "user-notes.md", age=100000)
    retention.prune_reports(tmp_path, now=200000)
    assert not old.exists() and not oversized.exists()
    assert all(path.exists() for path in candidates[:3])
    assert all(not path.exists() for path in candidates[3:])
    assert all(path.exists() for path in (keep, database, other))
    assert sum(path.stat().st_size for path in tmp_path.glob("debug-report-*.md")) == 3 * retention.REPORT_MAX_BYTES


def test_report_cleanup_rejects_links_without_following_them(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = artifact(outside, "debug-report-victim.md", age=100000)
    reports = tmp_path / "reports"
    reports.mkdir()
    try:
        (reports / "debug-report-link.md").symlink_to(victim)
        linked_directory = tmp_path / "directory-link"
        linked_directory.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    retention.prune_reports(reports, now=200000)
    retention.prune_reports(linked_directory, now=200000)
    assert victim.read_bytes() == b"x"
    assert (reports / "debug-report-link.md").is_symlink()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction safety")
def test_report_cleanup_rejects_real_junction_ancestors(tmp_path):
    import _winapi
    outside = tmp_path / "outside"
    reports = outside / "reports"
    reports.mkdir(parents=True)
    victim = artifact(reports, "debug-report-victim.md", age=100000)
    junction = tmp_path / "junction"
    try:
        _winapi.CreateJunction(str(outside), str(junction))
    except PermissionError:
        pytest.skip("junction creation unavailable")
    assert junction.is_junction()
    retention.prune_reports(junction / "reports", now=200000)
    assert victim.read_bytes() == b"x"


def test_unavailable_cleanup_cannot_break_export_or_startup(tmp_path, monkeypatch):
    stale = artifact(tmp_path, "debug-report-old.md", age=100000)
    original = Path.unlink
    def denied(path, *args, **kwargs):
        if path == stale:
            raise PermissionError("synthetic denied cleanup")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", denied)
    retention.prune_reports(tmp_path, now=200000)
    retention.prune_reports(tmp_path / "missing", now=200000)
    assert stale.exists()


def test_future_mtimes_stay_count_bounded_and_oversized_existing_logs_are_removed(tmp_path):
    for i in range(6):
        artifact(tmp_path, f"debug-report-future-{i}.md", now=400000)
    retention.prune_reports(tmp_path, now=200000)
    assert len(list(tmp_path.glob("debug-report-*.md"))) == retention.REPORT_KEEP
    oversized = artifact(tmp_path, "sm64_events.log", size=retention.SERVER_LOG_MAX_BYTES + 1)
    backup = artifact(tmp_path, "sm64_events.log.1", size=retention.SERVER_LOG_MAX_BYTES + 1)
    retention.prune_debug_logs(tmp_path, now=200000)
    assert not oversized.exists() and not backup.exists()


def test_startup_discards_oversized_legacy_perf_files(tmp_path):
    logs, data = tmp_path / "logs", tmp_path / "data"
    logs.mkdir()
    data.mkdir()
    oversized = [artifact(data, name, size=retention.PERF_LOG_MAX_BYTES + 1) for name in
                 ("perf_log.jsonl", "perf_log.jsonl.prev")]
    retention.prune_debug_logs(logs, data_directory=data, now=200000)
    assert all(not path.exists() for path in oversized)


def test_startup_debug_cleanup_has_a_fixed_scope(tmp_path):
    logs = tmp_path / "logs"
    data = tmp_path / "data"
    logs.mkdir()
    data.mkdir()
    stale = [artifact(logs, name, age=100000) for name in ("sm64_events.log", "sm64_events.log.1")]
    stale += [artifact(data, name, age=100000) for name in
              ("ui_log.jsonl", "perf_log.jsonl", "perf_log.jsonl.prev")]
    unrelated = [artifact(data, name, age=100000) for name in ("tracker.db", "events.jsonl", "personal.log")]
    fresh = artifact(logs, "unrelated.log", age=100000)
    retention.prune_debug_logs(logs, data_directory=data, now=200000)
    assert all(not path.exists() for path in stale)
    assert all(path.exists() for path in [*unrelated, fresh])


def test_logging_rotates_oversized_sessions_without_growing_backups(tmp_path, monkeypatch):
    from sm64_events.core import logging_setup
    monkeypatch.setattr("sm64_events.core.reporting_setup.initialize_reporting", lambda: None)
    isolated = logging.getLogger("isolated-retention")
    monkeypatch.setattr(logging_setup.logging, "getLogger", lambda: isolated)
    logging_setup.configure_logging(tmp_path)
    try:
        handler, = [h for h in isolated.handlers if isinstance(h, RotatingFileHandler)]
        assert handler.maxBytes == retention.SERVER_LOG_MAX_BYTES and handler.backupCount == 1
        handler.setFormatter(logging.Formatter("%(message)s"))
        for _ in range(130):
            isolated.info("x" * 20000)
        files = list(tmp_path.glob("sm64_events.log*"))
        assert len(files) == 2
        assert all(path.stat().st_size <= retention.SERVER_LOG_MAX_BYTES for path in files)
    finally:
        for handler in isolated.handlers:
            handler.close()


def test_explicit_log_fixture_does_not_touch_real_report_directory(tmp_path, monkeypatch):
    from sm64_events.core import logging_setup
    monkeypatch.setattr("sm64_events.core.reporting_setup.initialize_reporting", lambda: None)
    root = logging.getLogger("isolated-startup-retention")
    monkeypatch.setattr(logging_setup.logging, "getLogger", lambda: root)
    calls = []
    monkeypatch.setattr(logging_setup, "data_root", lambda: tmp_path)
    monkeypatch.setattr(logging_setup, "prune_reports", lambda path: calls.append(path))
    logging_setup.configure_logging(tmp_path)
    assert calls == []
    for handler in root.handlers:
        handler.close()
    root.handlers.clear()
    monkeypatch.setattr(logging_setup, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(logging_setup, "diagnostics_dir", lambda: tmp_path / "reports")
    logging_setup.configure_logging()
    assert calls == [tmp_path / "reports"]
    for handler in root.handlers:
        handler.close()


def envelope(message="synthetic retention"):
    item = Envelope()
    item.add_event({"event_id": "b" * 32, "message": message})
    return item


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def test_active_offline_spool_expires_without_process_restart(tmp_path):
    clock = [200000.0]
    transport = spool.PersistentTransport({"dsn": "https://public@example.invalid/123"}, tmp_path,
        delivery=lambda payload: (False, 86400), clock=lambda: clock[0])
    try:
        transport.capture_envelope(envelope())
        wait_for(lambda: len(list(tmp_path.glob("*.envelope"))) == 1)
        clock[0] += 86401
        transport.wake.set()
        wait_for(lambda: transport.status()["pending"] == 0 and not list(tmp_path.glob("*.envelope")))
        assert transport.status()["expired"] == 1
    finally:
        transport.kill()


def test_delayed_persistence_and_restart_do_not_extend_enqueue_age(tmp_path):
    clock = [200000.0]
    transport = spool.PersistentTransport({"dsn": "https://public@example.invalid/123"}, tmp_path,
        delivery=lambda payload: (False, 86400), clock=lambda: clock[0])
    try:
        with transport.persist_lock:
            transport.capture_envelope(envelope())
            clock[0] += 86000
        wait_for(lambda: len(list(tmp_path.glob("*.envelope"))) == 1)
    finally:
        transport.kill()
    saved, = tmp_path.glob("*.envelope")
    assert saved.stat().st_mtime == 200000
    clock[0] += 401
    restarted = spool.PersistentTransport({"dsn": "https://public@example.invalid/123"}, tmp_path,
        delivery=lambda payload: (False, 0), clock=lambda: clock[0])
    try:
        assert restarted.status()["pending"] == 0
        assert not list(tmp_path.glob("*.envelope"))
    finally:
        restarted.kill()


def test_expired_incoming_never_gets_written_at_shutdown(tmp_path):
    clock = [200000.0]
    started, release = threading.Event(), threading.Event()
    def blocked(payload):
        started.set()
        release.wait(2)
        return False, 0
    transport = spool.PersistentTransport({"dsn": "https://public@example.invalid/123"}, tmp_path,
        delivery=blocked, clock=lambda: clock[0])
    try:
        transport.capture_envelope(envelope())
        assert started.wait(1)
        transport.capture_envelope(envelope("second synthetic retention"))
        clock[0] += 86401
    finally:
        release.set()
        transport.kill()
    assert not list(tmp_path.glob("*.envelope"))


def test_startup_removes_only_stale_abandoned_envelope_temporaries(tmp_path):
    old = artifact(tmp_path, "a" * 32 + ".tmp", age=86401)
    fresh = artifact(tmp_path, "b" * 32 + ".tmp")
    settings = [artifact(tmp_path, name, age=100000) for name in ("preference.tmp", "budget.tmp", "personal.tmp")]
    clock = [200000.0]
    transport = spool.PersistentTransport({"dsn": "https://public@example.invalid/123"}, tmp_path,
        delivery=lambda payload: (False, 0), clock=lambda: clock[0])
    try:
        assert not old.exists() and fresh.exists()
        assert all(path.exists() for path in settings)
        clock[0] += 86401
        transport.wake.set()
        wait_for(lambda: not fresh.exists())
        assert all(path.exists() for path in settings)
    finally:
        transport.kill()


def test_fresh_abandoned_temporaries_share_the_disk_budget(tmp_path):
    for index in range(25):
        artifact(tmp_path, f"{index:032x}.tmp", size=spool.MAX_ENVELOPE_BYTES)
    untouched = artifact(tmp_path, "personal.tmp", size=1)
    transport = spool.PersistentTransport({"dsn": "https://public@example.invalid/123"}, tmp_path,
        delivery=lambda payload: (False, 0), clock=lambda: 200000)
    try:
        survivors = [path for path in tmp_path.glob("*.tmp") if path != untouched]
        assert len(survivors) <= spool.MAX_PENDING
        assert sum(path.stat().st_size for path in survivors) <= spool.MAX_SPOOL_BYTES
        assert untouched.read_bytes() == b"x"
    finally:
        transport.kill()
