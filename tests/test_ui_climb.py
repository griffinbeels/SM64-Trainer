"""ui/climbcurve.js — how long each part of a level-up climb lasts.

The climb is a PLAN of steps (ui/climbplan.js, tests/test_ui_climbplan.py);
this file owns only the numbers the plan sizes itself with. Two properties are
worth a guard rather than a reading:

* a bar sweep is monotone AND lands exactly on its target — "we should NEVER
  overshoot in a progress bar. It reads as annoying and an error -- you gave me
  progress and then took it away!!!!" (user, 2026-07-27). Sampled at 1ms across
  the whole sweep, never at the endpoints: both end states already looked
  correct in the ORIGINAL backwards-bar bug, which is exactly how it survived
  review.
* the per-step budgets stay bounded as a climb gets huge, so the rarest and
  biggest celebration in the game is still something you sit through once.
"""
import json
import shutil
import subprocess
from math import isclose, sqrt
from pathlib import Path

import pytest

CLIMB_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
            / "ui" / "climbcurve.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_node(imports: str, body: str):
    """Execute climbcurve.js for real — it is import-free specifically so node
    can unit-test it, the same convention as ui/climbplan.js and caps.js."""
    script = f"import {{ {imports} }} from {CLIMB_JS.as_uri()!r};\n{body}"
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# ---- Bar sweeps ----------------------------------------------------------
#
# A sweep is at most ONE division by construction: the bar is pinned full
# across every rank-up in between, so the only sweeps that exist are "fill up
# the division you were in", "grow into the one you landed in", and "you
# improved without ranking up".

# The durations the design targets, in ms. A FEEL decision (spec
# 2026-07-27-multi-rank-climb), not a derivation — if a tuning round moves
# them, this table moves with it deliberately.
SWEEP_TARGETS = {
    0.09: 450,      # floored: a few percent reads as a flicker otherwise
    0.25: 750,
    0.5: 1061,
    1: 1500,        # a whole empty division filling
}


def test_the_sweep_duration_targets_hold():
    got = run_node("barSweepMs", "console.log(JSON.stringify("
                   f"{json.dumps(list(SWEEP_TARGETS))}.map((d) => barSweepMs(d))))")
    for (distance, want), actual in zip(SWEEP_TARGETS.items(), got):
        assert isclose(actual, want, abs_tol=15), f"{distance} divisions -> {actual}ms"


def test_a_sweep_is_floored_and_capped_at_one_division():
    floored, zero, capped = run_node(
        "barSweepMs",
        "console.log(JSON.stringify([barSweepMs(0.004), barSweepMs(0),"
        " barSweepMs(4)]))")
    assert floored == 450, "a few percent of a division must not read as a flicker"
    assert zero == 450, ("a sweep with nowhere to go still has to LAND -- a "
                         "zero-length step would make the arrival invisible")
    assert capped == 1500, "no sweep is longer than one division; clamp, never scale"


def test_the_sweep_law_is_smooth_in_distance():
    below, at, above = run_node(
        "barSweepMs",
        "console.log(JSON.stringify([barSweepMs(0.49), barSweepMs(0.5),"
        " barSweepMs(0.51)]))")
    assert isclose(at, 1500 * sqrt(0.5), abs_tol=1)
    assert isclose(at - below, above - at, rel_tol=0.05)


def test_the_bar_never_overshoots_and_never_goes_backwards():
    """THE guard, and it is two-sided on purpose.

    Backwards was the original bug (a rank-up ran the bar .95 -> .05). The
    overshoot half is the user's own addendum, reported against a preview
    that eased to 8% and settled back to 4%: "we should NEVER overshoot in a
    progress bar... you gave me progress and then took it away!!!!" A
    one-sided monotonicity check passes an `easeOutBack` happily, which is
    exactly the curve that drew the complaint.
    """
    samples = run_node(
        "barEase",
        "const out = [];\n"
        "for (let t = -0.05; t <= 1.05; t += 0.001) out.push(barEase(t));\n"
        "console.log(JSON.stringify(out));")
    for index in range(1, len(samples)):
        assert samples[index] >= samples[index - 1] - 1e-12, (
            f"the bar went backwards at sample {index} "
            f"({samples[index - 1]} -> {samples[index]})")
        assert samples[index] <= 1 + 1e-12, (
            f"the bar overshot its target at sample {index}: {samples[index]}")
    assert samples[0] == 0, "clamped below, never negative"
    assert isclose(samples[-1], 1, rel_tol=1e-9)


