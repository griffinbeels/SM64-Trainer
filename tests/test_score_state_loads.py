"""`tools/score_state_loads.py` asks whether a save-state LOAD is detectable.

Today `state_loaded` watches the emulator-wide frame counter, which a Usamune
section-state load never moves (anchors.py's docstring, 2026-09-01). The
candidate rule watches the OTHER clock instead: Usamune's own counter moving
by something play cannot produce. These tests pin the rule's shape so the
measurement over real traces means the same thing next round.

The one property worth protecting: every NOT-a-load verdict is named rather
than dropped. A tool that reports only its hits cannot tell "nothing happened"
from "the rule swallowed it", and the whole question here is what the rule
confuses with what.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from score_state_loads import (BACKWARD, CONSOLE_RESET, EMULATOR_LOAD,  # noqa: E402
                               FORWARD, FORWARD_SLACK, MAX_STEP_FRAMES,
                               RESTART_TO_ZERO, SAMPLE_GAP, STALL, STEADY,
                               WRAPAROUND, Sample, classify, journal_findings,
                               score_trace)

# The tool must not restate the shipped thresholds -- it imports them, so a
# change to the detector's own idea of "near zero" moves the score too.
from sm64_events.detectors.anchors import (BOOT_TIMER_MAX,  # noqa: E402
                                           IGT_WRAP_CEILING, NEAR_ZERO_IGT)


def play(start_frame, start_igt, frames, *, action=None):
    """Ordinary play: both clocks advance together, one per game frame."""
    return [Sample(start_frame + n, start_igt + n, action) for n in range(frames)]


def kinds(samples):
    return [finding.kind for finding in score_trace(samples).findings]


# -- what the rule claims IS a load -------------------------------------------

def test_a_backward_counter_jump_that_misses_zero_is_a_load():
    """The 2026-08-28 shape: the emulator frame counter never moved."""
    before = play(1000, 900, 5)
    after = play(1005, 400, 5)
    verdict = classify(before[-1], after[0])
    assert verdict.kind == BACKWARD
    assert verdict.moved == 400 - 904
    # the emulator-wide counter is monotonic across the whole trace
    frames = [s.frame for s in before + after]
    assert frames == sorted(frames)


def test_a_forward_counter_jump_no_play_could_produce_is_a_load():
    """Loading a state saved LATER than where you are moves it the other way."""
    before = play(1000, 400, 5)
    after = play(1005, 900, 5)
    verdict = classify(before[-1], after[0])
    assert verdict.kind == FORWARD


def test_a_counter_that_gains_at_most_the_frames_that_passed_is_play():
    for gained in range(0, 1 + FORWARD_SLACK + 1):
        verdict = classify(Sample(1000, 400), Sample(1001, 400 + gained))
        assert verdict.kind in (STEADY, STALL), gained
    assert classify(Sample(1000, 400),
                    Sample(1001, 400 + FORWARD_SLACK + 2)).kind == FORWARD


def test_an_emulator_savestate_still_scores_through_the_frame_counter():
    """A full-RAM restore rewinds gGlobalTimer -- the 56 loads the journals
    already hold. The rule must not lose the case it already catches."""
    verdict = classify(Sample(50_000, 900), Sample(20_000, 400))
    assert verdict.kind == EMULATOR_LOAD


# -- what the rule refuses to call a load -------------------------------------

def test_a_level_reset_drops_the_counter_to_near_zero_and_is_not_a_load():
    verdict = classify(Sample(1000, 900), Sample(1002, 2))
    assert verdict.kind == RESTART_TO_ZERO
    assert not verdict.is_load


def test_an_area_warp_is_the_same_zero_and_the_rule_cannot_tell_them_apart():
    """Usamune zeroes on an area load exactly as on an L-reset. The tool says
    so instead of guessing: both land in one bucket, and the warp flag only
    LABELS which one the rest of the system already decided it was."""
    reset = classify(Sample(1000, 900), Sample(1002, 2))
    warp = classify(Sample(1000, 900), Sample(1002, 2, warp_fired=True))
    assert reset.kind == warp.kind == RESTART_TO_ZERO
    assert reset.cause == "reset_or_area_load"
    assert warp.cause == "area_warp"


def test_the_near_zero_boundary_is_the_shipped_one():
    assert classify(Sample(1000, 900),
                    Sample(1002, NEAR_ZERO_IGT)).kind == RESTART_TO_ZERO
    assert classify(Sample(1000, 900),
                    Sample(1002, NEAR_ZERO_IGT + 1)).kind == BACKWARD


def test_a_pause_stalls_the_counter_and_is_not_a_load():
    stalled = [Sample(2000 + n, 500) for n in range(200)]
    assert set(kinds(stalled)) <= {STALL}
    assert score_trace(stalled).counts[STALL] == 199


def test_the_counter_wrapping_at_the_u16_ceiling_is_not_a_load():
    verdict = classify(Sample(1000, IGT_WRAP_CEILING + 100), Sample(1001, 4))
    assert verdict.kind == WRAPAROUND
    assert not verdict.is_load


def test_a_console_reset_is_the_lifecycle_detectors_and_not_a_load():
    verdict = classify(Sample(50_000, 900), Sample(BOOT_TIMER_MAX - 1, 0))
    assert verdict.kind == CONSOLE_RESET
    assert not verdict.is_load


def test_steady_play_produces_no_finding_at_all():
    assert kinds(play(1000, 100, 400)) == []


# -- what the rule REFUSES to score, and says so ------------------------------

def test_a_hole_in_the_trace_is_refused_rather_than_scored():
    """A gap can hide a reset and a climb back, which reads exactly like a
    backward jump. Scoring it would manufacture loads out of dropped samples."""
    verdict = classify(Sample(1000, 900), Sample(1000 + MAX_STEP_FRAMES + 1, 400))
    assert verdict.kind == SAMPLE_GAP
    assert not verdict.is_load


def test_a_state_saved_near_the_start_of_a_run_is_invisible_and_that_is_stated():
    """The rule's measured blind spot: a state whose counter sits inside the
    near-zero band is indistinguishable from an L-reset, forever."""
    verdict = classify(Sample(1000, 900), Sample(1001, NEAR_ZERO_IGT - 1))
    assert verdict.kind == RESTART_TO_ZERO
    assert verdict.blind_spot == "load_into_near_zero"


# -- the trace scorer ---------------------------------------------------------

def test_the_scorer_counts_every_step_including_the_quiet_ones():
    samples = play(1000, 100, 10) + play(1010, 400, 10)
    scored = score_trace(samples)
    assert sum(scored.counts.values()) == len(samples) - 1
    assert scored.counts[BACKWARD] == 0
    assert scored.counts[FORWARD] == 1
    assert scored.loads == 1


def test_a_sample_without_a_counter_reading_breaks_the_chain_safely():
    samples = [Sample(1000, 900), Sample(1001, None), Sample(1002, 400)]
    assert [f.kind for f in score_trace(samples).findings] == [SAMPLE_GAP, SAMPLE_GAP]


# -- the journal side: events are sparse, so the claim is weaker --------------

def grab(event_id, frame, igt, kind="star_collected", source="result"):
    return {"id": event_id, "session_id": 1, "type": kind, "frame": frame,
            "wall_time_utc": "2026-08-28T08:44:00Z",
            "igt_frames": igt, "igt_source": source}


def test_two_grabs_whose_times_go_backward_with_no_restart_between_them():
    """Session 19's shape. The counter only grows, so a lower time on the
    later grab needs at least one backward jump nothing else recorded."""
    rows = [grab(4119, 165044, 3645), grab(4126, 166193, 3319)]
    found = journal_findings(rows)
    assert [f.kind for f in found] == [BACKWARD]
    assert found[0].moved == -326


def test_a_restart_event_between_two_grabs_explains_the_drop():
    rows = [grab(1, 1000, 900),
            {"id": 2, "session_id": 1, "type": "practice_reset", "frame": 1100,
             "wall_time_utc": "x", "igt_frames": None, "igt_source": None},
            grab(3, 1200, 120)]
    assert journal_findings(rows) == []


def test_a_spawn_is_a_wall_between_pairs_and_never_an_end_of_one():
    """A `spawned` answers 0 by design, so pairing it with the next grab
    measures the counter's re-base rather than any jump. Scoring those ends
    reported 342 impossible forward jumps across the journals before this."""
    rows = [{"id": 1, "session_id": 1, "type": "spawned", "frame": 1000,
             "wall_time_utc": "x", "igt_frames": 0, "igt_source": "counter"},
            grab(2, 1200, 3000)]
    assert journal_findings(rows) == []


def test_a_session_boundary_never_joins_two_runs():
    rows = [grab(1, 1000, 900), dict(grab(2, 1200, 120), session_id=2)]
    assert journal_findings(rows) == []


@pytest.mark.parametrize("earlier,later", [(3645, 3319), (900, 899)])
def test_every_backward_pair_is_reported_with_its_size_not_thresholded_away(
        earlier, later):
    """A tight save-state loop lands a few frames apart; a threshold tuned to
    look clean would hide exactly the case the loop produces."""
    found = journal_findings([grab(1, 1000, earlier), grab(2, 2000, later)])
    assert len(found) == 1
    assert found[0].moved == later - earlier
