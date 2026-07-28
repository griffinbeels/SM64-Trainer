"""ui/climbcurve.js — how long each part of a level-up climb lasts.

**These tests must never assert the shipped defaults.** Since 2026-07-27 the
values in ui/climbtuning.js are the USER'S: the inspector at /ui/tune.html
writes them back into the repo, so every tuning session would otherwise turn
the suite red for doing exactly what the tool exists to do. What is pinned here
is the LAW — sqrt scaling, floors, shared budgets, monotone easing — exercised
against an explicit REFERENCE tuning standing for the feel this curve was
designed around. Whether the registry's own live values are coherent and in
range is tests/test_ui_climbtuning.py's job, which is where that belongs.

Two properties are worth a guard rather than a reading:

* a bar sweep is monotone AND lands exactly on its target — "we should NEVER
  overshoot in a progress bar. It reads as annoying and an error -- you gave me
  progress and then took it away!!!!" (user, 2026-07-27). Sampled at 1ms across
  the whole sweep, never at the endpoints: both end states already looked
  correct in the ORIGINAL backwards-bar bug, which is exactly how it survived
  review.
* a floor bounds the budget squeeze and NOTHING else. Putting it outside the
  ceiling let it silently override a value the user had set — see the bottom of
  this file for that incident.
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

# The feel this curve was designed around (spec 2026-07-27-multi-rank-climb).
# Deliberately NOT read from climbtuning.js — see the module docstring.
REFERENCE = {
    "barSweepFullMs": 1500, "barSweepMinMs": 450,
    "ladderStepMs": 460, "ladderBudgetMs": 3400, "ladderStepMinMs": 220,
    "tierDwellMs": 1600, "tierDwellMinMs": 700, "tierDwellMinAt": 7,
    "tierDwellCurve": 1, "anticipateShare": 0.56,
}


def run_node(imports: str, body: str):
    """Execute climbcurve.js for real — it is import-free specifically so node
    can unit-test it, the same convention as ui/climbplan.js and caps.js.
    `REF` is in scope for every body below."""
    script = (f"import {{ {imports} }} from {CLIMB_JS.as_uri()!r};\n"
              f"const REF = {json.dumps(REFERENCE)};\n{body}")
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

SWEEP_TARGETS = {
    0.09: 450,      # floored: a few percent reads as a flicker otherwise
    0.25: 750,
    0.5: 1061,
    1: 1500,        # a whole empty division filling
}


def test_the_sweep_duration_law_holds():
    got = run_node("barSweepMs", "console.log(JSON.stringify("
                   f"{json.dumps(list(SWEEP_TARGETS))}"
                   ".map((d) => barSweepMs(d, REF))))")
    for (distance, want), actual in zip(SWEEP_TARGETS.items(), got):
        assert isclose(actual, want, abs_tol=15), f"{distance} divisions -> {actual}ms"


def test_a_sweep_is_floored_and_capped_at_one_division():
    floored, zero, capped = run_node(
        "barSweepMs",
        "console.log(JSON.stringify([barSweepMs(0.004, REF), barSweepMs(0, REF),"
        " barSweepMs(4, REF)]))")
    assert floored == 450, "a few percent of a division must not read as a flicker"
    assert zero == 450, ("a sweep with nowhere to go still has to LAND -- a "
                         "zero-length step would make the arrival invisible")
    assert capped == 1500, "no sweep is longer than one division; clamp, never scale"


def test_the_sweep_law_is_smooth_in_distance():
    below, at, above = run_node(
        "barSweepMs",
        "console.log(JSON.stringify([barSweepMs(0.49, REF), barSweepMs(0.5, REF),"
        " barSweepMs(0.51, REF)]))")
    assert isclose(at, 1500 * sqrt(0.5), abs_tol=1)
    assert isclose(at - below, above - at, rel_tol=0.05)


def test_the_bar_never_overshoots_and_never_goes_backwards():
    """THE guard, and it is two-sided on purpose.

    Backwards was the original bug (a rank-up ran the bar .95 -> .05). The
    overshoot half is the user's own addendum, reported against a preview that
    eased to 8% and settled back to 4%. A one-sided monotonicity check passes
    an `easeOutBack` happily, which is exactly the curve that drew that
    complaint.

    Tuning-independent by construction: `barEase` takes no tuning, because the
    shape of a progress bar's travel is a style RULE for this app, not a knob.
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
    assert run_node("ladderStepMs",
                    "console.log(JSON.stringify(ladderStepMs(1, REF)))") == 460


