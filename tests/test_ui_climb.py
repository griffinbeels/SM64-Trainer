"""ui/climbcurve.js — the level-up climb's motion.

The bug this feature exists to kill (task 0012) is a progress bar that runs
BACKWARDS when you rank up: `RankBanner` animated the server's within-division
`fill`, so crossing a boundary tweened 95% -> 5%. The fix is to animate ladder
POSITION instead and take the bar from its fractional part, which makes
"the bar never goes backwards during a rise" a property of the curve rather
than a case someone has to remember to handle.

So that property is what gets asserted here, at 1ms granularity across the
whole climb — not the endpoints. Both end states already looked correct in the
shipped bug; sampling only those is exactly how it survived review.
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
    can unit-test it, the same convention as ui/entities.js and caps.js."""
    script = f"import {{ {imports} }} from {CLIMB_JS.as_uri()!r};\n{body}"
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# The durations the design targets, in ms. These are a FEEL decision (spec
# 2026-07-26-progression-celebration §2), not a derivation — if a tuning round
# moves them, this table moves with it deliberately.
DURATION_TARGETS = {
    0.25: 750,      # a fill improvement with no rank change
    1: 1500,        # one division
    5: 3354,        # one whole tier
    12: 5700,       # a multi-tier jump
}


def test_the_duration_targets_hold():
    got = run_node("climbDuration", "console.log(JSON.stringify("
                   f"{json.dumps(list(DURATION_TARGETS))}.map(climbDuration)))")
    for (distance, want), actual in zip(DURATION_TARGETS.items(), got):
        assert isclose(actual, want, abs_tol=15), f"{distance} divisions -> {actual}ms"


def test_a_twitch_is_floored_and_a_huge_jump_is_capped():
    floored, capped, nothing = run_node(
        "climbDuration",
        "console.log(JSON.stringify([climbDuration(0.004), climbDuration(40),"
        " climbDuration(0)]))")
    assert floored == 450, "a few percent of a division must not read as a flicker"
    assert capped == 7000, "no climb may hold the UI past the cap"
    assert nothing == 0


def test_the_two_halves_of_the_duration_law_are_one_curve():
    # C¹ continuity at the crossover: same value AND same slope from each
    # side, which is what stops a 5.01-division climb taking visibly longer
    # than a 4.99-division one.
    below, at, above = run_node(
        "climbDuration",
        "console.log(JSON.stringify([climbDuration(4.99), climbDuration(5),"
        " climbDuration(5.01)]))")
    assert isclose(at, 1500 * sqrt(5), abs_tol=1)
    assert isclose(at - below, above - at, rel_tol=0.02)


@pytest.mark.parametrize("distance", [0.05, 0.3, 1, 1.4, 4.99, 5, 5.01, 9, 12, 23])
def test_the_climb_never_moves_backwards(distance):
    """THE guard. Sampled every 1ms over the whole climb plus a margin past
    the end, since a real rAF frame can land after the deadline."""
    samples = run_node(
        "climbDuration, climbTravelled",
        f"const d = {distance};\n"
        "const total = climbDuration(d);\n"
        "const out = [];\n"
        "for (let t = -5; t <= total + 40; t += 1) out.push(climbTravelled(d, t));\n"
        "console.log(JSON.stringify(out));")
    for index in range(1, len(samples)):
        assert samples[index] >= samples[index - 1] - 1e-12, (
            f"{distance} divisions: travelled fell at sample {index} "
            f"({samples[index - 1]} -> {samples[index]})")
    assert samples[0] == 0
    assert isclose(samples[-1], distance, rel_tol=1e-9)


@pytest.mark.parametrize("distance", [1, 5, 12])
def test_the_bar_only_ever_fills(distance):
    """The rendered consequence of the guard above: the bar is the fractional
    part of the position, so between two frames the fill must either rise or
    reset — and it may only reset on a frame that crossed a level."""
    samples = run_node(
        "climbDuration, climbTravelled",
        f"const d = {distance};\n"
        "const total = climbDuration(d);\n"
        "const out = [];\n"
        "for (let t = 0; t <= total; t += 1) {\n"
        "  const x = climbTravelled(d, t);\n"
        "  out.push([Math.floor(x), x - Math.floor(x)]);\n"
        "}\n"
        "console.log(JSON.stringify(out));")
    for index in range(1, len(samples)):
        (previous_level, previous_fill) = samples[index - 1]
        (level, fill) = samples[index]
        if level == previous_level:
            assert fill >= previous_fill - 1e-12, (
                f"the bar shrank inside level {level} at sample {index}")
        else:
            assert level > previous_level, "a level was lost going up"


def test_the_middle_of_a_long_climb_is_speed_capped():
    """The reason this is a trapezoid and not an ease-in-out: an ease-in-out's
    peak speed scales with the distance, so the celebrations in the middle of
    a big jump would be unreadable."""
    peak_short, peak_long, cruise = run_node(
        "climbDuration, climbTravelled, CRUISE_DIVISIONS_PER_S",
        "const peak = (d) => {\n"
        "  const total = climbDuration(d);\n"
        "  let best = 0;\n"
        "  for (let t = 0; t < total; t += 1)\n"
        "    best = Math.max(best, (climbTravelled(d, t + 1) - climbTravelled(d, t)) * 1000);\n"
        "  return best;\n"
        "};\n"
        "console.log(JSON.stringify([peak(5), peak(20), CRUISE_DIVISIONS_PER_S]));")
    assert peak_short <= cruise * 1.02
    # A 20-division climb is past the duration cap, so it is allowed to exceed
    # cruise — but only by the amount the cap forces, not by scaling freely.
    assert peak_long <= cruise * 1.6, "the cap must not turn into an ease-in-out"


def test_a_drop_is_not_a_climb():
    """The app never animates a regression — it shows the new state. Anything
    else would spend four seconds celebrating a worse time."""
    landed, duration = run_node(
        "climbPosition, climbDurationBetween",
        "console.log(JSON.stringify([climbPosition(41.9, 38.2, 0),"
        " climbDurationBetween(41.9, 38.2)]))")
    assert landed == 38.2, "a drop lands immediately"
    assert duration == 0


def test_a_rise_starts_where_it_was_and_ends_where_it_is_going():
    start, middle, end = run_node(
        "climbPosition, climbDurationBetween",
        "const from = 41.95, to = 43.10;\n"
        "const total = climbDurationBetween(from, to);\n"
        "console.log(JSON.stringify([climbPosition(from, to, 0),"
        " climbPosition(from, to, total / 2), climbPosition(from, to, total)]));")
    assert start == 41.95, "the climb begins at the rank you actually had"
    assert 41.95 < middle < 43.10
    assert isclose(end, 43.10, rel_tol=1e-9)


# ---- Tier dwells: the climb STOPS at every tier boundary -------------------
#
# Cruising through a tier crossing at three divisions a second threw away the
# biggest moment in the feature (live report 2026-07-27: "it needs to feel
# EXTRA juicy"). The climb now halts at each one — anticipation, crossing,
# then a beat to look at it.

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
