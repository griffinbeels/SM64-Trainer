"""Archive-free PoseRecording ownership with inert native source boundaries.

No emulator, native mapping, sound, media encoder, provider or network is opened.
The actual archive and handoff run where their contracts are being checked.
"""
from pathlib import Path
import sqlite3
import threading

import pytest

from ghost_native_fixture import frame, producer
from sm64_events.ghost.capture import NativeCapture
from sm64_events.ghost.recording import PoseRecording
from sm64_events.memory.layout import US
from sm64_events.multiplayer.source_handoff import SourceHandoff


class LegacySource:
    """Strict legacy signature: an absent live tap must add no keyword."""

    def __init__(self, *, on_batch, producer_pid):
        self.emit, self.pid = on_batch, producer_pid
        self.starts, self.joined = 0, True
        self.calls = []

    def start(self):
        self.starts += 1

    def stop(self, timeout_s=3):
        assert timeout_s == 3
        self.calls.append("join")
        return self.joined


class LiveSource(LegacySource):
    def __init__(self, *, live_source=None, **kwargs):
        super().__init__(**kwargs)
        self.live_source, self.live_changes, self.binding = live_source, [], None

    def start(self):
        super().start()
        if self.live_source is not None:
            self.binding = self.live_source, self.live_source.begin_capture()

    def set_live_source(self, source):
        self.live_changes.append(source)
        self.live_source = source

    def withdraw_live_source(self):
        self.calls.append("withdraw")
        if self.binding is not None:
            self.binding[0].withdraw_capture(self.binding[1])

    def stop(self, timeout_s=3):
        joined = super().stop(timeout_s)
        if joined and self.binding is not None:
            self.binding[0].end_capture(self.binding[1])
            self.binding = None
        return joined


def recording(path=None, factory=LegacySource, **kwargs):
    return PoseRecording(path, factory, producer_pid=123, allowed=lambda: True, **kwargs)


