"""A rank progress bar is anchored at its midpoint, and only the floor is empty.

User, 2026-07-29: "All rank displays (MARELO, Rank Standards, etc) should start
from the MIDDLE OF THE BAR. Then, we scale the progress towards their next rank
from the middle of the bar to the end of the bar. The intention is to anchor the
user towards feeling like they ALWAYS are making progress to the next rank...
The exception to this rule is for the Capless 5 case -- we've literally never
practiced this thing, so it should be empty. Once we level up to Capless 4, it
should start at least from the middle, hence forth for the remainder of the
ranks."

Two different things are checked here and they are not interchangeable. The LAW
is arithmetic and node runs caps.js for real (it is import-free for exactly this
reason). The DOOR is a source shape: a bar drawn from a raw `fill` would look
perfectly correct in isolation and simply disagree with every other bar on the
page -- the same class of divergence tests/test_single_source.py exists for,
where nobody copy-pasted anything and each surface was written by someone who
could not see the others.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.source_scan import code_only

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "src" / "sm64_events" / "ui"
CAPS_JS = UI / "components" / "caps.js"


def run_node(imports: str, body: str):
    script = f"import {{ {imports} }} from {CAPS_JS.as_uri()!r};\n{body}"
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def drawn():
    """{f"{tier} {numeral}": {fill: drawn width}} over the whole ladder."""
    return run_node(
        "RANK_NAMES, DIVISION_NUMERALS, barFill",
        """
        const fills = [0, 0.001, 0.25, 0.5, 0.75, 0.999, 1];
        const out = {};
        for (const tier of RANK_NAMES) {
          for (const numeral of DIVISION_NUMERALS) {
            out[`${tier} ${numeral}`] = fills.map(
              (fill) => [fill, barFill(tier, numeral, fill)]);
          }
        }
        console.log(JSON.stringify(out));
        """)


def test_only_the_ladder_floor_ever_draws_an_empty_bar(drawn):
    empty = [rank for rank, samples in drawn.items()
             if any(width == 0 for _, width in samples)]
    assert empty == ["Iron V"], (
        "exactly one rank may draw an empty bar -- the ladder floor, where a "
        f"strategy nobody has practiced sits. These do: {empty}")


def test_the_floor_draws_its_own_true_progress(drawn):
    """Not anchored, and not forced to zero either: at the floor the bar is
    the honest measure, which is what makes 'never practiced' read as empty
    without lying about a slow run that HAS been practiced."""
    assert drawn["Iron V"] == [[fill, fill] for fill, _ in drawn["Iron V"]]


def test_every_other_rank_starts_at_the_middle_and_scales_to_the_end(drawn):
    for rank, samples in drawn.items():
        if rank == "Iron V":
            continue
        for fill, width in samples:
            assert width == pytest.approx(0.5 + 0.5 * fill), rank


def test_a_finished_division_always_draws_a_full_bar(drawn):
    """The pin the climb relies on: every step between the first rank-up and
    the arrival holds the bar at 1 (ui/climbplan.js), and the anchoring must
    not turn that into a bar that stops short.

    This is also the FLOOR SEAM. A climb out of Capless V sweeps the floor's
    un-anchored bar up to 1 and then holds it there while the rank ticks --
    two different maps either side of that boundary, meeting at 1.0 in both,
    which is what makes the transition invisible instead of a jump."""
    for rank, samples in drawn.items():
        assert dict(samples)[1] == 1.0, rank


def test_a_rank_that_is_not_on_the_ladder_draws_nothing():
    assert run_node("barFill", """console.log(JSON.stringify([
      barFill(null, null, 0.8), barFill("Nonesuch", "IV", 0.8),
      barFill("Gold", "IX", 0.8)]));""") == [0, 0, 0]


def test_out_of_range_fills_are_clamped_before_anchoring():
    assert run_node("barFill", """console.log(JSON.stringify([
      barFill("Gold", "IV", -3), barFill("Gold", "IV", 9),
      barFill("Gold", "IV", undefined)]));""") == [0.5, 1, 0.5]


# ---- The closing sweep ----------------------------------------------------
#
# "when we fill up the meter on the final beat of the animation, it STARTS AT
# 50% visually, which is wrong. It should START AT 0% visually, and move to
# the destination %… but ALWAYS END PAST 50%" (user, 2026-07-29).
#
# ui/rankclimb.js converts the bar to a DRAWN width once, at the plan's
# boundary, so climbplan's existing `arrive: barFrom 0 -> barTo` means EMPTY
# -> the destination's anchored width. These drive the same composition and
# check the result, rather than restating the arithmetic.

CLIMBPLAN_JS = UI / "climbplan.js"


def plan_bars(from_tier, from_division, from_fill, to_tier, to_division, to_fill):
    """Every (barFrom, barTo) the plan holds, in DRAWN units -- built the way
    rankclimb.js builds it."""
    script = f"""
    import {{ barFill, rankPosition, DIVISIONS_PER_TIER }} from {CAPS_JS.as_uri()!r};
    import {{ buildClimbPlan }} from {CLIMBPLAN_JS.as_uri()!r};
    const plan = buildClimbPlan({{
      fromLevel: rankPosition({from_tier!r}, {from_division!r}, 0),
      fromFill: barFill({from_tier!r}, {from_division!r}, {from_fill}),
      toLevel: rankPosition({to_tier!r}, {to_division!r}, 0),
      toFill: barFill({to_tier!r}, {to_division!r}, {to_fill}),
      divisionsPerTier: DIVISIONS_PER_TIER,
      timings: () => ({{ barSweepMs: () => 100, ladderMs: 100,
                        anticipateMs: 100, payoffMs: 100 }}),
    }});
    console.log(JSON.stringify(plan.steps.map(
      (step) => [step.kind, step.barFrom, step.barTo])));
    """
    result = subprocess.run(["node", "--input-type=module", "-"], input=script,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("destination,fill", [
    (("Gold", "IV"), 0.0), (("Gold", "IV"), 0.4), (("Mario", "I"), 1.0),
    (("Iron", "IV"), 0.05),
])
def test_the_closing_sweep_starts_empty_and_ends_past_the_middle(destination, fill):
    steps = plan_bars("Iron", "V", 0.3, *destination, fill)
    arrive = [step for step in steps if step[0] == "arrive"]
    assert len(arrive) == 1, steps
    _kind, starts, ends = arrive[0]
    assert starts == 0, "the closing sweep must restart the bar from EMPTY"
    assert ends >= 0.5, "and must always land past the middle of the track"


def test_the_whole_climb_never_moves_the_bar_backwards():
    """The anchoring introduces a SECOND map (the floor's own), so the seam
    between them is a new way for the bar to jump. Walked over every rising
    pair on the ladder, not sampled: this is the exact shape of the original
    backwards-bar bug, whose end states both looked correct."""
    ranks = run_node("RANK_NAMES, DIVISION_NUMERALS",
                     "console.log(JSON.stringify(RANK_NAMES.flatMap("
                     "(tier) => DIVISION_NUMERALS.map((d) => [tier, d]))));")
    floor = ("Iron", "V")
    for destination in ranks:
        if destination == floor:
            continue
        steps = plan_bars(*floor, 0.3, *destination, 0.6)
        # Every step but the arrive is a continuation; the arrive is the one
        # deliberate reset, so it is checked by the test above instead.
        rising = [step for step in steps if step[0] != "arrive"]
        for (_kind, starts, ends), (_next, next_starts, _next_ends) in \
                zip(rising, rising[1:]):
            assert ends >= starts, (destination, rising)
            assert next_starts >= ends - 1e-9, (destination, rising)


# ---- The door -------------------------------------------------------------

WIDTH_EXPR = re.compile(r"width:\s*\$\{([^}]*)\}")
IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
# The ONE pre-anchored value a component may paint without calling barFill
# itself: ui/rankclimb.js's own output, which it derives through barFill at
# the plan's boundary. `test_the_climb_anchors_the_bar_it_hands_out` below is
# what makes accepting it legitimate rather than a hole.
PRE_ANCHORED = "climb.bar"


def reaches_bar_fill(expression: str, source: str, depth: int = 4) -> bool:
    """Whether `expression` resolves through `caps.js::barFill`, following
    same-file `const`/`let` bindings. One level of indirection is the normal
    shape here (a component computes a percentage above its own markup), and
    a guard that could not see through it would pass on any code at all."""
    if "barFill(" in expression or PRE_ANCHORED in expression:
        return True
    if depth <= 0:
        return False
    for name in set(IDENTIFIER.findall(expression)):
        binding = re.search(rf"\b(?:const|let|var)\s+{re.escape(name)}\s*=\s*([^;]*);",
                            source)
        if binding and reaches_bar_fill(binding.group(1), source, depth - 1):
            return True
    return False


PLAN_FILL_ARG = re.compile(r"(?:from|to)Fill:\s*([^,\n}]+)")


def test_the_climb_anchors_the_bar_it_hands_out():
    """`climb.bar` is only safe to paint unconverted because the hook feeds
    the plan DRAWN widths. Both endpoints, because the closing sweep's
    "always ends past the middle" comes from `toFill` and the approach's
    "continues from where it was" comes from `fromFill`."""
    source = code_only(UI / "rankclimb.js")
    args = PLAN_FILL_ARG.findall(source)
    assert len(args) == 2, f"expected one from/to pair into the plan, got {args}"
    for arg in args:
        assert reaches_bar_fill(arg.replace(PRE_ANCHORED, ""), source), (
            f"ui/rankclimb.js hands the climb plan `{arg.strip()}`, which is "
            "not a drawn width -- the closing sweep will start half full "
            "again and every surface reading `climb.bar` inherits it.")


def ui_js():
    return sorted([*UI.glob("*.js"), *(UI / "components").glob("*.js")])


@pytest.mark.parametrize("path", ui_js(), ids=lambda p: p.name)
def test_no_width_is_ever_drawn_from_a_raw_fill(path):
    """The check is not "is barFill imported" -- that passes happily while a
    second path exists beside it. It is: can a bar's width be written from a
    raw `fill` at all."""
    source = code_only(path)
    for expression in WIDTH_EXPR.findall(source):
        if "fill" not in expression.lower():
            continue
        assert reaches_bar_fill(expression, source), (
            f"{path.name} draws a width from `{expression.strip()}` without "
            "passing it through caps.js::barFill -- this bar will disagree "
            "with every other rank bar in the app about where empty is.")


def test_the_guard_can_still_fail():
    """A scan that matches nothing is green forever. Feed it the exact shape
    it exists to reject and the exact shape it must let through."""
    raw = 'const fillPct = climb.fill * 100;\n<i style=${`width:${fillPct}%`}></i>'
    routed = ('const fillPct = barFill(climb.tier, climb.division, climb.fill) * 100;\n'
              '<i style=${`width:${fillPct}%`}></i>')
    assert not reaches_bar_fill(WIDTH_EXPR.findall(raw)[0], raw)
    assert reaches_bar_fill(WIDTH_EXPR.findall(routed)[0], routed)


def test_both_rank_tracks_are_actually_covered_by_that_scan():
    """The scan above is vacuous on a file with no width expression, so name
    the two surfaces that must be in it -- the objective card's rank banners
    and the header's route rank card."""
    for name in ("ranks.js", "marelo.js"):
        source = code_only(UI / "components" / name)
        widths = [expression for expression in WIDTH_EXPR.findall(source)
                  if "fill" in expression.lower()]
        assert widths, f"{name} no longer draws a fill-derived width -- did " \
            "the rank track move? This file's guarantee moved with it."
