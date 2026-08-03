"""ui/exchange.js — the machine that makes an intermediate set of practice
options unobservable, and the single door every selector row draws through.

Live report 2026-08-02: "when we invalidate / add / remove cards from the menu
here, it feels more like a bug / error than intentional… internally we're doing
some shuffling / heartbeats / validations, but the user should never see that."

The claim under test is not "there is a fade" — it is that no set which was
never adopted can reach the screen. That is a property of the reducer plus ONE
render rule, so it is driven directly in node rather than inferred from a
screenshot, which could only ever show the frames it happened to catch.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from source_scan import code_only

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "src" / "sm64_events" / "ui"
EXCHANGE = UI / "exchange.js"
BANNER = UI / "components" / "stagebanner.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                               reason="node not on PATH")


def run_node(body: str) -> object:
    script = (f"import * as X from {EXCHANGE.as_uri()!r};\n"
              f"import {{ SELECTOR_DEFAULTS }} from "
              f"{(UI / 'selectortuning.js').as_uri()!r};\n{body}")
    done = subprocess.run(["node", "--input-type=module", "-"],
                          input=script, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_burst_of_changes_paints_the_first_set_then_the_last():
    """The whole feature. Four validations land inside the fade window — the
    shape a single walk through a door really produces (level edge, target
    retirement, topological cancels on the frame heartbeat, view refetch) — and
    the row paints exactly two sets: what he was looking at, and what is true
    now. B and C never appear."""
    painted = run_node("""
    let state = X.initialState("A");
    const seen = [];
    // The component's own render rule, verbatim: paint the arriving children
    // only when the machine has adopted their identity.
    const render = (id) => seen.push(X.paintsShown(id, state) ? id : "held");
    render("A");
    for (const id of ["B", "C", "D"]) {          // the burst
      render(id);                                 // renders BEFORE the effect
      state = X.nextState(state, {type: "incoming", id});
      render(id);
    }
    state = X.nextState(state, {type: "outDone"});
    render("D");
    state = X.nextState(state, {type: "inDone"});
    render("D");
    console.log(JSON.stringify(seen));
    """)
    assert painted == ["A", "held", "held", "held", "held", "held", "held",
                       "D", "D"]


def test_a_change_that_is_only_props_does_not_start_an_exchange():
    """A cell whose strategy, rank or active state changed is the SAME set of
    options. Fading the row for that would make every pick blink."""
    assert run_node("""
    const state = X.initialState("A");
    console.log(JSON.stringify(X.nextState(state, {type: "incoming", id: "A"})
                               === state));
    """) is True


def test_a_burst_that_cancels_itself_costs_one_blink_not_two():
    """Away and back inside the fade window — the set he already had. Coming up
    from where the fade got to beats swapping to an identical set and running a
    second exchange for no visible reason."""
    assert run_node("""
    let s = X.initialState("A");
    s = X.nextState(s, {type: "incoming", id: "B"});
    s = X.nextState(s, {type: "incoming", id: "A"});
    s = X.nextState(s, {type: "outDone"});
    console.log(JSON.stringify([s.phase, s.shownId]));
    """) == ["in", "A"]


def test_the_beat_is_charged_to_the_fade_out():
    """`gapMs` is empty time AFTER the old set is gone: the old cells stay
    mounted at zero opacity through it, because mounting the new ones early
    would make the swap frame the thing he sees."""
    assert run_node("""
    const t = SELECTOR_DEFAULTS;
    console.log(JSON.stringify([X.phaseMs(X.OUT, t) === t.outMs + t.gapMs,
                                X.phaseMs(X.IN, t) === t.inMs,
                                X.phaseMs(X.IDLE, t) === 0]));
    """) == [True, True, True]


def test_reduced_motion_snaps_with_no_transition():
    assert run_node("""
    const t = SELECTOR_DEFAULTS;
    const reduced = X.rowStyle(X.OUT, t, true);
    console.log(JSON.stringify([reduced.opacity, reduced.transitionMs,
                                X.rowStyle(X.OUT, t).opacity]));
    """) == [1, 0, 0]


def test_two_different_sets_can_never_share_an_identity():
    """A separator a key could contain would collide, and a collision reads as
    "nothing changed" — the one answer that skips an exchange that was owed."""
    assert run_node("""
    console.log(JSON.stringify(
      X.identityOf(["a|b"]) === X.identityOf(["a", "b"])));
    """) is False


def test_no_selector_row_draws_its_own_cell_container():
    """The single door. A row that renders `<div class="starrow">` itself looks
    completely correct and quietly opts out of the exchange, so the flicker
    comes back in one row while every other row stays smooth — the shape of
    every divergence this project has paid for. Comment-free source, since the
    class name is discussed in prose here and there."""
    source = code_only(BANNER)
    assert '<div class="starrow' not in source, (
        "a selector row is building its own cell container again; draw it "
        "through CellRow (ui/components/cellrow.js)")
    assert source.count("<${CellRow}") == 5, (
        "expected the four stage rows plus the armed-only row to go through "
        "CellRow — a new row must too")
