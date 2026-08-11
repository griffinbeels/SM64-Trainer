"""The textbox probe's pure core (tools/probe_textbox.py), round 2.

Round 1 shipped `first_entry` untested (trusted by inspection, like
`probe_warp_block.py`'s original watcher). Round 2 adds a second derivation
-- `box_opens`, which decides the frame a dialog box actually appeared from
MARIO_ACTION_STATE rather than from the action edge -- and it is the number
his next live report will be checked against, so it gets a test before he
runs it. Cases below are the three shapes his own two rounds have produced:
the box opening on schedule, an action with no known threshold (WAITING has
none -- it is not a reading action), and the window closing before the box
appeared (widen TRACE_FRAMES or he walked away).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from sm64_events.memory.addresses import (ACT_READING_AUTOMATIC_DIALOG,  # noqa: E402
                                           ACT_READING_NPC_DIALOG,
                                           ACT_WAITING_FOR_DIALOG)
from probe_textbox import box_opens, first_entry  # noqa: E402

READING_ACTIONS = frozenset(
    {ACT_READING_AUTOMATIC_DIALOG, ACT_READING_NPC_DIALOG})


def row(timer: int, action: int, action_state: int = 0,
        action_timer: int = 0, counter: int | None = None) -> dict:
    return {
        "timer": timer,
        "action": action,
        "action_state": action_state,
        "action_timer": action_timer,
        "level": 24,
        "area": 1,
        "counter": counter if counter is not None else timer,
    }


def turn_then_box(start: int, action=ACT_READING_NPC_DIALOG,
                   held_frames: int = 0) -> list[dict]:
    """The real King Whomp shape: actionState climbs 0..8 one per frame
    (the turn), then holds at 8 while the box is open."""
    trace = [row(start + step, action, action_state=step)
             for step in range(9)]  # 0..8 inclusive
    trace += [row(start + 9 + extra, action, action_state=8)
              for extra in range(held_frames)]
    return trace


def test_box_opens_finds_the_frame_action_state_reaches_its_threshold():
    trace = turn_then_box(1000)
    reading = first_entry(trace, READING_ACTIONS)
    opened = box_opens(trace, reading)
    assert reading["timer"] == 1000
    assert opened["timer"] == 1008          # 8 frames later, his own figure
    assert opened["action_state"] == 8


def test_box_opens_returns_none_for_an_action_with_no_known_threshold():
    # ACT_WAITING_FOR_DIALOG is not a reading action -- it has no box-open
    # state at all, so a caller must not be told a frame for one.
    trace = [row(1000, ACT_WAITING_FOR_DIALOG, action_state=step)
             for step in range(5)]
    reading = first_entry(trace, READING_ACTIONS)
    assert reading is None
    assert box_opens(trace, reading) is None


def test_box_opens_returns_none_when_the_window_closes_first():
    # Only reaches action_state 4 before the trace ends -- Mario walked away
    # mid-turn, or TRACE_FRAMES was too short. Never guess a frame for this.
    trace = [row(1000 + step, ACT_READING_NPC_DIALOG, action_state=step)
             for step in range(5)]
    reading = first_entry(trace, READING_ACTIONS)
    assert box_opens(trace, reading) is None


def test_the_probe_reports_no_open_state_for_automatic_dialogue():
    """AUTOMATIC dialogue is deliberately NOT gated, and this test says so.

    It asserted the opposite until the 2026-08-11 merge, on the strength of the
    decomp alone: `act_reading_automatic_dialog` increments actionState before
    checking it, so its box "opens at 9". That was never watched in RAM. His
    live run captured 13 textboxes and every one was NPC dialogue (King Whomp),
    so the automatic threshold rested on reading source and nothing else.

    Meanwhile a sibling branch had already measured the automatic case at its
    ACTION EDGE, by frame-stepping a star door's message (journal id 28790,
    Usamune 0'11"53 against raw counter 346 -> `counter + 0`), and shipped it.
    Gating automatic dialogue on an unmeasured 9 would have moved the moment
    several frames later and silently invalidated that measurement, so
    `BOX_OPENS_AT_STATE` covers the NPC action only.

    OPEN, and the shape of what would settle it: one screenshot of a star-door
    message holding both numbers, scored with `tools/score_moment_clock.py`.
    Until then the probe honestly reports nothing rather than a guessed frame —
    the same refusal `test_box_opens_returns_none_when_the_window_closes_first`
    makes for a trace that ends mid-turn.
    """
    trace = turn_then_box(2000, action=ACT_READING_AUTOMATIC_DIALOG)
    trace += [row(2009, ACT_READING_AUTOMATIC_DIALOG, action_state=9)]
    reading = first_entry(trace, READING_ACTIONS)
    assert reading is not None, "the automatic reading action is still found"
    assert box_opens(trace, reading) is None, (
        "automatic dialogue must stay ungated until it has a live reading of "
        "its own -- a guessed threshold would invalidate the star-door "
        "measurement already shipped against the action edge")


def test_first_entry_finds_reading_directly_when_waiting_is_skipped():
    # His refuted round-1 hypothesis, preserved as a regression: King Whomp's
    # ten encounters never touched ACT_WAITING_FOR_DIALOG at all.
    trace = turn_then_box(500)
    waiting = first_entry(trace, frozenset({ACT_WAITING_FOR_DIALOG}))
    reading = first_entry(trace, READING_ACTIONS)
    assert waiting is None
    assert reading["timer"] == 500
