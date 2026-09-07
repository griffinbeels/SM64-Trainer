"""Does the REAL detector chain notice that it stopped being called twice?

The 250 Hz loop changed the detectors' diet: the old 60 Hz loop handed them
~1.5 calls per game frame, so many pairs had `prev.global_timer ==
curr.global_timer`, and the new one hands them exactly one call per frame.

Replaying the journal cannot answer this. `projection.replay()` re-derives
attempts and segments from events that were ALREADY WRITTEN; it never re-runs
a detector over memory, so it is blind to how often the detectors were polled.
The question is whether the same play produces the same EVENTS, and that is
what this file drives: one synthetic frame sequence, played through the real
chain from `main.build_detectors` at both cadences, compared event for event.

A detector that depended on seeing a frame twice would show up here as an
event present under one cadence and absent under the other.
"""
from datetime import datetime, timedelta, timezone

import pytest

from sm64_events.main import build_detectors
from sm64_events.memory import addresses as A
from sm64_events.core.snapshot import GameSnapshot

START = datetime(2026, 8, 20, 21, 0, tzinfo=timezone.utc)
# A standing action and a moving one, taken from the registry rather than
# named by hand: PASSIVE_ACTIONS is what the trainer itself calls idle.
IDLE = sorted(A.PASSIVE_ACTIONS)[1]
MOVING = A.ACT_JUMP


def snap(frame: int, **overrides) -> GameSnapshot:
    fields = dict(wall_time_utc=START + timedelta(seconds=frame / 30),
                  global_timer=frame, mario_action=IDLE,
                  mario_action_timer=0, num_stars=0,
                  last_completed_course=0, last_completed_star=0,
                  curr_level=24, curr_area=1)
    fields.update(overrides)
    return GameSnapshot(**fields)


def play(frames: list[GameSnapshot], calls_per_frame: int,
         record_from: int) -> list[tuple]:
    """Run the real chain, seeing each frame `calls_per_frame` times.

    Returns a comparable shape rather than the events themselves: the objects
    carry wall-clock stamps, which necessarily differ between the two runs.

    `record_from` skips the LEAD-IN, and it is not a convenience. A chain that
    has just attached emits establishing events on its first pair, and the two
    cadences necessarily have different first pairs — at one call per frame
    the earliest is (f0, f1), at two it is (f0, f0). That difference is about
    ATTACHING, not about play, and comparing it would fail this test for a
    reason it is not asking about.
    """
    detectors = build_detectors()
    out: list[tuple] = []
    previous = None
    for frame in frames:
        for _ in range(calls_per_frame):
            if previous is not None:
                for detector in detectors:
                    for event in detector.process(previous, frame):
                        if event.frame < record_from:
                            continue
                        out.append((event.type, event.frame,
                                    tuple(sorted(
                                        (key, repr(value)) for key, value
                                        in event.payload.items()))))
            previous = frame
    return out


def a_star_grab_sequence() -> list[GameSnapshot]:
    """Idle, then a jump, then a grab into the star dance and out of it."""
    frames = [snap(number) for number in range(100, 110)]
    frames += [snap(number, mario_action=MOVING,
                    mario_action_timer=number - 110)
               for number in range(110, 118)]
    frames += [snap(number, mario_action=A.ACT_STAR_DANCE_NO_EXIT,
                    mario_action_timer=number - 118,
                    last_completed_course=24, last_completed_star=1,
                    num_stars=1, igt_overall=300 + (number - 118),
                    igt_result=330)
               for number in range(118, 190)]
    frames += [snap(number, num_stars=1, last_completed_course=24,
                    last_completed_star=1)
               for number in range(190, 200)]
    return frames


def a_level_change_and_reset_sequence() -> list[GameSnapshot]:
    """A level entry, an IGT zero (a practice reset), and movement after it."""
    frames = [snap(number, curr_level=24, igt_overall=500)
              for number in range(300, 310)]
    frames += [snap(number, curr_level=9, igt_overall=0,
                    mario_action=IDLE) for number in range(310, 320)]
    frames += [snap(number, curr_level=9, igt_overall=number - 320,
                    mario_action=MOVING) for number in range(320, 340)]
    return frames


SEQUENCES = [(a_star_grab_sequence, 110),
             (a_level_change_and_reset_sequence, 310)]

# The ONE payload field the cadence is allowed to move, and what it costs.
#
# `published_after` is the game frames a grab was held past its x-cam. The old
# double poll gave the detector a SECOND look at the x-cam frame itself, so it
# could publish with 0 held; at one look per frame the earliest chance after
# the x-cam is the next frame, so it publishes with 1. The row therefore
# appears one game frame -- 33 ms -- later than it used to.
#
# The recorded TIME is untouched: same igt_frames, same source, same star.
# This set is deliberately a whitelist rather than an ignore: anything else
# moving is a real behaviour change and this test says so.
KNOWN_CADENCE_DIFFERENCES = {"published_after"}


def _differences(single: tuple, double: tuple) -> set:
    left, right = dict(single), dict(double)
    return {key for key in set(left) | set(right)
            if left.get(key) != right.get(key)}


@pytest.mark.parametrize("build,record_from", SEQUENCES,
                         ids=["star grab", "level change and reset"])
def test_the_same_play_produces_the_same_events_at_either_cadence(build,
                                                                  record_from):
    frames = build()
    twice = play(frames, 2, record_from)
    once = play(frames, 1, record_from)
    assert [(kind, frame) for kind, frame, _payload in once] == \
           [(kind, frame) for kind, frame, _payload in twice], (
        "a detector emits a different EVENT, or on a different frame, when it "
        "stops being polled twice per game frame")
    for (_kind, _frame, single), (_k, _f, double) in zip(once, twice):
        assert _differences(single, double) <= KNOWN_CADENCE_DIFFERENCES, (
            "a payload field changed with the polling cadence, and only "
            f"{sorted(KNOWN_CADENCE_DIFFERENCES)} is known to: "
            f"{_differences(single, double)}")


@pytest.mark.parametrize("build,record_from", SEQUENCES,
                         ids=["star grab", "level change and reset"])
def test_the_sequences_actually_make_the_chain_emit_something(build,
                                                              record_from):
    """Guard on the guard: two empty lists compare equal, so a sequence that
    emits nothing would report parity while proving nothing at all."""
    assert play(build(), 1, record_from), "this sequence emitted nothing"
