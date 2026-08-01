"""The x-cam derivation gate's pure core (tools/derive_xcam.py).

This probe scores itself — the human only plays — so nothing downstream will
catch a scoring bug the way a human reading a screen would have. Two failures
are specifically fatal here and each has a case below: scoring a grab against a
STALE result store (which would report a perfect match for a number Usamune
never wrote for that grab), and calling a candidate CONSTANT on one sample
(which would hand back a calibration constant derived from nothing).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from sm64_events.memory.addresses import (ACT_FALL_AFTER_STAR_GRAB,  # noqa: E402
                                          ACT_STAR_DANCE_EXIT)
from derive_xcam import (DANCE_ACTIONS, SETTLE_FRAMES, candidates,  # noqa: E402
                         counter_at, errors, first_frame_in,
                         grab_report, settled_result, summary)

TOUCH = 9000
ACT_RUNNING = 0x00000440


def stream(counter: int, length: int, dance_at: int | None = None,
           fall_until: int | None = None, result_at: int | None = None,
           result_value: int = 0, stale_result: int = 0):
    """`length` samples of (global_timer, mario_action, igt_overall,
    igt_result) from the touch frame on. The counter always keeps climbing —
    live 2026-08-01, `USAMUNE_OVERALL` ran on past every grab under every STOP
    value, which is the fact this whole derivation rests on."""
    out = []
    for offset in range(length):
        if fall_until is not None and offset < fall_until:
            action = ACT_FALL_AFTER_STAR_GRAB
        elif dance_at is not None and offset >= dance_at:
            action = ACT_STAR_DANCE_EXIT
        else:
            action = ACT_RUNNING
        result = (result_value
                  if result_at is not None and offset >= result_at
                  else stale_result)
        out.append((TOUCH + offset, action, counter + offset, result))
    return out


def test_a_result_store_that_never_moved_yields_no_ground_truth():
    """The fatal case. On STOP=GRAB the write lands before we start watching,
    so the store already holds this grab's value — scoring against it would
    report a flawless match for a comparison that never happened. It must come
    back as 'no truth', not as a number."""
    samples = stream(600, 60, dance_at=0, stale_result=452)
    assert settled_result(samples) is None


def test_a_result_write_inside_the_window_is_the_ground_truth():
    samples = stream(600, 60, dance_at=0, result_at=4, result_value=604,
                     stale_result=452)
    assert settled_result(samples) == (604, TOUCH + 4)


def test_the_last_write_wins_when_usamune_writes_twice():
    """STOP=GRABX writes at the grab and AGAIN at the x-cam (Usamune manual).
    The x-cam one is the legal time, so an earlier write must not shadow it."""
    samples = stream(600, 60, dance_at=0, result_at=20, result_value=620,
                     stale_result=452)
    samples = [(frame, action, overall, 601 if 2 <= frame - TOUCH < 20 else result)
               for frame, action, overall, result in samples]
    assert settled_result(samples) == (620, TOUCH + 20)


def test_a_midair_grab_lands_on_the_dance_not_on_the_grab():
    """The whole question. A ground grab is in the dance immediately; a midair
    one falls first and only reaches it on landing (live: a WF caged-island
    grab settled +39 frames after the touch). If those two produced the same
    candidate frame, the probe could not tell the moments apart at all."""
    ground = stream(600, 60, dance_at=0)
    midair = stream(600, 60, dance_at=39, fall_until=39)
    assert first_frame_in(ground, DANCE_ACTIONS) == TOUCH
    assert first_frame_in(midair, DANCE_ACTIONS) == TOUCH + 39
    assert first_frame_in(ground, frozenset({ACT_FALL_AFTER_STAR_GRAB})) is None


def test_a_candidate_outside_the_window_is_dropped_not_guessed():
    """Pricing a candidate we never sampled would put an invented number into
    a summary the human is asked to trust without checking."""
    samples = stream(600, 10, dance_at=0)
    assert counter_at(samples, TOUCH + 500) is None


def test_a_midair_grab_reports_the_landing_as_its_own_candidate():
    """Kept separate so a grab that never fell can be told from one that did —
    otherwise a derivation that only works for ground grabs would look
    universal."""
    midair = candidates(stream(600, 60, dance_at=39, fall_until=39), TOUCH)
    ground = candidates(stream(600, 60, dance_at=0), TOUCH)
    assert "landing after a fall" in midair
    assert "landing after a fall" not in ground
    assert midair["landing after a fall"] == (TOUCH + 39, 639)


def test_a_candidate_is_only_constant_when_every_grab_agrees():
    """One disagreeing grab is the difference between a derivation and a
    coincidence."""
    assert errors([{"a": 2}, {"a": 2}, {"a": 2}])["a"] == ([2, 2, 2], True)
    assert errors([{"a": 2}, {"a": 3}])["a"] == ([2, 3], False)


def test_a_candidate_seen_on_only_some_grabs_is_still_judged():
    """The midair candidate exists only on midair grabs. Dropping partial rows
    would discard exactly the half the question is about."""
    values, constant = errors([{"a": 1, "b": 4}, {"a": 1}])["b"]
    assert values == [4] and constant


def test_the_summary_refuses_to_pick_a_winner_when_nothing_is_constant():
    """'Nothing fits' is a finding — x-cam would not be a Mario-action
    transition at all. Reporting the closest candidate instead would bury it
    under a number that looks like an answer."""
    text = summary([{"star dance entry": 2}, {"star dance entry": 9}])
    assert "VARIES" in text
    assert "Nothing came back constant" in text
    assert "CONSTANT" not in text.split("SUMMARY")[1].split("errors:")[1]


def test_the_summary_names_the_constant_candidate_and_its_offset():
    text = summary([{"star dance entry": 2}, {"star dance entry": 2}])
    assert "CONSTANT +2" in text
    assert "is the derivation" in text


def test_a_grab_with_no_ground_truth_scores_nothing_and_says_why():
    """It must contribute zero rows to the summary — a skipped grab that
    silently scored would poison every verdict after it."""
    samples = stream(600, 60, dance_at=0, stale_result=452)
    text, scored = grab_report(1, "Lethal Lava Land", "8-Coin Puzzle",
                               597, "counter", TOUCH, samples)
    assert scored == {}
    assert "no result inside the window" in text
    assert "skipped" in text


def test_a_scoreable_grab_prices_every_candidate_against_usamune():
    samples = stream(600, 60, dance_at=39, fall_until=39,
                     result_at=39, result_value=639, stale_result=452)
    text, scored = grab_report(1, "Whomp's Fortress", "Fall onto the Caged Island",
                               600, "counter", TOUCH, samples)
    assert scored["our grab edge"] == 600 - 639        # our edge is 39 low
    assert scored["star dance entry"] == 0             # the landing is exact
    assert "Usamune's answer" in text


def test_the_window_outlasts_the_longest_observed_write():
    """Live 2026-08-01: the latest result write landed +39 frames after the
    touch, on a grab that fell first. A window near that would drop the very
    grabs the derivation exists to handle."""
    assert SETTLE_FRAMES >= 120
