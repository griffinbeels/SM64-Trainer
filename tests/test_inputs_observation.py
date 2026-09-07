"""Observation time must survive delayed emission and chunk flushing."""
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from sm64_events.inputs.sampler import InputSampler
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.observation import InputObservation
from sm64_events.inputs.store import ChunkWriter
from sm64_events.inputs.track import track_for_attempt
from sm64_events.memory.layout import US
from sm64_events.storage.db import Database
from test_inputs_sampler import ScriptedMemory
from test_inputs_track import FakeAttempt


@pytest.mark.parametrize("emission_delay", [0, 20], ids=["immediate", "delayed"])
def test_pending_input_is_found_when_it_was_observed_not_when_flushed(tmp_path, emission_delay):
    with closing(Database(tmp_path / "observed.db")) as db:
        before = datetime.now(timezone.utc)
        session = db.insert_session(before.isoformat())
        emission = [before.isoformat()]
        writer = ChunkWriter(db.inputs, session, clock=lambda: emission[0])
        sampler = InputSampler(ScriptedMemory([(100, 0x8000, 0, 80, 0)]), US,
                               writer.add)
        sampler.sample()
        after = datetime.now(timezone.utc)
        # Only emission/flush move. The read happened inside the independently
        # measured bracket in both cases; no sleep or live memory is needed.
        emission[0] = (after + timedelta(seconds=emission_delay)).isoformat()
        sampler.flush()
        writer.close()
        frames = db.inputs.frames_between(before.isoformat(), after.isoformat())
        assert [(raw, pad.buttons, pad.stick_x) for raw, pad in frames] == [(100, 0x8000, 80)]


def test_flushing_the_previous_frame_cannot_move_the_current_reads_timestamp():
    at = "2026-08-20T21:00:00+00:00"
    later = "2026-08-20T21:00:20+00:00"
    clock = [at]
    got = []

    def slow_sink(number, frame, *, observation):
        got.append(observation)
        clock[0] = later

    sampler = InputSampler(ScriptedMemory([(100, 0x8000, 0, 80, 0),
                                           (101, 0x8000, 0, 80, 0)]),
                           US, slow_sink, clock=lambda: clock[0])
    sampler.sample()
    sampler.sample()  # reads frame 101, then emits 100 through the slow sink
    sampler.flush()
    assert [datetime.fromisoformat(o.observed_utc) for o in got] == [datetime.fromisoformat(at)] * 2


@pytest.mark.parametrize("observed", [False, True], ids=["legacy", "observed"])
def test_fractional_input_time_matches_the_journals_whole_second_z_window(tmp_path, observed):
    at = "2026-08-20T21:00:00.500000+00:00"
    pad = InputFrame(0x8000, 0, 80, 0)
    with closing(Database(tmp_path / "utc.db")) as db:
        session = db.insert_session(at)
        observations = [InputObservation("poll:test", 0, at)] if observed else None
        db.inputs.append(session, [(100, pad)], at, at, observations=observations)
        attempt = FakeAttempt(started_utc="2026-08-20T21:00:00Z",
                              ended_utc="2026-08-20T21:00:01Z",
                              anchor_frame=100, rta_frames=0)
        assert track_for_attempt(db.inputs, attempt) == [(100, pad)]


@pytest.mark.parametrize("second, expected", [(0, True), (30, False), (60, True)])
def test_a_held_state_keeps_actual_observation_endpoints_without_filling_the_gap(tmp_path, second, expected):
    base = datetime(2026, 8, 20, 21, tzinfo=timezone.utc)
    stamps = iter([base.isoformat(), (base + timedelta(seconds=60)).isoformat()])
    with closing(Database(tmp_path / "held.db")) as db:
        session = db.insert_session(base.isoformat())
        writer = ChunkWriter(db.inputs, session)
        sampler = InputSampler(ScriptedMemory([(100, 0x8000, 0, 80, 0)] * 2),
                               US, writer.add, clock=lambda: next(stamps))
        sampler.sample()
        sampler.sample()
        sampler.flush()
        writer.close()
        at = (base + timedelta(seconds=second)).isoformat()
        attempt = FakeAttempt(started_utc=at, ended_utc=at, anchor_frame=100, rta_frames=0)
        assert bool(track_for_attempt(db.inputs, attempt)) is expected


@pytest.mark.parametrize("observed", [False, True], ids=["legacy", "observed"])
def test_submillisecond_query_does_not_admit_a_neighbouring_observation(tmp_path, observed):
    at = "2026-08-20T21:00:00.123456+00:00"
    with closing(Database(tmp_path / "precise.db")) as db:
        session = db.insert_session(at)
        metadata = [InputObservation("poll:test", 0, at)] if observed else None
        db.inputs.append(session, [(100, InputFrame(0, 0, 0, 0))], at, at,
                         observations=metadata)
        assert len(db.inputs.chunks_between(at, at)) == 1
        for fraction in ("123455", "123457"):
            adjacent = f"2026-08-20T21:00:00.{fraction}Z"
            assert db.inputs.chunks_between(adjacent, adjacent) == []


def test_rewritten_state_does_not_inherit_the_earlier_stale_reads_time():
    stamps = iter(["2026-08-20T21:00:00Z", "2026-08-20T21:00:01Z"])
    got = []
    sampler = InputSampler(ScriptedMemory([(100, 0x8000, 0, 80, 0),
                                           (100, 0x4000, 0, -80, 0)]), US,
                           lambda number, frame, **metadata: got.append(metadata["observation"]),
                           clock=lambda: next(stamps))
    sampler.sample()
    sampler.sample()
    sampler.flush()
    observation, = got
    assert observation.first_observed_utc == observation.observed_utc
    assert datetime.fromisoformat(observation.observed_utc).second == 1


@pytest.mark.parametrize("middle,query,expected", [(5, 5, True), (50, 50, True), (50, 30, False)])
def test_held_state_retains_interior_reads_and_backward_clock_extrema(tmp_path, middle, query, expected):
    times = iter([f"2026-08-20T21:00:{second:02d}Z" for second in (0, middle, 10)])
    with closing(Database(tmp_path / "interior.db")) as db:
        session = db.insert_session("2026-08-20T21:00:00Z")
        writer = ChunkWriter(db.inputs, session)
        sampler = InputSampler(ScriptedMemory([(100, 0x8000, 0, 80, 0)] * 3),
                               US, writer.add, clock=lambda: next(times))
        for _ in range(3):
            sampler.sample()
        sampler.flush()
        writer.close()
        attempt = FakeAttempt(started_utc=f"2026-08-20T21:00:{query - 1:02d}Z",
                              ended_utc=f"2026-08-20T21:00:{query + 1:02d}Z",
                              anchor_frame=100, rta_frames=0)
        frames = [(raw, pad.buttons) for raw, pad in track_for_attempt(db.inputs, attempt)]
        assert frames == ([(100, 0x8000)] if expected else [])