def test_a_sweep_eases_in_and_out_rather_than_starting_at_full_speed():
    """"a slow crawl, easy ease into the pace… and then it eventually easy ease
    and slow down" — the original climb spec's ask, which survives the rewrite.
    """
    start, quarter, middle, end = run_node(
        "barEase", "console.log(JSON.stringify("
        "[barEase(0.02) , barEase(0.25), barEase(0.5), barEase(0.98)]))")
    assert start < 0.02, "a sweep must not leave at full speed"
    assert 1 - end < 0.02, "nor arrive at it"
    assert isclose(middle, 0.5, abs_tol=1e-9), "symmetric about the midpoint"
    assert quarter < 0.25


# ---- Ladder steps: one rank-up each --------------------------------------

def test_a_lone_rank_up_gets_the_full_step():
    assert run_node("ladderStepMs", "console.log(JSON.stringify(ladderStepMs(1)))") == 460


def test_the_ladder_steps_share_a_budget_so_a_long_climb_stays_watchable():
    """A climb can hold at most 15 ladder steps (<=4 out of the tier you
    started in, <=4 into the one you land in, <=7 whole tiers passed through).
    Fifteen unhurried ones on top of eight tier dwells is a celebration nobody
    wants to sit through twice."""
    totals = run_node("ladderStepMs", "console.log(JSON.stringify("
                      "[1, 4, 7, 8, 15].map((n) => [n * ladderStepMs(n), ladderStepMs(n)])))")
    per_step = [each for _total, each in totals]
    assert per_step == sorted(per_step, reverse=True), (
        "more steps must never make each one longer")
    assert all(each >= 220 for each in per_step), (
        "a step below the floor reads as a stutter, not a rank-up")
    assert max(total for total, _each in totals) <= 3500, (
        "the whole ladder budget must stay bounded")
    assert run_node("ladderStepMs", "console.log(JSON.stringify(ladderStepMs(0)))") == 0


def test_the_users_own_example_is_not_compressed():
    """Capless V -> Waluigi IV in the `pop` style is 4 division steps out of
    Capless, 2 skipped tiers and 1 into Waluigi = 7 ladder steps. That climb is
    the spec's worked example and has to read at full pace."""
    assert run_node("ladderStepMs", "console.log(JSON.stringify(ladderStepMs(7)))") == 460


# ---- Tier dwells: the climb STOPS at every tier boundary -------------------
#
# Cruising through a tier crossing threw away the biggest moment in the feature
# (live report 2026-07-27: "it needs to feel EXTRA juicy"). The climb halts at
# each one — anticipation, crossing, then a beat to look at it.

def test_one_tier_crossing_gets_the_full_dwell():
    dwell = run_node("tierDwell", "console.log(JSON.stringify(tierDwell(1)))")
    assert dwell["anticipateMs"] + dwell["payoffMs"] == 1600
    assert dwell["anticipateMs"] > dwell["payoffMs"], (
        "the build-up is the longer half -- anticipation is what makes the "
        "release land")


def test_the_dwells_share_a_budget_so_a_long_climb_stays_watchable():
    """Eight tier crossings at the full 1.6s each would hold the UI for
    thirteen seconds on top of the movement."""
    totals = run_node("tierDwell", "console.log(JSON.stringify("
                      "[1, 2, 4, 8, 20].map((n) => {"
                      " const d = tierDwell(n);"
                      " return [n * (d.anticipateMs + d.payoffMs), d.anticipateMs + d.payoffMs];"
                      "})))")
    per_crossing = [each for _total, each in totals]
    assert per_crossing == sorted(per_crossing, reverse=True), (
        "more crossings must never make each one longer")
    assert all(each >= 700 for each in per_crossing), (
        "a dwell below the floor reads as a stutter, not a pause")
    assert max(total for total, _each in totals) <= 14000, (
        "the whole dwell budget must stay bounded")


def test_no_crossings_means_no_dwell():
    dwell = run_node("tierDwell", "console.log(JSON.stringify(tierDwell(0)))")
    assert dwell == {"anticipateMs": 0, "payoffMs": 0}


def test_the_timing_table_answers_in_the_shape_the_plan_asks_for():
    """`buildClimbPlan` destructures exactly these four; a rename here that the
    plan does not follow makes every step `undefined` ms long, which is a NaN
    clock rather than a wrong-looking animation."""
    keys, ladder, anticipate = run_node(
        "climbTimings",
        "const t = climbTimings({ crossings: 2, ladder: 3 });\n"
        "console.log(JSON.stringify([Object.keys(t).sort(), t.ladderMs, t.anticipateMs]));")
    assert keys == ["anticipateMs", "barSweepMs", "ladderMs", "payoffMs"]
    assert ladder == 460
    assert anticipate > 0
