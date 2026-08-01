"""The x-cam derivation gate's pure core (tools/derive_xcam.py).

This probe scores itself — the human only plays — so nothing downstream will
catch a scoring bug the way a human reading a screen would have. Three failures
are specifically fatal and each has a case below: scoring a grab against a
STALE result store (a flawless match for a comparison that never happened),
reading the counter from a DIFFERENT memory read than the one that saw the
action change (the counter is the number being measured), and reporting
"nothing fits" for a derivation that fits within our own read skew.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from sm64_events.memory.addresses import (ACT_FALL_AFTER_STAR_GRAB,  # noqa: E402
                                          ACT_STAR_DANCE_EXIT)
from derive_xcam import (ACTION, DANCE_ACTIONS, FRAME, OVERALL,     # noqa: E402
                         RESULT, SAMPLING_SKEW, SETTLE_FRAMES,
                         PendingGrab, candidates, errors,
                         first_sample_in, grab_report, settled_result,
                         summary, was_midair)

TOUCH = 9000
ACT_RUNNING = 0x00000440


class FakeSnapshot:
    def __init__(self, global_timer, mario_action, igt_overall, igt_result):
        self.global_timer, self.mario_action = global_timer, mario_action
        self.igt_overall, self.igt_result = igt_overall, igt_result


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


# --- ground truth ----------------------------------------------------------

def test_a_result_store_that_never_moved_yields_no_ground_truth():
    """The fatal case. Under STOP=GRAB — and, his own report 2026-08-01,
    under ANY setting for a star grabbed on the ground — the write lands
    before we start watching, so the store already holds this grab's value.
    Scoring against it would report a flawless match for a comparison that
    never happened."""
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
    samples = [(frame, action, overall,
                601 if 2 <= frame - TOUCH < 20 else result)
               for frame, action, overall, result in samples]
    assert settled_result(samples) == (620, TOUCH + 20)


# --- finding the moment ----------------------------------------------------

def test_a_midair_grab_reaches_the_dance_later_than_a_ground_grab():
    """The whole question, and his words for it (2026-08-01): running into a
    star makes grab and x-cam identical, backflipping into one puts tens of
    frames between them. If both produced the same candidate frame the probe
    could not separate the moments at all."""
    ground, midair = stream(600, 60, dance_at=0), stream(600, 60, dance_at=39,
                                                         fall_until=39)
    assert first_sample_in(ground, DANCE_ACTIONS)[FRAME] == TOUCH
    assert first_sample_in(midair, DANCE_ACTIONS)[FRAME] == TOUCH + 39
    assert not was_midair(ground)
    assert was_midair(midair)


def test_the_counter_is_read_from_the_same_sample_as_the_action():
    """A 60 Hz poll over a 30 fps game stores two samples per frame, and a
    snapshot is twelve separate reads that can straddle a frame. Looking the
    counter up BY FRAME NUMBER can answer with a different read than the one
    that saw the transition — and the counter IS the number being measured.
    Live 2026-08-01 the errors came back -1, -2, -1, -1; this was the one
    source of that -2 we could remove rather than tolerate."""
    torn = [
        (TOUCH, ACT_RUNNING, 600, 452),
        (TOUCH + 1, ACT_RUNNING, 601, 452),
        (TOUCH + 1, ACT_STAR_DANCE_EXIT, 602, 452),   # same frame, later read
    ]
    assert candidates(torn, TOUCH)["star dance entry"] == (TOUCH + 1, 602)


def test_only_one_sample_per_game_frame_is_kept():
    """The other half of the same fix: at 60 Hz a duplicate frame is just a
    second chance to store a torn read."""
    grab = PendingGrab(1, None, TOUCH)
    grab.observe(FakeSnapshot(TOUCH, ACT_RUNNING, 600, 0))
    grab.observe(FakeSnapshot(TOUCH, ACT_RUNNING, 600, 0))
    grab.observe(FakeSnapshot(TOUCH + 1, ACT_STAR_DANCE_EXIT, 601, 0))
    assert [sample[FRAME] for sample in grab.samples] == [TOUCH, TOUCH + 1]


# --- scoring ---------------------------------------------------------------

def test_perfect_agreement_reports_the_calibration_exactly():
    assert errors([{"a": 2}, {"a": 2}, {"a": 2}])["a"] == ([2, 2, 2], 2)


def test_one_frame_of_disagreement_still_supports_the_constant():
    """His real run, verbatim: the dance-entry candidate scored -1, -2, -1, -1.
    Demanding EXACT agreement called that 'nothing fits' — a derivation with
    one noisy sample reported as a failed hypothesis, which is the more
    expensive error of the two because it argues for abandoning the right
    answer."""
    values, supported = errors([{"dance": -1}, {"dance": -2},
                                {"dance": -1}, {"dance": -1}])["dance"]
    assert values == [-1, -2, -1, -1]
    assert supported == -1              # the modal value, not the mean
    assert SAMPLING_SKEW == 1


def test_two_frames_of_disagreement_is_still_varies():
    """The tolerance is exactly our measured read skew. Wider and it would
    start endorsing candidates that are simply wrong — which is what 'our grab
    edge' is, and it must never be endorsed."""
    assert errors([{"edge": -11}, {"edge": -39}])["edge"][1] is None
    assert errors([{"a": -1}, {"a": -3}])["a"][1] is None


