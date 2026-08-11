"""`tools/score_moment_clock.py` turns one screenshot into a scored offset.

A star scores itself (`derive_xcam.py` reads Usamune's own result store); a
door writes nothing, so the ground truth is the number on his screen and the
only question is whether reading it is cheap and unambiguous enough that
nobody argues from memory again. `MomentDetector.DISPLAY_LAG_FRAMES` moved by
±1 in three consecutive rounds before this existed.

What these tests pin is the property that makes the tool outlive the next
flip: an offset is measured against the RAW COUNTER, never against the time we
published — so a door journaled before a change and one journaled after score
identically off the same screen reading.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from score_moment_clock import (code_offset, render_scores, score,  # noqa: E402
                                scored_by_kind, verdict)

# The parser is asserted at its canonical home -- the tool only imports it.
from sm64_events.core.timefmt import format_igt, parse_igt  # noqa: E402
from sm64_events.detectors.moment import MomentDetector  # noqa: E402
from sm64_events.memory.addresses import (ACT_PULLING_DOOR,  # noqa: E402
                                          ACT_READING_NPC_DIALOG)

sys.path.insert(0, str(REPO / "tests"))
from test_moment import ACT_WALKING, run, snap  # noqa: E402


def row(event_id, counter, ours, kind="door_open"):
    return {"id": event_id, "wall": "2026-08-06T00:00:00Z", "kind": kind,
            "counter": counter, "ours": ours, "action_timer": 1,
            "level": 7, "area": 1, "landmark": "7:1:door"}


# -- reading his screen back into frames --------------------------------------

def test_every_frame_a_display_can_show_parses_back_to_itself():
    """Exact rather than approximate, and that is a property of the format:
    centiseconds are `frames % 30 * 100 // 30`, injective over 0..29, so no
    two frames share a display and nothing here is a guess."""
    for frames in range(0, 5400):
        assert parse_igt(format_igt(frames)) == frames


def test_a_reading_that_is_not_a_usamune_time_is_refused():
    with pytest.raises(ValueError):
        parse_igt("66.83")


def test_a_centisecond_the_game_never_displays_is_refused():
    """01 is not reachable at 30 fps. Accepting it would silently attribute a
    mistyped reading to the nearest frame and score a real offset off it."""
    with pytest.raises(ValueError):
        parse_igt("1'06\"01")


# -- scoring against the counter, not against ourselves -----------------------

def test_a_reading_names_its_moment_and_the_offset_from_the_raw_counter():
    scored = score([row(2279, counter=2003, ours=2004)], ["1'06\"83"])
    assert scored[0].state == "scored"
    assert scored[0].matches[0].row["id"] == 2279
    assert scored[0].matches[0].offset == 2


def test_the_same_reading_scores_the_same_either_side_of_a_constant_flip():
    """THE point of the tool. Round 6 published `counter + 1` and round 7
    publishes `counter + 2`; both rows below are the same door on his screen.
    Scored against what we published, they would disagree by a frame and the
    evidence would reset with every round."""
    before = score([row(1, counter=2003, ours=2004)], ["1'06\"83"])
    after = score([row(2, counter=2003, ours=2005)], ["1'06\"83"])
    assert before[0].matches[0].offset == after[0].matches[0].offset == 2


def test_two_moments_close_enough_to_share_a_reading_are_AMBIGUOUS():
    """Silently taking the nearest would manufacture an offset out of a
    coincidence -- exactly the recalled-comparison failure this replaces."""
    scored = score([row(1, counter=2003, ours=2005),
                    row(2, counter=2004, ours=2006)], ["1'06\"83"])
    assert scored[0].state == "ambiguous"
    assert {m.offset for m in scored[0].matches} == {1, 2}
    assert verdict(scored) == (None, "no reading could be attributed to a "
                                     "single moment")


def test_a_reading_matching_no_moment_is_reported_rather_than_forced():
    scored = score([row(1, counter=2003, ours=2005)], ["0'01\"00"])
    assert scored[0].state == "unmatched"


def test_readings_that_disagree_refuse_to_settle_on_an_offset():
    """If Usamune's lead over the counter is not constant, saying so IS the
    finding -- a tool that averaged them would hide the one result that
    invalidates the whole approach."""
    scored = score([row(1, counter=2003, ours=2005),
                    row(2, counter=1607, ours=1609)],
                   ["1'06\"83", "0'53\"60"])
    offset, why = verdict(scored)
    assert offset is None and "disagree" in why


def test_agreeing_readings_settle_on_the_one_offset():
    scored = score([row(1, counter=2003, ours=2005),
                    row(2, counter=1607, ours=1609)],
                   ["1'06\"83", "0'53\"63"])
    assert verdict(scored)[0] == 2


# -- what the shipped code adds -----------------------------------------------

def test_the_shipped_offset_is_derived_from_the_detector_not_restated():
    """A tool that hardcoded the number it is scoring could report AGREES
    while the server journaled something else. This runs a real door edge
    through the real detector and compares."""
    events = run([snap(ACT_WALKING, 100, igt_overall=777),
                  snap(ACT_PULLING_DOOR, 101, igt_overall=777)])
    assert code_offset() == events[0].payload["igt_frames"] - 777


def test_the_shipped_offset_tracks_the_constant_it_is_scoring():
    """Mutation proof in code: move the constant and the tool's report moves
    with it, so a stale literal in the tool cannot survive."""
    original = MomentDetector.DISPLAY_LAG_FRAMES
    try:
        MomentDetector.DISPLAY_LAG_FRAMES = original + 3
        assert code_offset() == original + 3 + 1  # + IgtClock.DISPLAY_TICK
    finally:
        MomentDetector.DISPLAY_LAG_FRAMES = original


# -- kind-aware shipped offsets (round 4, 2026-08-11) --------------------------
# `code_offset()` used to hardcode a DOOR edge as "the shipped code", so a
# CORRECT textbox reading (counter + 3, since round 3's extra_lag_frames)
# would score as a mismatch against a door's counter + 2. Both kinds now
# derive their own offset from the same MOMENTS registry moment.py itself
# reads, rather than one literal standing in for every kind.

def test_a_textbox_carries_one_more_shipped_frame_than_a_door():
    """Mirrors moment.py's own measured claim (round 3): a textbox's shipped
    offset is a door's plus its own `extra_lag_frames` (1), not a copy of the
    door's number."""
    assert code_offset("textbox") == code_offset("door_open") + 1


