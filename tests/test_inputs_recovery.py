"""Reset garbage must not poison an input chunk or stop course tracking."""
import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from test_app import OfflineMemory
from test_inputs_sampler import ScriptedMemory
from test_input_track_settles import _NullBroadcaster, event
from test_poller_cadence import CountingReader, CountingDetector, NullBroadcaster
from test_poller import snap
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.sampler import InputSampler
from sm64_events.inputs.store import ChunkWriter
from sm64_events.memory.layout import US
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

AT = "2026-09-14T00:33:50+00:00"


def test_reset_garbage_is_a_gap_and_valid_samples_still_persist(tmp_path):
    db = Database(tmp_path / "inputs.db")
    session = db.insert_session(AT)
    writer = ChunkWriter(db.inputs, session, clock=lambda: AT)
    activity = []
    script = [(96000, 0, 0, -128, 127), (96, 0, 0, 32767, -32768),
              (97, 0, 0, 128, 0), (98, 0, 0, 0, -129),
              (99, 0, 0, 84, -84), (100, 0, 0, 0, 0)]
    sampler = InputSampler(ScriptedMemory(script), US, writer.add,
                           clock=lambda: AT, on_activity=lambda: activity.append(1))
    for _ in script:
        sampler.sample()
    sampler.flush()
    writer.close()
    chunks = db.inputs.chunks_between(AT, AT)
    assert [n for chunk in chunks for n, _ in chunk.frames] == [96000, 99, 100]
    assert chunks[0].frames[0][1].stick_x == -128
    assert chunks[0].frames[0][1].stick_y == 127
    assert chunks[0].observations[0].source_id != chunks[-1].observations[0].source_id
    assert len(activity) == 2  # impossible input never wakes recording
    assert sampler.health()["invalid_samples"] == 3
    db.close()


def test_writer_rejects_invalid_axes_before_they_enter_its_buffer(tmp_path):
    db = Database(tmp_path / "inputs.db")
    session = db.insert_session(AT)
    writer = ChunkWriter(db.inputs, session, clock=lambda: AT)
    writer.add(1, InputFrame(0, 0, 84, -84))
    with pytest.raises(ValueError, match="stick"):
        writer.add(2, InputFrame(0, 0, 128, 0))
    writer.add(3, InputFrame(0, 0, -128, 127))
    writer.close()
    assert [n for n, _ in db.inputs.frames_between(AT, AT)] == [1, 3]
    db.close()


def test_failed_optional_flush_still_journals_reset_and_next_course(tmp_path, caplog):
    db = Database(tmp_path / "journal.db")
    svc = TrackerService(db, _NullBroadcaster())
    svc.session_id = db.insert_session(AT)

    def fail():
        raise OSError("input disk unavailable")

    svc.on_attempt_settled = fail
    with caplog.at_level(logging.ERROR):
        asyncio.run(svc.publish(event("practice_reset")))
        changed = event("level_changed")
        changed.payload.update({"from": 16, "to": 24})
        asyncio.run(svc.publish(changed))
    assert [row.type for row in db.events()] == ["practice_reset", "level_changed"]
    failed_flushes = svc.input_flush_failures
    assert failed_flushes >= 1  # a derived completion also settles its input
    assert "input disk unavailable" in svc.input_flush_error
    assert any(record.exc_info for record in caplog.records)
    svc.on_attempt_settled = lambda: None
    asyncio.run(svc.publish(event("state_loaded")))
    assert svc.input_flush_error is None
    assert svc.input_flush_failures == failed_flushes
    db.close()


def test_invalid_pad_does_not_slow_valid_game_snapshot_cadence():
    script = [(n, 0, 0, 200, 0) for n in range(100, 105) for _ in range(8)]
    sampler = InputSampler(ScriptedMemory(script), US, lambda *_a, **_kw: None)

    class PacedSampler:
        last = None

        def sample(self):
            self.last = sampler.sample()
            return self.last

    paced = PacedSampler()
    reader, detector = CountingReader(paced), CountingDetector()
    poller = Poller(None, [detector], NullBroadcaster(), reader=reader, input_sampler=paced)

    async def drive():
        for _ in script:
            await poller.tick()

    asyncio.run(drive())
    assert reader.frames_read == list(range(100, 105))
    assert detector.calls == 4


def test_storage_failure_cannot_grow_the_chunk_without_bound(tmp_path):
    class FailingStore:
        def append(self, *_args):
            raise OSError("disk full")

    writer = ChunkWriter(FailingStore(), 1, clock=lambda: AT)
    for number in range(writer.FLUSH_FRAMES * 3):
        try:
            writer.add(number, InputFrame(0, 0, 0, 0))
        except OSError:
            pass
    assert len(writer._buffer) == writer.FLUSH_FRAMES
    assert writer._buffer[0][0] == 0  # original valid data kept for retry


@pytest.mark.parametrize("x,y", [(1.5, 0), (0, -129), (32767, 0)])
def test_writer_rejects_other_unencodable_axes(x, y):
    writer = ChunkWriter(None, 1)
    with pytest.raises(ValueError, match="stick"):
        writer.add(1, InputFrame(0, 0, x, y))
    assert writer._buffer == []


def test_failed_poller_is_visible_and_shutdown_still_finishes(caplog):
    broadcaster = Broadcaster()
    poller = Poller(OfflineMemory(), [], broadcaster)

    async def fail():
        poller.latest = snap(193)  # a stale live snapshot must be hidden
        raise RuntimeError("deliberate poller failure")

    poller.run = fail
    stopped = []
    poller.on_stop = lambda: stopped.append(True)
    with caplog.at_level(logging.ERROR):
        with TestClient(create_app(poller, broadcaster)) as client:
            health = client.get("/health").json()
            assert health["status"] == "error"
            assert health["polling"]["state"] == "recovering"
            assert "deliberate poller failure" in health["polling"]["error"]
            assert client.get("/state").json() == {"snapshot": None}
    assert stopped == [True]
    assert any(record.exc_info for record in caplog.records)


def test_invalid_read_breaks_source_even_if_counter_repeats(tmp_path):
    db = Database(tmp_path / "inputs.db")
    session = db.insert_session(AT)
    writer = ChunkWriter(db.inputs, session)
    script = [(100, 0, 0, 84, 0), (100, 0, 0, 200, 0),
              (100, 0, 0, -84, 0), (101, 0, 0, 0, 0)]
    sampler = InputSampler(ScriptedMemory(script), US, writer.add, clock=lambda: AT)
    for _ in script:
        sampler.sample()
    sampler.flush()
    writer.close()
    chunks = db.inputs.chunks_between(AT, AT)
    assert [chunk.frames[0] for chunk in chunks] == [
        (100, InputFrame(0, 0, 84, 0)), (100, InputFrame(0, 0, -84, 0))]
    assert chunks[0].observations[0].source_id != chunks[1].observations[0].source_id
    db.close()


@pytest.mark.parametrize("cancelled", [False, True])
def test_unexpected_poller_return_or_cancellation_is_unavailable(cancelled):
    broadcaster = Broadcaster()
    poller = Poller(OfflineMemory(), [], broadcaster)

    async def stop():
        poller.latest = snap(193)
        if cancelled:
            raise asyncio.CancelledError

    poller.run = stop
    with TestClient(create_app(poller, broadcaster)) as client:
        health = client.get("/health").json()
        assert health["status"] == "error"
        assert health["polling"]["state"] == "recovering"
        assert client.get("/state").json() == {"snapshot": None}