def test_a_candidate_seen_on_only_some_grabs_is_still_judged():
    """A midair-only candidate is exactly that shape; dropping partial rows
    would discard the half the question is about."""
    assert errors([{"a": 1, "b": 4}, {"a": 1}])["b"] == ([4], 4)


def test_the_summary_refuses_to_pick_a_winner_when_nothing_fits():
    """'Nothing fits' is a real finding — x-cam would not be an action
    transition at all. Reporting the closest candidate would bury it under a
    number that looks like an answer."""
    text = summary([{"star dance entry": 2}, {"star dance entry": 9}])
    assert "VARIES" in text
    assert "Nothing landed on one error" in text
    assert "CONSTANT" not in text


def test_the_summary_names_the_winner_and_inverts_the_offset_for_the_caller():
    """The error is candidate MINUS Usamune; the CALLER needs Usamune, so the
    line has to hand back the correction to apply, not the error to admire."""
    text = summary([{"star dance entry": -1}, {"star dance entry": -1}])
    assert "CONSTANT -1" in text
    assert "is the x-cam moment" in text
    assert "+1" in text                 # counter -(-1) = counter +1


def test_the_summary_marks_a_tolerated_sample_rather_than_hiding_it():
    """A ± verdict must stay visibly different from an exact one, and every
    raw error stays printed — a tolerance that silently smoothed its input is
    how a wrong constant survives."""
    text = summary([{"dance": -1}, {"dance": -2}, {"dance": -1}])
    assert "CONSTANT -1 ±1" in text
    assert "-2" in text
    assert "OUR read" in text


# --- reports ---------------------------------------------------------------

def test_a_grab_with_no_ground_truth_scores_nothing_and_says_why():
    """It must contribute zero rows to the summary — a skipped grab that
    silently scored would poison every verdict after it — and it must name the
    GROUND-grab case, which is the one he hit twice without expecting to."""
    samples = stream(600, 60, dance_at=0, stale_result=452)
    text, scored = grab_report(1, "Lethal Lava Land", "Red-Hot Log Rolling",
                               615, "result", TOUCH, samples)
    assert scored == {}
    assert "GROUND grab" in text
    assert "x-cam IS the grab frame" in text


def test_a_scoreable_grab_prices_every_candidate_against_usamune():
    samples = stream(600, 60, dance_at=39, fall_until=39,
                     result_at=39, result_value=639, stale_result=452)
    text, scored = grab_report(1, "Whomp's Fortress",
                               "Fall onto the Caged Island",
                               600, "counter", TOUCH, samples)
    assert scored["our grab edge"] == 600 - 639        # our edge is 39 low
    assert scored["star dance entry"] == 0             # the landing is exact
    assert "midair grab" in text and "Usamune's answer" in text


def test_the_window_outlasts_the_longest_observed_write():
    """Live 2026-08-01: the latest result write landed +39 frames after the
    touch, on a grab that fell first. A window near that would drop the very
    grabs the derivation exists to handle."""
    assert SETTLE_FRAMES >= 120


def test_the_sample_tuple_layout_is_what_every_helper_assumes():
    """Four indices shared by six functions and the live loop. A silent
    reorder would make every reading wrong and every test still pass."""
    assert (FRAME, ACTION, OVERALL, RESULT) == (0, 1, 2, 3)