def test_the_textbox_offset_is_cross_checked_against_a_real_detector_run():
    """Same synthetic shape as moment.py's own round-3 test (the turn through
    mario_action_state 0..8, then the box opens), via the SAME `run()` helper
    that test -- independent proof that `code_offset("textbox")` doesn't
    just agree with itself, it agrees with the detector everyone else's
    tests drive."""
    events = run([
        snap(ACT_WALKING, 100, igt_overall=336),
        *[snap(ACT_READING_NPC_DIALOG, 101 + state, mario_action_state=state,
               igt_overall=336)
          for state in range(9)],
    ])
    assert code_offset("textbox") == events[0].payload["igt_frames"] - 336


def test_a_caused_kind_raises_rather_than_pretending_to_a_shipped_offset():
    """switch_press/enemy_defeated are CAUSED moments (detectors/caused.py,
    empty action set) -- this tool cannot run them through MomentDetector,
    and inventing a number for them would be worse than saying so."""
    with pytest.raises(ValueError, match="CAUSED moment"):
        code_offset("switch_press")


def test_a_door_row_and_a_textbox_row_each_score_against_their_own_shipped_offset():
    """The false-report class this branch has fixed twice: a reading that
    genuinely agrees with the shipped code must not be told it disagrees
    because the tool checked it against a DIFFERENT kind's constant."""
    door = row(2279, counter=2003, ours=2005, kind="door_open")
    textbox = row(355, counter=336, ours=339, kind="textbox")
    scored = score([door, textbox], ["1'06\"83", "0'11\"30"])
    by_kind = scored_by_kind(scored)
    door_offset, _ = verdict(by_kind["door_open"])
    textbox_offset, _ = verdict(by_kind["textbox"])
    assert door_offset == code_offset("door_open")
    assert textbox_offset == code_offset("textbox")
    assert door_offset != textbox_offset


def test_the_verdict_line_names_its_kind_and_reads_as_agreeing():
    """What the controller shows him: a reader who lands on this line needs
    no other context to know a textbox reading agreed with the shipped
    code."""
    textbox = row(355, counter=336, ours=339, kind="textbox")
    output = render_scores(score([textbox], ["0'11\"30"]))
    verdict_line = next(line for line in output.splitlines()
                        if line.startswith("VERDICT"))
    assert verdict_line == (
        "VERDICT (textbox): +3 measured, +3 shipped — AGREES "
        "(1 reading(s), unanimous)")
