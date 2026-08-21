import pytest

from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.store import (ChunkWriter, InputStore, decode_runs,
                                      encode_runs)
from sm64_events.storage.db import Database

AT = "2026-08-20T21:00:00+00:00"
LATER = "2026-08-20T21:00:10+00:00"


def frames(spec):
    """spec: list of (frame_number, buttons, stick_x, stick_y)."""
    return [(number, InputFrame(buttons, 0, stick_x, stick_y))
            for number, buttons, stick_x, stick_y in spec]


def test_runs_round_trip_through_the_blob():
    original = frames([(100, 0, 0, 0), (101, 0x8000, 40, -30),
                       (102, 0x8000, 40, -30), (103, 0, 0, 0)])
    assert decode_runs(encode_runs(original)) == original


def test_identical_consecutive_frames_collapse_to_one_run():
    held = frames([(number, 0x8000, 60, 0) for number in range(200, 260)])
    assert decode_runs(encode_runs(held)) == held
    assert len(encode_runs(held)) < len(encode_runs(held[:2])) + 20


def test_a_gap_in_frame_numbers_survives_the_round_trip():
    """A capture hole is a hole. Each run carries the frame it STARTS on for
    exactly this reason -- a decoder given only lengths would have to assume
    the frames were contiguous, which is the one thing a hole means they are
    not."""
    original = frames([(10, 0x8000, 0, 0), (11, 0x8000, 0, 0),
                       (40, 0x8000, 0, 0)])
    assert decode_runs(encode_runs(original)) == original


def test_a_negative_stick_survives_the_round_trip():
    original = frames([(1, 0, -84, -1)])
    assert decode_runs(encode_runs(original)) == original


def test_an_empty_track_encodes_and_decodes_to_nothing():
    assert decode_runs(encode_runs([])) == []


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "t.db")
    return db, InputStore(db._conn, db._lock), db.insert_session(AT)


def test_a_chunk_comes_back_by_its_utc_span(store):
    _db, inputs, session = store
    inputs.append(session, frames([(100, 0x8000, 10, 20)]), AT, LATER)
    got = inputs.frames_between("2026-08-20T21:00:05+00:00",
                                "2026-08-20T21:00:07+00:00")
    assert got == frames([(100, 0x8000, 10, 20)])


def test_a_chunk_outside_the_span_is_not_returned(store):
    _db, inputs, session = store
    inputs.append(session, frames([(1, 0x8000, 0, 0)]),
                  "2026-08-20T20:00:00+00:00", "2026-08-20T20:00:10+00:00")
    assert inputs.frames_between(AT, LATER) == []


def test_chunks_come_back_in_capture_order_not_frame_order(store):
    """The frame counter restarts on a console reset, so frame numbers repeat
    within a session. Wall clock is the only total order there is."""
    _db, inputs, session = store
    inputs.append(session, frames([(900, 0x8000, 0, 0)]), AT,
                  "2026-08-20T21:00:05+00:00")
    inputs.append(session, frames([(12, 0x4000, 0, 0)]),
                  "2026-08-20T21:00:05+00:00", LATER)
    got = inputs.frames_between(AT, LATER)
    assert [number for number, _frame in got] == [900, 12]


def test_an_empty_chunk_is_not_written(store):
    _db, inputs, session = store
    inputs.append(session, [], AT, LATER)
    assert inputs.frames_between(AT, LATER) == []


def test_the_writer_flushes_every_ten_seconds_of_frames(store):
    _db, inputs, session = store
    writer = ChunkWriter(inputs, session, clock=lambda: AT)
    for number in range(ChunkWriter.FLUSH_FRAMES):
        writer.add(number, InputFrame(0, 0, 0, 0))
    assert len(inputs.frames_between(AT, AT)) == ChunkWriter.FLUSH_FRAMES


def test_a_backward_frame_counter_closes_the_chunk(store):
    """A console reset restarts the counter. One chunk cannot hold both sides
    of that seam and still decode as a monotonic run."""
    _db, inputs, session = store
    writer = ChunkWriter(inputs, session, clock=lambda: AT)
    writer.add(900, InputFrame(0x8000, 0, 0, 0))
    writer.add(12, InputFrame(0x4000, 0, 0, 0))
    writer.close()
    got = inputs.frames_between(AT, AT)
    assert [number for number, _frame in got] == [900, 12]


def test_close_flushes_whatever_is_in_hand(store):
    _db, inputs, session = store
    writer = ChunkWriter(inputs, session, clock=lambda: AT)
    writer.add(1, InputFrame(0x2000, 0, 5, 5))
    writer.close()
    assert len(inputs.frames_between(AT, AT)) == 1


def test_closing_twice_writes_one_chunk(store):
    _db, inputs, session = store
    writer = ChunkWriter(inputs, session, clock=lambda: AT)
    writer.add(1, InputFrame(0x2000, 0, 5, 5))
    writer.close()
    writer.close()
    assert len(inputs.frames_between(AT, AT)) == 1


def test_the_writer_reads_the_session_id_lazily(store):
    """The composition root builds the writer before the tracker has opened a
    session, so the id cannot be captured at build time."""
    _db, inputs, session = store
    current = [None]
    writer = ChunkWriter(inputs, lambda: current[0], clock=lambda: AT)
    writer.add(1, InputFrame(0x2000, 0, 0, 0))
    writer.close()
    assert inputs.frames_between(AT, AT) == []   # no session yet: dropped
    current[0] = session
    writer.add(2, InputFrame(0x2000, 0, 0, 0))
    writer.close()
    assert len(inputs.frames_between(AT, AT)) == 1
