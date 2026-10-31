"""Ghosts asked for and unseen too long: what the practice card's setup notice reads."""
import asyncio

from sm64_events.core.silent_failures import GRACE_S
from sm64_events.ghost.display_stall import DisplayStall
from tests.test_ghost_session import a_session, drive


def waiting(**changes):
    status = {"expected_display": True, "available": True, "display_pending": True,
              "awaiting_preparation": False, "displayed": False, "progress": (4, 0, 2, 2, 1, 1, 1), "scope": 1}
    return {**status, **changes}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_an_unshown_ghost_stalls_only_after_the_report_watchs_grace():
    clock = Clock()
    stall = DisplayStall(clock)
    assert stall.observe(waiting()) is False
    clock.now = GRACE_S - 1
    assert stall.observe(waiting()) is False
    clock.now = GRACE_S
    assert stall.observe(waiting()) is True
    # A busy (empty) read changes nothing; a displayed ghost clears it at once.
    assert stall.observe({}) is True
    assert stall.observe(waiting(displayed=True)) is False


def test_progress_restarts_the_wait_and_ordinary_states_never_stall():
    clock = Clock()
    stall = DisplayStall(clock)
    stall.observe(waiting())
    clock.now = GRACE_S - 1
    stall.observe(waiting(progress=(4, 0, 2, 2, 2, 2, 1)))
    clock.now = GRACE_S + 5
    assert stall.observe(waiting(progress=(4, 0, 2, 2, 2, 2, 1))) is False
    for quiet in (waiting(expected_display=False), waiting(display_pending=False)):
        clock.now += 100
        assert stall.observe(quiet) is False and stall.observe(quiet) is False


def test_an_unreachable_renderer_with_ghosts_expected_also_stalls():
    """Project64 on another graphics plugin: no renderer ever answers."""
    clock = Clock()
    stall = DisplayStall(clock)
    stall.observe({"expected_display": True, "available": False, "displayed": False})
    clock.now = GRACE_S
    assert stall.observe({"expected_display": True, "available": False, "displayed": False}) is True


def test_the_ghost_payload_publishes_the_stall(tmp_path):
    session, _, _, _ = a_session(tmp_path)
    clock = Clock()
    session._display_stall = DisplayStall(clock)
    session.silent_failure_status = waiting
    assert drive(session)["display_stalled"] is False
    clock.now = GRACE_S
    body = drive(session)
    assert body["display_stalled"] is True
    assert session._broadcaster.events[-1].payload["display_stalled"] is True
    session.silent_failure_status = lambda: waiting(displayed=True)
    assert asyncio.run(session.refresh(resolve=False))["display_stalled"] is False
