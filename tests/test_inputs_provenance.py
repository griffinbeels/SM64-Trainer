"""Provenance survives compression, persistence and repeated counter values."""
from contextlib import closing
from datetime import datetime

import pytest

from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.observation import InputObservation, decode_observations, encode_observations
from sm64_events.inputs.sampler import InputSampler
from sm64_events.inputs.store import ChunkWriter
from sm64_events.inputs.track import track_for_attempt
from sm64_events.memory.layout import US
from sm64_events.storage.db import Database
from test_inputs_sampler import ScriptedMemory
from test_inputs_track import AT, LATER, FakeAttempt


PAD = InputFrame(0x8000, 0, 80, 0)


def test_observed_order_survives_a_backward_wall_clock_and_database_reopen(tmp_path):
    path = tmp_path / "order.db"
    with closing(Database(path)) as db:
        session = db.insert_session(AT)
        clock = iter([LATER, AT])
        writer = ChunkWriter(db.inputs, session)
        sampler = InputSampler(ScriptedMemory([(100, 0x8000, 0, 80, 0),
                                               (101, 0x8000, 0, 80, 0)]),
                               US, writer.add, clock=lambda: next(clock))
        writer.FLUSH_FRAMES = 1
        sampler.sample()
        sampler.sample()
        sampler.flush()
        writer.close()
    with closing(Database(path)) as db:
        chunks = db.inputs.chunks_between(AT, LATER)
        assert [raw for c in chunks for raw, _pad in c.frames] == [100, 101]
        first, second = [c.observations[0] for c in chunks]
        assert first.source_id == second.source_id
        assert (first.sequence, second.sequence) == (0, 1)
        assert [datetime.fromisoformat(o.observed_utc) for o in (first, second)] == [
            datetime.fromisoformat(stamp) for stamp in (LATER, AT)]


def test_compressed_identical_frames_keep_each_observation(tmp_path):
    with closing(Database(tmp_path / "rle.db")) as db:
        session = db.insert_session(AT)
        observations = [InputObservation("poll:test", n, AT) for n in range(300)]
        db.inputs.append(session, [(n, PAD) for n in range(300)], AT, LATER,
                         observations=observations)
        chunk, = db.inputs.chunks_between(AT, AT)
        assert chunk.observations == observations
        assert chunk.frames == [(n, PAD) for n in range(300)]
        assert chunk.started_utc == chunk.ended_utc
        assert datetime.fromisoformat(chunk.started_utc) == datetime.fromisoformat(AT)
        row = db._conn.execute("SELECT format,runs FROM input_chunks").fetchone()
        assert row["format"] == 3
        assert len(row["runs"]) < 300 * 8  # repeated identity/time is compressed


@pytest.mark.parametrize("anchored", [False, True])
def test_chunk_endpoints_do_not_invent_observations_inside_a_pause(tmp_path, anchored):
    before, after = "2026-08-20T20:59:59+00:00", "2026-08-20T21:00:11+00:00"
    with closing(Database(tmp_path / "gap.db")) as db:
        session = db.insert_session(before)
        db.inputs.append(session, [(100, PAD), (101, PAD)], before, after,
                         observations=[InputObservation("poll:old", 0, before),
                                       InputObservation("poll:old", 1, after)])
        attempt = FakeAttempt(anchor_frame=100 if anchored else None, rta_frames=1)
        assert track_for_attempt(db.inputs, attempt) == []
        # A second source really observed these same counters inside the attempt.
        other = InputFrame(0x4000, 0, -80, 0)
        db.inputs.append(session, [(100, other), (101, other)], AT, LATER,
                         observations=[InputObservation("poll:actual", 0, AT),
                                       InputObservation("poll:actual", 1, LATER)])
        assert track_for_attempt(db.inputs, attempt) == [(100, other), (101, other)]


def test_new_sampler_runs_do_not_widen_into_an_older_run_with_lower_counters(tmp_path):
    with closing(Database(tmp_path / "runs.db")) as db:
        session = db.insert_session(AT)
        writer = ChunkWriter(db.inputs, session)
        writer.add(99, PAD, observation=InputObservation("poll:old", 0, AT))
        writer.add(100, PAD, observation=InputObservation("poll:new", 0, LATER))
        writer.close()
        chunks = db.inputs.chunks_between(AT, LATER)
        assert len(chunks) == 2
        attempt = FakeAttempt(started_utc=LATER, ended_utc=LATER, anchor_frame=99,
                              rta_frames=1)
        assert track_for_attempt(db.inputs, attempt) == [(100, PAD)]


def test_reset_identity_survives_the_reset_chunk_falling_outside_the_time_query(tmp_path):
    clock = iter(["2026-08-20T21:00:10Z", "2026-08-20T21:01:40Z",
                  "2026-08-20T21:00:11Z"])
    with closing(Database(tmp_path / "hidden-reset.db")) as db:
        session = db.insert_session(AT)
        writer = ChunkWriter(db.inputs, session)
        writer.FLUSH_FRAMES = 1
        sampler = InputSampler(ScriptedMemory([(100, 0x8000, 0, 80, 0),
                                               (50, 0, 0, 0, 0),
                                               (101, 0x4000, 0, -80, 0)]), US,
                               writer.add, clock=lambda: next(clock))
        for _ in range(3):
            sampler.sample()
        sampler.flush()
        writer.close()
        attempt = FakeAttempt(started_utc="2026-08-20T21:00:11Z",
                              ended_utc="2026-08-20T21:00:11Z",
                              anchor_frame=100, rta_frames=1)
        assert [(raw, pad.buttons) for raw, pad in track_for_attempt(db.inputs, attempt)] == [
            (101, 0x4000)]


@pytest.mark.parametrize("observations", [[], [InputObservation("poll:test", 0, AT)] * 2])
def test_invalid_observation_count_or_order_cannot_be_written(tmp_path, observations):
    with closing(Database(tmp_path / "invalid.db")) as db:
        session = db.insert_session(AT)
        with pytest.raises(ValueError, match="observation"):
            db.inputs.append(session, [(100, PAD), (101, PAD)], AT, LATER,
                             observations=observations)
        assert db.inputs.chunks_between(AT, LATER) == []


def test_largest_accepted_observation_fits_the_bounded_decoder():
    observations = [InputObservation("p" * 80, 2**64 - 1,
                                     "9999-12-31T23:59:59.999999Z",
                                     "0001-01-01T00:00:00.000000Z")]
    assert decode_observations(encode_observations(observations, 1), 1) == observations


@pytest.mark.parametrize("source,sequence", [("p" * 81, 0), ("\u00e9" * 80, 0),
                                             ("poll:test", 2**64), ("poll:test", -1)])
def test_out_of_contract_identity_is_rejected_before_encoding(source, sequence):
    with pytest.raises(ValueError, match="identity"):
        encode_observations([InputObservation(source, sequence, AT)], 1)