def test_the_ladder_steps_share_a_budget_so_a_long_climb_stays_watchable():
    """A climb can hold at most 15 ladder steps (<=4 out of the tier you
    started in, <=4 into the one you land in, <=7 whole tiers passed through).
    Fifteen unhurried ones on top of eight tier dwells is a celebration nobody
    wants to sit through twice."""
    totals = run_node("ladderStepMs", "console.log(JSON.stringify("
                      "[1, 4, 7, 8, 15].map((n) => "
                      "[n * ladderStepMs(n, REF), ladderStepMs(n, REF)])))")
    per_step = [each for _total, each in totals]
    assert per_step == sorted(per_step, reverse=True), (
        "more steps must never make each one longer")
    assert all(each >= 220 for each in per_step), (
        "a step below the floor reads as a stutter, not a rank-up")
    assert max(total for total, _each in totals) <= 3500, (
        "the whole ladder budget must stay bounded")
    assert run_node("ladderStepMs",
                    "console.log(JSON.stringify(ladderStepMs(0, REF)))") == 0


def test_the_worked_example_is_not_compressed():
    """Capless V -> Waluigi IV in the `pop` style is 4 division steps out of
    Capless, 2 skipped tiers and 1 into Waluigi = 7 ladder steps. That climb is
    the spec's worked example and has to read at full pace."""
    assert run_node("ladderStepMs",
                    "console.log(JSON.stringify(ladderStepMs(7, REF)))") == 460


# ---- Tier dwells: the climb STOPS at every tier boundary -------------------

def test_one_tier_crossing_gets_the_full_dwell():
    dwell = run_node("tierDwell", "console.log(JSON.stringify(tierDwell(1, REF)))")
    assert dwell["anticipateMs"] + dwell["payoffMs"] == 1600
    assert dwell["anticipateMs"] > dwell["payoffMs"], (
        "the build-up is the longer half -- anticipation is what makes the "
        "release land")


def test_a_crossing_falls_off_from_one_tier_to_many():
    """User, 2026-07-27: "a single climb should be the max duration… when we
    have, say, 7 ranks to climb, it should scale down to some minimum, like
    200. And then the number of tiers along the way would interpolate between
    that min / max."

    Every clause of that is asserted: the endpoints land exactly, the middle
    interpolates, and it never turns back upward. This replaced a shared BUDGET
    whose 1/n curve spent almost all its fall-off between one crossing and
    three — a shape nobody chose and nothing could tune.
    """
    each = run_node("tierDwell", "console.log(JSON.stringify("
                    "[1, 2, 3, 4, 5, 6, 7, 8].map((n) => {"
                    " const d = tierDwell(n, REF); return d.anticipateMs + d.payoffMs;"
                    "})))")
    assert each[0] == 1600, "one crossing gets the full duration, exactly"
    assert each[6] == 700, "the fall-off count reaches the short duration, exactly"
    assert each[7] == 700, "and past it, it stays there rather than shrinking on"
    assert each == sorted(each, reverse=True), (
        "more crossings must never make a crossing longer")
    assert all(700 <= one <= 1600 for one in each), (
        "an interpolation may never leave its own endpoints")
    # Linear at curve 1: the midpoint of the span sits at the midpoint of the
    # durations. This is what makes the curve knob's effect legible.
    assert each[3] == 1150, each


def test_the_fall_off_curve_moves_where_the_shortening_happens():
    """Below 1 shortens hard on the first extra tier and levels off; above 1
    stays long and drops late. Without this the knob could be wired to nothing
    and every assertion above would still pass."""
    early, linear, late = run_node(
        "tierDwell",
        "const at = (curve) => { const d = tierDwell(2, { ...REF, tierDwellCurve: curve });"
        " return d.anticipateMs + d.payoffMs; };"
        "console.log(JSON.stringify([at(0.3), at(1), at(3)]));")
    assert early < linear < late, (early, linear, late)
    assert late > 1500, "a late curve must still be near the full duration at two"
    assert early < 1100, "an early curve must have given up most of it by two"


