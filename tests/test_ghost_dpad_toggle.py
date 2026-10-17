"""The ghost hotkey flips the Ghost Overlay outside Usamune's menu
(ghost/dpad_toggle.py): D-pad Up, or all four C buttons while Usamune's save
state is on D-pad Up."""
import asyncio
from datetime import datetime, timedelta, timezone
from itertools import pairwise

import pytest

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.ghost import dpad_toggle
from sm64_events.ghost.dpad_toggle import DPAD_UP, DpadGhostToggle, OverlayFlipper
from sm64_events.ghost.session import EVENT_TYPE
from sm64_events.memory import addresses as A
from sm64_events.storage.db import Database
from tests.test_ghost_session import a_session, mark

START = datetime(2026, 10, 16, 20, 0, tzinfo=timezone.utc)
BIT = {name: bit for bit, name in A.BUTTON_BITS}
C_BUTTONS = ("Cup", "Cdown", "Cleft", "Cright")
CX4 = BIT["Cup"] | BIT["Cdown"] | BIT["Cleft"] | BIT["Cright"]
# The save state button byte as his menu flips read it, 2026-10-17.
DUP = dpad_toggle.SAVESTATE_ON_DPAD_UP
ON_CX4 = 0


def snap(frame, buttons=0, menu=False, igt=None, savestate=None):
    return GameSnapshot(
        wall_time_utc=START + timedelta(seconds=frame / 30), global_timer=frame,
        mario_action=A.ACT_JUMP, mario_action_timer=0, num_stars=0,
        last_completed_course=0, last_completed_star=0,
        igt_overall=frame if igt is None else igt,
        buttons_held=buttons, usamune_menu_open=menu,
        usamune_savestate_button=savestate)


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


@pytest.mark.parametrize("held", [("L",), ("R",), ("Z",), ("A", "B"), ("L", "R", "Z", "A", "B", "Start")])
def test_up_pressed_with_any_other_buttons_held_still_toggles_once(held):
    """His ask, 2026-10-17: "It should detect if dpad up is ever selected,
    even if it's combined with other buttons" -- the first version ignored a
    press with L, R or Z held."""
    others = 0
    for name in held:
        others |= BIT[name]
    readings = [snap(1, others), snap(2, others | DPAD_UP), snap(3, others | DPAD_UP)]
    assert presses(readings) == 1


def test_up_pressed_together_with_another_button_on_one_frame_toggles():
    readings = [snap(1), snap(2, DPAD_UP | BIT["Z"]), snap(3, DPAD_UP | BIT["Z"])]
    assert presses(readings) == 1


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


# -- the hotkey follows Usamune's save state button ---------------------------
# His ask, 2026-10-17: "if the user selected Cx4, then the ghost toggle is
# D-UP; if the user selected D-UP, then the toggle is Cx4".


def test_dup_is_the_value_his_menu_flips_read():
    """The measured pin (2026-10-17: DUP -> Cx4 -> DUP -> Cx4 -> DUP read
    1 -> 0 -> 1 -> 0 -> 1). Swapping it would silently swap the two hotkeys."""
    assert (DUP, ON_CX4) == (1, 0)


def on_dup(frame, buttons=0, menu=False):
    return snap(frame, buttons, menu=menu, savestate=DUP)


def test_with_the_save_state_on_dpad_up_all_four_c_buttons_flip_once():
    held = [on_dup(1)] + [on_dup(frame, CX4) for frame in range(2, 40)]
    assert presses(held) == 1


def test_the_c_buttons_count_on_the_reading_the_fourth_one_joins():
    joining, held = [on_dup(1)], 0
    for frame, name in enumerate(C_BUTTONS, start=2):
        held |= BIT[name]
        joining.append(on_dup(frame, held))
    assert presses(joining[:-1]) == 0 and presses(joining) == 1
    # Let one go and press it again: a second press.
    again = joining + [on_dup(9, CX4 & ~BIT["Cleft"]), on_dup(10, CX4)]
    assert presses(again) == 2


@pytest.mark.parametrize("missing", C_BUTTONS)
def test_three_c_buttons_of_four_never_flip(missing):
    three = CX4 & ~BIT[missing]
    assert presses([on_dup(1), on_dup(2, three), on_dup(3, three | BIT["A"])]) == 0


def test_dpad_up_stops_flipping_while_it_loads_usamunes_save_state():
    assert presses([on_dup(1), on_dup(2, DPAD_UP), on_dup(3), on_dup(4, DPAD_UP | BIT["Z"])]) == 0


def test_all_four_c_buttons_with_others_held_still_flip():
    others = BIT["Z"] | BIT["R"] | DPAD_UP
    assert presses([on_dup(1, others), on_dup(2, others | CX4)]) == 1


def test_the_c_buttons_never_flip_in_or_beside_usamunes_menu():
    navigating = [on_dup(1, menu=True), on_dup(2, CX4, menu=True), on_dup(3, menu=True)]
    opening = [on_dup(1), on_dup(2, CX4, menu=True)]
    closing = [on_dup(10, menu=True), on_dup(10), on_dup(11, CX4)]
    assert presses(navigating) == presses(opening) == presses(closing) == 0
    assert presses([on_dup(10, menu=True), on_dup(11), on_dup(11), on_dup(12, CX4)]) == 1


@pytest.mark.parametrize("savestate", [None, ON_CX4, DUP + 1, 0xFF])
def test_cx4_another_value_or_an_unknown_byte_keeps_dpad_up(savestate):
    def at(frame, buttons=0):
        return snap(frame, buttons, savestate=savestate)
    assert presses([at(1), at(2, DPAD_UP), at(3, DPAD_UP)]) == 1
    assert presses([at(1), at(2, CX4), at(3, CX4)]) == 0


def test_the_session_hears_the_hotkey_on_the_first_reading_and_on_each_change_only():
    told = []
    toggle = DpadGhostToggle(lambda: None, told.append)
    readings = ([snap(frame) for frame in range(1, 5)]
                + [on_dup(frame) for frame in range(5, 9)]
                + [snap(frame, savestate=ON_CX4) for frame in range(9, 12)])
    for prev, curr in pairwise(readings):
        toggle.process(prev, curr)
    assert told == ["dpad_up", "c_x4", "dpad_up"]


def test_the_ghost_payload_names_the_hotkey_and_publishes_only_when_it_changes(tmp_path):
    session, _, _, _ = a_session(tmp_path)

    async def check():
        before = await session.refresh()
        assert before["hotkey"] == "dpad_up"  # nothing read yet: D-pad Up
        sent = len(session._broadcaster.events)
        session.note_hotkey("c_x4")
        body = await session.refresh(resolve=False)
        assert body["hotkey"] == "c_x4"
        assert len(session._broadcaster.events) == sent + 1
        assert session._broadcaster.events[-1].payload["hotkey"] == "c_x4"
        session.note_hotkey("c_x4")  # a rebuilt toggle telling it again
        await session.refresh(resolve=False)
        assert len(session._broadcaster.events) == sent + 1
        await session.stop()
    asyncio.run(check())


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


def test_it_flips_with_nobody_to_race_like_the_cards_toggle(tmp_path):
    session, _, _, _ = a_session(tmp_path)
    flipper = OverlayFlipper(session)

    async def check():
        before = await session.refresh()
        assert not before["source"].get("available")
        assert (await flipper.flip())["enabled"] is (not before["enabled"])
        await session.stop()
    asyncio.run(check())


def test_nothing_flips_without_a_practice_target(tmp_path):
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
