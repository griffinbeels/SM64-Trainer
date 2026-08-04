"""Standards columns run SLOWEST on the left to FASTEST on the right.

User, 2026-08-03, on a WF table reading Half Cycle Skip / Half Cycle / Kanno
Cycle / Pro Cycle: *"we always sort by the Mario time. Fastest mario time
strategies go on the far right. Slowest go on the far left... they would start
from the slowest strat and ranking (bottom left), and move to the top of that
strat (top of left column), then move onto the next strat towards the right.
So as they get better, they'd move from left to right across the rank
standards. This should be for ALL rank standards now and forever."*

The table is a PATH, which is what decides the awkward cases: a ladder with no
Mario cutoff, and a strategy with no times at all.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ORDER_JS = REPO / "src" / "sm64_events" / "ui" / "ladderorder.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")


def _order(strategies, ladders):
    script = (f"import {{ slowestFirst }} from {ORDER_JS.as_uri()!r};\n"
              f"console.log(JSON.stringify(slowestFirst("
              f"{json.dumps(strategies)}, {json.dumps(ladders)})));")
    out = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True,
                         # subprocess decodes with the Windows ANSI codepage
                         # unless told otherwise, which mojibakes the middle
                         # dot in every variant-qualified strategy name.
                         encoding="utf-8")
    return json.loads(out.stdout)


# His own screenshot: WF's 100-coin ladder, in the order it rendered WRONG.
WF = {
    "Half Cycle Skip": {"Mario": 60.13, "Bronze": 69.60},
    "Half Cycle": {"Mario": 66.03, "Bronze": 89.36},
    "Kanno Cycle": {"Mario": 56.06, "Platinum": 58.90},
    "Pro Cycle": {"Mario": 57.70, "Gold": 62.10},
}


def test_the_reported_table_reads_slow_to_fast():
    assert _order(list(WF), WF) == [
        "Half Cycle", "Half Cycle Skip", "Pro Cycle", "Kanno Cycle"]


def test_a_ladder_with_no_mario_cutoff_sorts_by_its_own_fastest():
    """A ladder may legitimately skip tiers. Dropping it to the left edge
    would put a genuinely quick strategy at the START of the path."""
    ladders = {"Fast": {"Grandmaster": 40.0}, "Slow": {"Mario": 90.0}}
    assert _order(["Fast", "Slow"], ladders) == ["Slow", "Fast"]


def test_a_strategy_with_no_times_at_all_starts_the_path():
    """Unproven, not slow — and the left edge is where a run starts. A strat
    the caller lists but the store has no ladder for behaves the same."""
    ladders = {"Timed": {"Mario": 30.0}}
    assert _order(["Timed", "Brand New"], ladders) == ["Brand New", "Timed"]
    assert _order(["Brand New", "Timed"], ladders) == ["Brand New", "Timed"]


def test_two_unproven_strategies_keep_their_given_order():
    """Both keys are Infinity, and `Infinity - Infinity` is NaN — a comparator
    returning NaN has undefined results, not merely wrong ones."""
    assert _order(["A", "B", "C"], {}) == ["A", "B", "C"]


def test_adding_a_strategy_never_reshuffles_its_neighbours():
    """Ties keep the order they arrived in, so a new column slots in rather
    than rearranging the table around it."""
    ladders = {"A": {"Mario": 50.0}, "B": {"Mario": 50.0}, "C": {"Mario": 70.0}}
    assert _order(["A", "B", "C"], ladders) == ["C", "A", "B"]
    ladders["D"] = {"Mario": 50.0}
    assert _order(["A", "B", "C", "D"], ladders) == ["C", "A", "B", "D"]


def test_every_seeded_entity_ends_up_monotone():
    """Over the whole bundled seed, not a fixture: each entity's columns must
    be non-increasing in their ceiling, left to right."""
    seed = json.loads(
        (REPO / "src" / "sm64_events" / "data" / "rank_standards.seed.json")
        .read_text(encoding="utf-8"))
    checked = 0
    for key, entity in seed["entities"].items():
        ladders = entity.get("strategies", {})
        if len(ladders) < 2:
            continue
        ordered = _order(list(ladders), ladders)
        keys = [ladders[name].get("Mario")
                or (min(ladders[name].values()) if ladders[name] else None)
                for name in ordered]
        present = [k for k in keys if k is not None]
        assert present == sorted(present, reverse=True), (key, ordered, keys)
        checked += 1
    assert checked > 40, f"only {checked} multi-strategy entities checked"