def test_no_crossings_means_no_dwell():
    dwell = run_node("tierDwell", "console.log(JSON.stringify(tierDwell(0, REF)))")
    assert dwell == {"anticipateMs": 0, "payoffMs": 0}


def test_the_timing_table_answers_in_the_shape_the_plan_asks_for():
    """`buildClimbPlan` destructures exactly these four; a rename here that the
    plan does not follow makes every step `undefined` ms long, which is a NaN
    clock rather than a wrong-looking animation."""
    keys, ladder, anticipate = run_node(
        "climbTimings",
        "const t = climbTimings({ crossings: 2, ladder: 3 }, REF);\n"
        "console.log(JSON.stringify([Object.keys(t).sort(), t.ladderMs, t.anticipateMs]));")
    assert keys == ["anticipateMs", "barSweepMs", "ladderMs", "payoffMs"]
    assert ladder == 460
    assert anticipate > 0


# ---- A floor may never override the ceiling above it ----------------------
#
# Live report, 2026-07-27: "the output was actually totally different that what
# I had changed my settings to... probably something to do with floors / me not
# realizing values can't be lower than the other floor setting." He was right,
# and it was worse than a save bug: nothing was mis-saved. All three durations
# were written `max(floor, min(ceiling, wanted))` with the floor OUTSIDE, so a
# ladder step of 100ms against the 220ms floor ran at 220 at EVERY step count
# while the inspector's slider showed 100. A whole tuning session was judged
# against a number no control on screen was displaying.

def test_no_floor_can_push_a_duration_past_the_ceiling_that_was_asked_for():
    """The property, over the whole space rather than the one combination that
    was reported — floors above their ceilings, below them, and equal, at every
    step count a climb can produce."""
    bad = run_node(
        "ladderStepMs, tierDwell, barSweepMs",
        "const bad = [];\n"
        "for (const ceiling of [40, 100, 220, 460, 1600])\n"
        "  for (const floor of [0, 100, 220, 700, 2000])\n"
        "    for (const budget of [200, 1000, 3400, 12000])\n"
        "      for (const n of [1, 2, 3, 5, 8, 15]) {\n"
        "        const tune = { ladderStepMs: ceiling, ladderStepMinMs: floor,\n"
        "          ladderBudgetMs: budget, tierDwellMs: ceiling,\n"
        "          tierDwellMinMs: floor,\n"
        "          anticipateShare: 0.5, barSweepFullMs: ceiling,\n"
        "          barSweepMinMs: floor };\n"
        "        const step = ladderStepMs(n, tune);\n"
        "        if (step > ceiling + 1e-9) bad.push(['ladder', ceiling, floor, budget, n, step]);\n"
        # A crossing is an INTERPOLATION between two endpoints now, not a
        # clamp, so its invariant is that it never leaves them — in either
        # direction, whichever way round the two are set.
        "        const dwell = tierDwell(n, { ...tune, tierDwellMinAt: 7, tierDwellCurve: 1 });\n"
        "        const each = dwell.anticipateMs + dwell.payoffMs;\n"
        "        const lo = Math.min(ceiling, floor), hi = Math.max(ceiling, floor);\n"
        "        if (each < lo - 1 || each > hi + 1) bad.push(['dwell', ceiling, floor, n, each]);\n"
        "        for (const d of [0, 0.04, 0.5, 1]) {\n"
        "          const sweep = barSweepMs(d, tune);\n"
        "          if (sweep > ceiling + 1e-9) bad.push(['sweep', ceiling, floor, d, sweep]);\n"
        "        }\n"
        "      }\n"
        "console.log(JSON.stringify(bad.slice(0, 10)));")
    assert bad == [], (
        "a floor pushed a duration ABOVE the ceiling it was given -- the "
        f"inspector's slider would show one number and the climb run another: {bad}")


def test_a_floor_still_stops_a_crowded_climb_squeezing_a_step_to_nothing():
    """The inverse, so the fix above cannot be 'delete the floor'. With room
    under the ceiling, the floor must still catch the budget squeeze."""
    roomy, squeezed = run_node(
        "ladderStepMs",
        "console.log(JSON.stringify([ladderStepMs(1, REF), ladderStepMs(40, REF)]));")
    assert roomy == 460, "one step must get the full length asked for"
    assert squeezed == 220, (
        "forty steps share a 3400ms budget (85ms each) and must be caught by "
        "the 220ms floor, not run as a stutter")
