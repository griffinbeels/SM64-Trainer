"""ui/climbplan.js — a level-up climb as an ordered list of steps.

The feature this file guards (spec 2026-07-27-multi-rank-climb):

    "The bar should STAY FULL WHILE RANKING UP GOING FORWARD. ONCE IT'S FULL,
     AND IF WE HAVE MORE RANKS TO FULLY LEVEL UP THROUGH, IT STAYS FULL."
    (user, 2026-07-27)

and, for a rank the climb passes ENTIRELY through, condensing it rather than
playing all five of its subdivisions.

Structure is asserted against FIXED timings, so a tuning round moves numbers
without touching these; the wall-clock tests below say so explicitly and use
the real table.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
PLAN_JS = UI / "climbplan.js"
CURVE_JS = UI / "climbcurve.js"
CAPS_JS = UI / "components" / "caps.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")

# Deliberately unlike the shipped numbers, and all distinct, so a step that
# reads the wrong duration shows up as a wrong number rather than blending in.
FIXED_TIMINGS = ("({ barSweepMs: () => 100, ladderMs: 11,"
                 " anticipateMs: 22, payoffMs: 33 })")

# Iron V is level 0 and each tier is five divisions, so:
IRON_V, IRON_I = 0, 4
BRONZE_V, BRONZE_I = 5, 9
SILVER_I = 14
GOLD_V, GOLD_IV = 15, 16


def run_node(body: str):
    script = (f"import {{ buildClimbPlan }} from {PLAN_JS.as_uri()!r};\n"
              f"import {{ climbTimings }} from {CURVE_JS.as_uri()!r};\n"
              "import { rankAt, rankPosition, wingTiers, capName, divisionDigit,"
              f" DIVISIONS_PER_TIER }} from {CAPS_JS.as_uri()!r};\n"
              f"const FIXED = {FIXED_TIMINGS};\n"
              "const plan = (from, to, skipStyle, timings) => buildClimbPlan({\n"
              "  fromLevel: from[0], fromFill: from[1],\n"
              "  toLevel: to[0], toFill: to[1],\n"
              "  divisionsPerTier: DIVISIONS_PER_TIER,\n"
              "  skipStyle, timings: timings || (() => FIXED),\n"
              "});\n"
              f"{body}")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def shape(from_level, from_fill, to_level, to_fill, style):
    """The plan as `[kind, "Capless 5", barFrom, barTo]` rows."""
    return run_node(
        f"const p = plan([{from_level}, {from_fill}], [{to_level}, {to_fill}],"
        f" {style!r});\n"
        "console.log(JSON.stringify(p.steps.map((s) => [s.kind,\n"
        "  `${capName(rankAt(s.level).tier)} ${divisionDigit(rankAt(s.level).division)}`,\n"
        "  s.barFrom, s.barTo])));")


# ---- The user's worked example -------------------------------------------
#
# "Capless 5 start. Get a PB that pushes me to Waluigi 4." Spelled out beat by
# beat in the report, so it is transcribed here beat by beat.

def test_capless_five_to_waluigi_four_pops_the_wings_of_each_skipped_rank():
    assert shape(IRON_V, 0.3, GOLD_IV, 0.04, "pop") == [
        ["approach", "Capless 5", 0.3, 1],
        # "So, I've ranked up to Capless 4. The bar stays full."
        ["division", "Capless 4", 1, 1],
        ["division", "Capless 3", 1, 1],
        ["division", "Capless 2", 1, 1],
        ["division", "Capless 1", 1, 1],
        # "Now, bar is STILL full. We do the big awesome Capless -> Toad
        #  animation. Bar stays full."
        ["anticipate", "Capless 1", 1, 1],
        ["tier", "Toad 5", 1, 1],
        # "IMMEDIATELY TRANSITION INTO THE MAX RANK OF THE NEXT DIVISON"
        ["tierskip", "Toad 1", 1, 1],
        ["anticipate", "Toad 1", 1, 1],
        ["tier", "Toadsworth 5", 1, 1],
        ["tierskip", "Toadsworth 1", 1, 1],
        ["anticipate", "Toadsworth 1", 1, 1],
        ["tier", "Waluigi 5", 1, 1],
        # "It levels up from Waluigi 5 to Waluigi 4, the wings grow"
        ["division", "Waluigi 4", 1, 1],
        # "the bar should now grow from 0 to our actual position inside the bar"
        ["arrive", "Waluigi 4", 0, 0.04],
    ]


def test_capless_five_to_waluigi_four_can_instead_chain_the_caps():
    """The style the user asked to judge side by side: "we would go CAPLESS ->
    TOAD, TOAD -> TOADSWORTH, TOADSWORTH -> WALUIGI immediately, where we
    basically stay at full wings the entire time."" """
    assert shape(IRON_V, 0.3, GOLD_IV, 0.04, "chain") == [
        ["approach", "Capless 5", 0.3, 1],
        ["division", "Capless 4", 1, 1],
        ["division", "Capless 3", 1, 1],
        ["division", "Capless 2", 1, 1],
        ["division", "Capless 1", 1, 1],
        ["anticipate", "Capless 1", 1, 1],
        # Straight onto division I — no V to pass through, so no wings come off
        ["tier", "Toad 1", 1, 1],
        ["anticipate", "Toad 1", 1, 1],
        ["tier", "Toadsworth 1", 1, 1],
        ["anticipate", "Toadsworth 1", 1, 1],
        # Waluigi is where the climb STOPS, so it is entered normally
        ["tier", "Waluigi 5", 1, 1],
        ["division", "Waluigi 4", 1, 1],
        ["arrive", "Waluigi 4", 0, 0.04],
    ]


@pytest.mark.parametrize("style", ["pop", "chain"])
def test_the_chained_caps_keep_their_wings_and_the_popped_ones_regrow_them(style):
    """The visible difference between the two styles, stated as WINGS rather
    than as step names — that is what the user is actually choosing between.

    Measured over the stretch between the first tier crossing and the last one,
    which is the only part of the climb the styles disagree about: everything
    before it is the tier you started in (Capless, which never wears wings at
    any division) and everything after is the destination tier, entered at its
    own wingless division V by both styles.
    """
    wings = run_node(
        f"const p = plan([{IRON_V}, 0], [{GOLD_IV}, 0.04], {style!r});\n"
        "const crossings = p.steps"
        "  .map((s, index) => (s.kind === 'tier' ? index : -1))"
        "  .filter((index) => index >= 0);\n"
        "console.log(JSON.stringify(p.steps"
        "  .slice(crossings[0], crossings[crossings.length - 1])"
        "  .map((s) => wingTiers(rankAt(s.level).tier, rankAt(s.level).division))));")
    assert wings, "the example crosses three tiers -- something ate the middle"
    if style == "chain":
        assert wings == [4] * len(wings), (
            "chain must never drop the wings between two crossings: "
            f"{wings}")
    else:
        assert 0 in wings and 4 in wings, (
            f"pop must land on a wingless division V and grow them back: {wings}")


# ---- The pin --------------------------------------------------------------

CLIMBS = [
    (IRON_V, 0.3, GOLD_IV, 0.04),      # the worked example
    (IRON_V, 0.0, 44, 1.0),            # the whole ladder, the rarest climb there is
    (BRONZE_V, 0.9, BRONZE_I, 0.5),    # four divisions inside one tier
    (IRON_I, 0.2, BRONZE_V, 0.0),      # one tier crossing and nothing else
    (SILVER_I, 0.1, GOLD_V, 0.77),     # crossing that lands on the tier floor
    (GOLD_V, 0.1, GOLD_V, 0.8),        # a better time, same rank
    (BRONZE_V, 1.0, BRONZE_V + 2, 0.3),  # resuming a climb whose bar is pinned
]


@pytest.mark.parametrize("style", ["pop", "chain"])
@pytest.mark.parametrize("climb", CLIMBS)
def test_the_bar_is_full_from_the_first_rank_up_until_the_arrival(climb, style):
    from_level, from_fill, to_level, to_fill = climb
    steps = shape(from_level, from_fill, to_level, to_fill, style)
    kinds = [kind for kind, _rank, _a, _b in steps]
    if to_level <= from_level:
        assert kinds == ["fill"]
        return
    assert kinds[-1] == "arrive"
    ladder = steps[1:-1] if kinds[0] == "approach" else steps[:-1]
    assert ladder, "a climb that changes rank must have something in between"
    for kind, rank, bar_from, bar_to in ladder:
        assert (bar_from, bar_to) == (1, 1), (
            f"{kind} on {rank} moved the bar; every step between the first "
            "rank-up and the arrival is pinned full")
    if kinds[0] == "approach":
        assert steps[0][3] == 1, "the approach must reach full"
    assert steps[-1][2] == 0, "the arrival starts from empty"
    assert steps[-1][3] == to_fill


@pytest.mark.parametrize("style", ["pop", "chain"])
@pytest.mark.parametrize("climb", CLIMBS)
def test_the_bar_only_gives_progress_back_at_the_arrival(climb, style):
    """The bar may reset exactly ONCE, entering the arrival — "we finally
    leveled up to where we need to be, so let's show you how close you are…
    animate from empty to our actual progress". Anywhere else it would be the
    original backwards-bar bug wearing a new coat."""
    from_level, from_fill, to_level, to_fill = climb
    steps = shape(from_level, from_fill, to_level, to_fill, style)
    drops = [index for index in range(1, len(steps))
             if steps[index][2] < steps[index - 1][3] - 1e-12]
    assert drops in ([], [len(steps) - 1]), (
        f"the bar dropped at step(s) {drops} of {[s[0] for s in steps]}")


# ---- Only a tier you pass ENTIRELY through gets condensed -----------------

def test_the_tier_you_start_in_is_climbed_division_by_division():
    """Those divisions are progress the player actually made — the user's own
    walk spends four steps on them. Only a tier the climb enters AND leaves is
    condensed."""
    for style in ("pop", "chain"):
        steps = shape(BRONZE_V + 2, 0.4, SILVER_I, 0.2, style)
        kinds = [kind for kind, _rank, _a, _b in steps]
        assert kinds.count("division") >= 2, (
            "the two divisions left in the starting tier must still be climbed")
        assert "tierskip" not in kinds, (
            "neither the starting tier (entered mid-way) nor the destination "
            "tier (never left) may be condensed")


def test_the_destination_tier_is_climbed_division_by_division():
    steps = shape(BRONZE_I, 0.5, SILVER_I, 0.2, "pop")
    kinds = [kind for kind, _rank, _a, _b in steps]
    assert kinds.count("division") == 4, (
        "Toadsworth V -> I is where the climb stops, so all four of its steps "
        "are shown")
    assert "tierskip" not in kinds


def test_a_tier_entered_and_left_is_condensed_to_one_step():
    steps = shape(IRON_I, 0.5, SILVER_I, 0.2, "pop")
    kinds = [kind for kind, _rank, _a, _b in steps]
    assert kinds.count("tierskip") == 1, "Toad is passed straight through"
    assert kinds.count("division") == 4, "Toadsworth is where it lands"


# ---- Properties that hold for every climb on the ladder -------------------

def test_a_tier_boundary_is_exactly_a_level_divisible_by_the_tier_size():
    """climbplan.js takes that as given rather than importing caps.js, which is
    what keeps it import-free. So it gets checked against caps.js here instead
    of trusted — the two must agree on all 45 levels."""
    disagreements = run_node(
        "const out = [];\n"
        "for (let level = 1; level <= 44; level += 1) {\n"
        "  const crossed = rankAt(level).tier !== rankAt(level - 1).tier;\n"
        "  if (crossed !== (level % DIVISIONS_PER_TIER === 0)) out.push(level);\n"
        "}\n"
        "console.log(JSON.stringify(out));")
    assert disagreements == []


@pytest.mark.parametrize("style", ["pop", "chain"])
def test_every_climb_on_the_ladder_is_well_formed(style):
    """All 990 rising (from, to) level pairs. Four invariants, each of which a
    hand-picked table would have let through somewhere: steps are contiguous in
    time, the ranks shown only ever go UP, the plan ends on the destination,
    and no sweep is longer than one division (the whole reason climbcurve.js
    could drop its trapezoid)."""
    bad = run_node(
        f"const style = {style!r};\n"
        "const bad = [];\n"
        "for (let from = 0; from <= 44; from += 1)\n"
        "  for (let to = from + 1; to <= 44; to += 1) {\n"
        "    const p = plan([from, 0.25], [to, 0.6], style);\n"
        "    let clock = 0, shown = from;\n"
        "    for (const step of p.steps) {\n"
        "      if (Math.abs(step.at - clock) > 1e-9) bad.push([from, to, 'gap', step.kind]);\n"
        "      if (step.ms <= 0) bad.push([from, to, 'zero-length', step.kind]);\n"
        "      clock += step.ms;\n"
        "      if (step.level < shown) bad.push([from, to, 'went down', step.kind]);\n"
        "      shown = step.level;\n"
        "      if (Math.abs(step.barTo - step.barFrom) > 1)\n"
        "        bad.push([from, to, 'sweep over one division', step.kind]);\n"
        "    }\n"
        "    if (Math.abs(clock - p.totalMs) > 1e-9) bad.push([from, to, 'total']);\n"
        "    if (shown !== to) bad.push([from, to, 'ended on ' + shown]);\n"
        "    const last = p.steps[p.steps.length - 1];\n"
        "    if (last.kind !== 'arrive') bad.push([from, to, 'ends on ' + last.kind]);\n"
        "  }\n"
        "console.log(JSON.stringify(bad.slice(0, 12)));")
    assert bad == []


@pytest.mark.parametrize("style", ["pop", "chain"])
def test_the_ladder_step_count_is_bounded_without_needing_a_budget(style):
    """<=4 climbing out of the tier you started in, <=4 into the one you land
    in, <=7 whole tiers passed through. The budget in climbcurve.js is a
    belt-and-braces on top of this, not the thing making it finite."""
    worst = run_node(
        f"const style = {style!r};\n"
        "let worst = 0;\n"
        "for (let from = 0; from <= 44; from += 1)\n"
        "  for (let to = from + 1; to <= 44; to += 1)\n"
        "    worst = Math.max(worst, plan([from, 0], [to, 1], style).ladder);\n"
        "console.log(JSON.stringify(worst));")
    assert worst <= 15


# ---- Wall clock, with the real timing table -------------------------------

def test_the_worked_example_and_the_worst_case_stay_watchable():
    """Wall clock, against the REFERENCE tuning rather than the live defaults.

    The defaults in ui/climbtuning.js belong to the user since 2026-07-27 —
    the inspector writes them back into the repo — so asserting them here would
    turn the suite red for every tuning session, which is the tool working. The
    law worth pinning is that the SHAPE stays watchable at the feel this design
    was drawn around; the live values only have to be in range, which is
    tests/test_ui_climbtuning.py's job.
    """
    example_pop, example_chain, worst_pop, worst_chain = run_node(
        "const REF = { barSweepFullMs: 1500, barSweepMinMs: 450,\n"
        "  ladderStepMs: 460, ladderBudgetMs: 3400, ladderStepMinMs: 220,\n"
        "  tierDwellMs: 1600, tierDwellBudgetMs: 5200, tierDwellMinMs: 700,\n"
        "  anticipateShare: 0.56 };\n"
        "const real = (counts) => climbTimings(counts, REF);\n"
        "const ms = (from, to, style) => plan(from, to, style, real).totalMs;\n"
        "console.log(JSON.stringify([\n"
        f"  ms([{IRON_V}, 0.3], [{GOLD_IV}, 0.04], 'pop'),\n"
        f"  ms([{IRON_V}, 0.3], [{GOLD_IV}, 0.04], 'chain'),\n"
        "  ms([0, 0], [44, 1], 'pop'),\n"
        "  ms([0, 0], [44, 1], 'chain')]));")
    assert 8000 <= example_pop <= 10000, example_pop
    assert example_chain < example_pop, (
        "chaining the caps drops the skip steps, so it must be shorter")
    # The hold has a 20s ceiling (rankclimb.js) and a celebration nobody can
    # sit through is worse than one that is slightly too short.
    assert worst_pop <= 14000, worst_pop
    # NOT shorter, and that is correct rather than a bug worth chasing: the
    # whole-ladder climb saturates the ladder budget in BOTH styles (15 steps
    # at 227ms, 8 steps at 425ms — 3400ms either way), so chaining buys back
    # length only while a climb is small enough to still be paying full price
    # per step, which is every climb a human will ever actually see.
    assert worst_chain <= worst_pop + 1, (worst_chain, worst_pop)
