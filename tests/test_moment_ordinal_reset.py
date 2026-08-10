# tests/test_moment_ordinal_reset.py
"""Ordinals restart with the attempt.

The bug this guards is a MISSING CALL, not a broken method: `MomentDetector.
reset()` works on its own (tests/test_moment.py), and a detector-level test of
it passes happily while nothing in the running system ever calls it. Then the
first attempt journals "door #1" and the second journals "door #6", so a
subsection pinned to an ordinal matches on the first run and never again.

So this drives the REAL service with the REAL detector wired the way
`main.build()` wires it, and asserts on the ORDINALS, never on the callback.
"""
import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.moment import MomentDetector
from sm64_events.memory.addresses import ACT_PULLING_DOOR
from sm64_events.tracking.service import TrackerService

ACT_WALKING = 0x04000440


def snap(action, timer) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 8, 5, tzinfo=timezone.utc),
        global_timer=timer, mario_action=action, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
        curr_level=4, curr_area=1)


class _NullBroadcaster:
    async def publish(self, event):
        return 0


def anchor(kind: str, frame: int) -> Event:
    return Event(type=kind, frame=frame,
                 timestamp_utc=datetime(2026, 8, 5, tzinfo=timezone.utc),
                 payload={"mario_acted": True})


def door(detector, frame) -> list:
    """One door pull: walk, enter the pulling action, then the settle poll
    that publishes it (a moment is a one-poll held emit since round 9 item 4
    — the landmark settles from the poll after the edge)."""
    detector.process(snap(ACT_WALKING, frame - 1), snap(ACT_WALKING, frame))
    detector.process(snap(ACT_WALKING, frame),
                     snap(ACT_PULLING_DOOR, frame + 1))
    return detector.process(snap(ACT_PULLING_DOOR, frame + 1),
                            snap(ACT_PULLING_DOOR, frame + 2))


def service_with(detector) -> TrackerService:
    svc = TrackerService(db=None, broadcaster=_NullBroadcaster())
    svc.on_attempt_boundary = detector.reset
    return svc


def test_a_practice_reset_restarts_the_ordinals():
    detector = MomentDetector()
    svc = service_with(detector)

    first = door(detector, 100)
    asyncio.run(svc.publish(anchor("practice_reset", 200)))
    second = door(detector, 300)

    assert first[0].payload["ordinal"] == 1
    assert second[0].payload["ordinal"] == 1, (
        "the second attempt's first door journalled "
        f"#{second[0].payload['ordinal']} -- an ordinal-pinned subsection "
        "would match the first run and never again")


def test_a_state_load_restarts_them_too():
    detector = MomentDetector()
    svc = service_with(detector)

    door(detector, 100)
    asyncio.run(svc.publish(anchor("state_loaded", 200)))
    after = door(detector, 300)

    assert after[0].payload["ordinal"] == 1


def test_ordinals_keep_counting_WITHIN_one_attempt():
    """The reset must fire on a boundary and nowhere else -- an ordinal that
    restarts mid-attempt makes "the 5th door" unreachable."""
    detector = MomentDetector()
    svc = service_with(detector)

    ordinals = [door(detector, 100 + step * 10)[0].payload["ordinal"]
                for step in range(5)]
    assert ordinals == [1, 2, 3, 4, 5]


def test_an_ordinary_event_does_not_restart_them():
    detector = MomentDetector()
    svc = service_with(detector)

    door(detector, 100)
    asyncio.run(svc.publish(Event(
        type="area_changed", frame=200,
        timestamp_utc=datetime(2026, 8, 5, tzinfo=timezone.utc),
        payload={"level": 4, "from": 1, "to": 2})))
    after = door(detector, 300)

    assert after[0].payload["ordinal"] == 2


def test_the_service_survives_having_no_boundary_hook_wired():
    """Every test fixture and several tools build a TrackerService without
    a detector chain at all; an unwired hook must be inert, not a crash."""
    svc = TrackerService(db=None, broadcaster=_NullBroadcaster())
    asyncio.run(svc.publish(anchor("practice_reset", 200)))
