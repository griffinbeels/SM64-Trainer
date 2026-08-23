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


def test_marios_action_and_yaw_survive_the_round_trip():
    """Round 32's ask: what he was DOING alongside what he was pressing."""
    original = [(0, InputFrame(0x8000, 0, 40, 40, 0x0188088A, -12000))]
    got = decode_runs(encode_runs(original))
    assert got[0][1].action == 0x0188088A
    assert got[0][1].yaw == -12000


def test_an_action_change_BREAKS_a_run_even_with_the_pad_unmoved():
    """A dive that begins while A is already held is exactly the transition
    the action row exists to show; collapsing it away would hide it."""
    frames = [(0, InputFrame(0x8000, 0, 0, 0, 0x0C400201, 0)),
              (1, InputFrame(0x8000, 0, 0, 0, 0x0188088A, 0))]
    assert len(decode_runs(encode_runs(frames))) == 2
    assert decode_runs(encode_runs(frames))[1][1].action == 0x0188088A


def test_a_v1_chunk_still_decodes_as_v1():
    """Old chunks were written before Mario's state was captured. They read
    back with zeroes for it -- which is the honest answer -- rather than being
    reinterpreted as v2 and returning plausible nonsense."""
    import struct as _struct
    blob = (_struct.pack("<II", 0, 1)
            + _struct.pack("<IHHbb", 0, 3, 0x8000, 20, -20))
    got = decode_runs(blob, 1)
    assert [number for number, _f in got] == [0, 1, 2]
    assert got[0][1].buttons == 0x8000 and got[0][1].stick_x == 20
    assert got[0][1].action == 0 and got[0][1].yaw == 0


def test_an_unknown_chunk_format_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="format"):
        decode_runs(encode_runs([(0, InputFrame(0, 0, 0, 0))]), 99)


def test_a_stored_chunk_records_which_format_it_used(store):
    _db, inputs, session = store
    inputs.append(session, [(0, InputFrame(0x8000, 0, 1, 2, 7, 9))], AT, LATER)
    got = inputs.frames_between(AT, LATER)
    assert got[0][1].action == 7 and got[0][1].yaw == 9


# --- what survives a restart, and what a deletion takes with it (2026-08-23) --
# His question: "when I save a PB / save a replay, it ALSO stores that
# replay's input file right? So that, if I closed the program and came back,
# I would expect to still be able to inspect the replays for anything I've
# played before". Captured input lives in the journal's own database for
# EVERY attempt -- saved or not -- and is never evicted; only deleting the
# session, or wiping all history, removes it.

def test_captured_input_survives_closing_and_reopening_the_database(tmp_path):
    from sm64_events.storage.db import Database
    path = tmp_path / "t.db"
    first = Database(path)
    first.inputs.append(first.insert_session(AT),
                        [(100, InputFrame(0x8000, 0, 40, 0))], AT, LATER)
    first.close()
    again = Database(path)
    assert [(number, frame.buttons)
            for number, frame in again.inputs.frames_between(AT, LATER)] == [(100, 0x8000)]


def test_deleting_a_session_takes_its_captured_input_with_it(tmp_path):
    from sm64_events.storage.db import Database
    db = Database(tmp_path / "t.db")
    doomed = db.insert_session(AT)
    kept = db.insert_session(LATER)
    db.inputs.append(doomed, [(100, InputFrame(0x8000, 0, 0, 0))], AT, AT)
    db.inputs.append(kept, [(200, InputFrame(0x4000, 0, 0, 0))], LATER, LATER)
    db.delete_session(doomed)
    assert [frame.buttons for _n, frame in db.inputs.frames_between(AT, LATER)] == [0x4000]


def test_wiping_all_history_wipes_the_captured_input_too(tmp_path):
    from sm64_events.storage.db import Database
    db = Database(tmp_path / "t.db")
    session = db.insert_session(AT)
    db.inputs.append(session, [(100, InputFrame(0x8000, 0, 0, 0))], AT, AT)
    db.wipe_all_history(keep_session_id=session)
    assert db.inputs.frames_between(AT, LATER) == []