def test_archive_free_owner_never_creates_directory_sqlite_or_archive(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("archive-free ownership requested a filesystem/database resource")
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr("sm64_events.ghost.recording.PoseArchive", forbidden)
    owner = recording()
    assert owner.archive is None and owner.source is None
    owner.start()
    source = owner.source
    source.emit(producer(), (frame(),))
    assert source.pid == 123 and source.starts == 1 and owner.archive is None
    owner.stop()
    assert source.calls == ["join"] and list(tmp_path.iterdir()) == []


def test_default_path_retains_actual_archive_acceptance_and_late_callback_fence(tmp_path):
    owner = recording(tmp_path / "archive" / "poses.sqlite3")
    assert owner.archive.path.exists() and owner.source is None
    owner.start()
    owner.source.emit(producer(), (frame(),))
    assert owner.archive.status()["records"] == 1
    owner.request_stop()
    owner.source.emit(producer(), (frame(2),))
    assert owner.archive.status()["records"] == 1
    owner.stop()
    assert owner.archive.status()["closed"]


def test_archive_gate_stays_independent_and_batch_never_fans_out_live(tmp_path):
    class Tap:
        def observe(self, *_args, **_kwargs):
            pytest.fail("PoseRecording duplicated NativeCapture's live publisher")
    class ArchiveSource(LegacySource):
        def __init__(self, *, live_source, **kwargs):
            super().__init__(**kwargs)
            self.live_source = live_source
    allowed = [False]
    owner = PoseRecording(tmp_path / "poses.sqlite3", ArchiveSource, producer_pid=123,
                          allowed=lambda: allowed[0], live_source=Tap())
    # Use the actual archive callback directly: NativeCapture owns live work.
    owner.start()
    owner.source.emit(producer(), (frame(),))
    assert owner.archive.status()["records"] == 0
    allowed[0] = True
    owner.source.emit(producer(), (frame(),))
    assert owner.archive.status()["records"] == 1
    owner.stop()


def test_archive_free_callback_does_not_even_query_archive_permission():
    owner = PoseRecording(None, LegacySource, producer_pid=123,
                          allowed=lambda: pytest.fail("archive-free callback queried media gate"))
    owner.start()
    owner.source.emit(producer(), (frame(),))
    owner.stop()


def test_initial_live_tap_is_forwarded_only_when_present_and_prestart_changes_are_remembered():
    first = SourceHandoff(bytes(range(16)), stale_after_ns=50)
    latest = SourceHandoff(bytes(range(1, 17)), stale_after_ns=50)
    owner = recording(factory=LiveSource, live_source=first)
    owner.set_live_source(latest)
    owner.start()
    assert owner.source.live_source is latest and owner.source.live_changes == []
    owner.stop()


def test_hotmount_delegates_to_same_native_source_and_never_constructs_a_second_owner():
    owner = recording(factory=LiveSource)
    owner.start()
    source, tap = owner.source, object()
    owner.set_live_source(tap)
    owner.set_live_source(None)
    assert owner.source is source and source.live_changes == [tap, None] and source.starts == 1
    owner.stop()


@pytest.mark.parametrize("archive", [False, True])
def test_duplicate_start_is_rejected_before_a_second_factory_call(tmp_path, archive):
    constructions = []
    def factory(**kwargs):
        constructions.append(kwargs)
        return LegacySource(**kwargs)
    owner = recording(tmp_path / "poses.sqlite3" if archive else None, factory)
    owner.start()
    source = owner.source
    with pytest.raises(RuntimeError, match="already started"):
        owner.start()
    assert len(constructions) == 1 and owner.source is source and source.starts == 1
    owner.stop()
    with pytest.raises(RuntimeError, match="already started"):
        owner.start()


@pytest.mark.parametrize("archive", [False, True])
def test_failed_join_retains_exact_source_and_optional_archive_until_quiet(tmp_path, archive):
    owner = recording(tmp_path / "poses.sqlite3" if archive else None, LiveSource)
    owner.start()
    source, retained = owner.source, owner.archive
    source.joined = False
    owner.request_stop()
    assert source.calls == ["withdraw"]
    with pytest.raises(RuntimeError, match="cleanup pending"):
        owner.stop()
    assert owner.source is source and owner.archive is retained
    if retained is not None:
        assert not retained.status()["closed"]
    source.joined = True
    owner.stop()
    assert source.calls == ["withdraw", "withdraw", "join", "withdraw", "join"]
    if retained is not None:
        assert retained.status()["closed"]


def test_request_stop_withdraws_taken_publication_before_a_stalled_join():
    handoff = SourceHandoff(bytes(range(16)), stale_after_ns=50)
    owner = recording(factory=LiveSource, live_source=handoff)
    owner.start()
    source = owner.source
    binding = source.binding[1]
    handoff.observe(binding, producer(rom_layout=1), (frame(),), received_ns=1)
    publication = handoff.take_latest(now_ns=1)
    entered, release, errors = threading.Event(), threading.Event(), []
    original_stop = source.stop
    def stalled(timeout_s=3):
        entered.set()
        assert release.wait(3)
        return original_stop(timeout_s)
    source.stop = stalled
    def stop():
        try:
            owner.stop()
        except Exception as error:  # noqa: BLE001 - return owned thread errors to the test
            errors.append(error)
    worker = threading.Thread(target=stop, name="owned-pose-stop", daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        assert not handoff.current(publication, now_ns=2) and handoff.take_latest(now_ns=2) is None
        owner.set_live_source(object())
        assert source.live_changes == []  # retiring ownership cannot reattach a new tap
        assert owner.source is source and source.binding is not None
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors and source.binding is None


def test_archive_callback_holds_no_control_lock_and_closes_only_after_join(tmp_path):
    owner = recording(tmp_path / "poses.sqlite3", LiveSource)
    owner.start()
    source, archive = owner.source, owner.archive
    source.joined = False
    entered, release, errors = threading.Event(), threading.Event(), []
    original_ingest = archive.ingest
    def stalled_ingest(*args):
        entered.set()
        assert release.wait(3)
        return original_ingest(*args)
    archive.ingest = stalled_ingest
    def emit():
        try:
            source.emit(producer(), (frame(),))
        except Exception as error:  # noqa: BLE001 - return owned thread errors to the test
            errors.append(error)
    worker = threading.Thread(target=emit, name="owned-pose-archive", daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        owner.request_stop()
        assert source.calls == ["withdraw"] and worker.is_alive()
        with pytest.raises(RuntimeError, match="cleanup pending"):
            owner.stop()
        assert owner.archive is archive and not archive.status()["closed"]
    finally:
        release.set()
        worker.join(3)
        source.joined = not worker.is_alive()
        owner.stop()
    assert not worker.is_alive() and not errors
    assert archive.status()["closed"] and archive.status()["records"] == 1


@pytest.mark.parametrize("archive", [False, True])
def test_partially_started_source_is_retained_without_closing_archive(tmp_path, archive):
    class Partial(LiveSource):
        def start(self):
            self.starts += 1
            self.joined = False
            raise RuntimeError("inert partial startup")
    owner = recording(tmp_path / "poses.sqlite3" if archive else None, Partial)
    with pytest.raises(RuntimeError, match="partial startup"):
        owner.start()
    source = owner.source
    with pytest.raises(RuntimeError, match="cleanup pending"):
        owner.stop()
    assert owner.source is source
    if owner.archive is not None:
        assert not owner.archive.status()["closed"]
    source.joined = True
    owner.stop()
    if owner.archive is not None:
        assert owner.archive.status()["closed"]


def test_failed_factory_cannot_be_retried_into_another_native_source(tmp_path):
    constructions = []
    def failed(**_kwargs):
        constructions.append(True)
        raise ValueError("inert unsupported layout")
    owner = recording(tmp_path / "poses.sqlite3", failed)
    with pytest.raises(ValueError, match="unsupported layout"):
        owner.start()
    assert owner.source is None and not owner.archive.status()["closed"]
    with pytest.raises(RuntimeError, match="already started"):
        owner.start()
    assert constructions == [True]
    owner.stop()
    assert owner.archive.status()["closed"]


def test_hotmount_during_factory_construction_reconciles_latest_desire():
    entered, release, errors = threading.Event(), threading.Event(), []
    first, latest = object(), object()
    def factory(**kwargs):
        entered.set()
        assert release.wait(3)
        source = LiveSource(**kwargs)
        source.start = lambda: setattr(source, "starts", source.starts + 1)
        return source
    owner = recording(factory=factory, live_source=first)
    def start():
        try:
            owner.start()
        except Exception as error:  # noqa: BLE001 - return owned thread errors to the test
            errors.append(error)
    worker = threading.Thread(target=start, name="owned-pose-start", daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        owner.set_live_source(latest)
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors
    assert owner.source.live_changes == [latest] and owner.source.live_source is latest
    owner.stop()


def test_stop_during_factory_retains_startup_and_never_starts_retired_source(tmp_path):
    entered, release, errors = threading.Event(), threading.Event(), []
    def factory(**kwargs):
        entered.set()
        assert release.wait(3)
        return LiveSource(**kwargs)
    owner = recording(tmp_path / "poses.sqlite3", factory)
    def start():
        try:
            owner.start()
        except RuntimeError as error:
            errors.append(str(error))
    worker = threading.Thread(target=start, name="owned-retired-pose-start", daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        with pytest.raises(RuntimeError, match="startup in progress"):
            owner.stop()
        assert not owner.archive.status()["closed"] and owner.source is None
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and errors == ["native pose source startup retired"]
    assert owner.source.starts == 0 and owner.source.calls == ["withdraw"]
    owner.stop()
    assert owner.archive.status()["closed"]


def test_request_stop_prefers_source_acquisition_stop_over_legacy_withdrawal():
    class Stoppable(LiveSource):
        def request_stop(self):
            self.calls.append("request-stop")
    owner = recording(factory=Stoppable)
    owner.start()
    owner.request_stop()
    assert owner.source.calls == ["request-stop"]
    owner.stop()
    assert owner.source.calls == ["request-stop", "request-stop", "join"]


@pytest.mark.parametrize("archive", [False, True])
def test_stop_inside_entered_native_start_prevents_channel_acquisition_and_retains_owner(tmp_path, monkeypatch, archive):
    entered, release, errors = threading.Event(), threading.Event(), []
    opened = []
    class DelayedNativeCapture(NativeCapture):
        def start(self):
            entered.set()
            assert release.wait(3)
            super().start()
    def forbidden_channel(*_args):
        opened.append(True)
        raise AssertionError("retired startup acquired a native channel")
    monkeypatch.setattr("sm64_events.ghost.capture.NativeChannel", forbidden_channel)
    def factory(**kwargs):
        return DelayedNativeCapture(US, **kwargs)
    owner = recording(tmp_path / "poses.sqlite3" if archive else None, factory)
    def start():
        try:
            owner.start()
        except Exception as error:  # noqa: BLE001 - return owned thread errors to the test
            errors.append(error)
    worker = threading.Thread(target=start, name="owned-delayed-native-start", daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        source, retained = owner.source, owner.archive
        owner.request_stop()
        with pytest.raises(RuntimeError, match="startup in progress"):
            owner.stop()
        assert owner.source is source and owner.archive is retained
        if retained is not None:
            assert not retained.status()["closed"]
        release.set()
        worker.join(3)
        assert not worker.is_alive() and not errors
        assert source._cleanup_complete.wait(1), "retired native pump did not finish without acquisition"
        assert source._thread is not None and not source._thread.is_alive() and opened == []
        if retained is not None:
            assert not retained.status()["closed"]  # only parent stop may close this archive
    finally:
        release.set()
        worker.join(3)
        owner.stop()
    assert not worker.is_alive() and owner.source is source
    if retained is not None:
        assert retained.status()["closed"]
