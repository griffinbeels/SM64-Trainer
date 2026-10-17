"""D-pad Up flips the Ghost Overlay outside Usamune's menu (ghost/dpad_toggle.py)."""
import asyncio
from datetime import datetime, timedelta, timezone
from itertools import pairwise

import pytest

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.ghost.dpad_toggle import (
    COMBO_BUTTONS, DPAD_UP, DpadGhostToggle, OverlayFlipper,
)
from sm64_events.ghost.session import EVENT_TYPE
from sm64_events.memory import addresses as A
from sm64_events.storage.db import Database
from tests.test_ghost_session import a_session, mark

START = datetime(2026, 10, 16, 20, 0, tzinfo=timezone.utc)
BIT = {name: bit for bit, name in A.BUTTON_BITS}


def snap(frame, buttons=0, menu=False, igt=None):
    return GameSnapshot(
        wall_time_utc=START + timedelta(seconds=frame / 30), global_timer=frame,
        mario_action=A.ACT_JUMP, mario_action_timer=0, num_stars=0,
        last_completed_course=0, last_completed_star=0,
        igt_overall=frame if igt is None else igt,
        buttons_held=buttons, usamune_menu_open=menu)


def presses(readings) -> int:
    """How many flips the toggle asks for over consecutive readings."""
    asked = []
    toggle = DpadGhostToggle(lambda: asked.append(True))
    for prev, curr in pairwise(readings):
        assert toggle.process(prev, curr) == []
    return len(asked)


def test_a_press_alone_toggles_once_and_holding_never_repeats():
    held = [snap(1)] + [snap(frame, DPAD_UP) for frame in range(2, 40)]
    assert presses(held) == 1


def test_release_and_press_again_toggles_again():
    readings = [snap(1), snap(2, DPAD_UP), snap(3, DPAD_UP), snap(4), snap(5),
                snap(6, DPAD_UP | BIT["A"])]
    assert presses(readings) == 2


@pytest.mark.parametrize("combo", ["L", "R", "Z"])
def test_a_usamune_combo_never_toggles(combo):
    readings = [snap(1, BIT[combo]), snap(2, BIT[combo] | DPAD_UP),
                snap(3, BIT[combo] | DPAD_UP)]
    assert presses(readings) == 0
    assert COMBO_BUTTONS & BIT[combo]


def test_navigating_usamunes_menu_never_toggles():
    readings = [snap(1, menu=True), snap(2, DPAD_UP, menu=True), snap(3, menu=True),
                snap(4, DPAD_UP, menu=True)]
    assert presses(readings) == 0


def test_a_press_on_the_frame_the_menu_opens_or_closes_never_toggles():
    opening = [snap(1), snap(2, DPAD_UP, menu=True)]
    closing = [snap(1, menu=True), snap(2, DPAD_UP)]
    assert presses(opening) == 0 and presses(closing) == 0


def test_the_frame_after_the_menu_closes_still_waits_when_two_readings_share_a_frame():
    # Without a pad sampler the poller reads at 60 Hz: two readings a frame.
    readings = [snap(10, menu=True), snap(11), snap(11), snap(12, DPAD_UP)]
    assert presses(readings) == 1
    blocked = [snap(10, menu=True), snap(10), snap(11, DPAD_UP)]
    assert presses(blocked) == 0


def test_the_games_own_pause_menu_toggles_like_gameplay():
    # Paused: the frame advances while Usamune's counter stands (anchors.py).
    readings = [snap(frame, DPAD_UP if frame >= 5 else 0, igt=300) for frame in range(1, 9)]
    assert presses(readings) == 1


def test_a_backward_timer_jump_heals_the_menu_memory():
    # A state loaded from the menu: the clock goes back, then he plays on.
    readings = [snap(900, menu=True), snap(120), snap(121), snap(122, DPAD_UP)]
    assert presses(readings) == 1


def test_an_unknown_or_impossible_reading_never_toggles():
    unknown_pad = GameSnapshot(**{**snap(2, DPAD_UP).__dict__, "buttons_held": None})
    unknown_menu = GameSnapshot(**{**snap(2, DPAD_UP).__dict__, "usamune_menu_open": None})
    reset_line = snap(2, DPAD_UP | 0x0080)  # not a button word (BUTTON_VALID_MASK)
    for curr in (unknown_pad, unknown_menu, reset_line):
        assert presses([snap(1), curr]) == 0
    assert presses([GameSnapshot(**{**snap(1).__dict__, "buttons_held": None}),
                    snap(2, DPAD_UP)]) == 0


# -- the flip goes through the card's doors ---------------------------------


def test_the_flip_turns_the_overlay_back_on_remembers_it_and_tells_clients(tmp_path):
    db = Database(tmp_path / "prefs.db")
    session, _, _, store = a_session(tmp_path, db=db)
    mark(store)
    flipper = OverlayFlipper(session)

    async def check():
        await session.update(enabled=False)
        body = await flipper.flip()
        assert body["enabled"] is True
        assert session._broadcaster.events[-1].type == EVENT_TYPE
        assert session._broadcaster.events[-1].payload["enabled"] is True
        restored, _, _, _ = a_session(tmp_path, db=db, store=store)
        assert (await restored.refresh())["enabled"] is True
        assert (await flipper.flip())["enabled"] is False
        await session.stop()
        await restored.stop()
    asyncio.run(check())


def test_nothing_flips_when_the_switch_could_not(tmp_path):
    session, _, _, _ = a_session(tmp_path, target=None)
    flipper = OverlayFlipper(session)

    async def check():
        before = await session.refresh()
        assert await flipper.flip() is None
        assert (await session.refresh())["enabled"] == before["enabled"]
        assert await OverlayFlipper().flip() is None
        await session.stop()
    asyncio.run(check())


def test_quick_presses_each_read_the_last_flip(tmp_path):
    session, _, _, store = a_session(tmp_path)
    mark(store)
    flipper = OverlayFlipper(session)

    async def check():
        start = (await session.refresh())["enabled"]
        flipper.request()
        flipper.request()
        await asyncio.gather(*flipper._tasks)
        # Two presses are two flips: unserialized, both read `start`.
        assert (await session.refresh())["enabled"] is start
        await session.stop()
    asyncio.run(check())


def test_a_request_with_no_session_or_no_loop_does_nothing():
    OverlayFlipper().request()
    flipper = OverlayFlipper(session=object())
    flipper.request()  # called outside a running loop: an offline replay
    assert not flipper._tasks
