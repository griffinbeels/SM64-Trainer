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


def test_an_answer_arriving_mid_fade_extends_the_hidden_window():
    """His rule, verbatim (2026-08-02): "when the selector DISAPPEARS, we need to
    figure out how we're coalescing. Figure out the result. Then display the
    final result. Therefore the animation ends up only happening ONCE."

    So each change absorbed while the row is invisible RE-ARMS the wait. Without
    this, a course swap whose answers land 250 ms apart pays for two animations —
    which is exactly what "it flashes again" was."""
    assert run_node("""
    const t = SELECTOR_DEFAULTS;
    let s = X.initialState("A");
    s = X.nextState(s, {type: "incoming", id: "B"});
    const first = X.phaseMs(X.OUT, t, s.absorbed);
    s = X.nextState(s, {type: "incoming", id: "C"});
    const again = X.phaseMs(X.OUT, t, s.absorbed);
    console.log(JSON.stringify([s.absorbed, first, again, first === again]));
    """) == [1, 210, 210, True]


def test_the_hidden_window_has_a_ceiling():
    """The bound is not optional: a window something can hold open indefinitely
    is a selector that never comes back. Past `maxHoldMs` the next answer is
    adopted immediately."""
    assert run_node("""
    const t = SELECTOR_DEFAULTS;
    const settle = t.outMs + t.gapMs;
    const cap = Math.ceil(t.maxHoldMs / settle);
    console.log(JSON.stringify([X.phaseMs(X.OUT, t, cap - 1) > 0,
                                X.phaseMs(X.OUT, t, cap + 1)]));
    """) == [True, 0]


def test_adopting_an_identity_remounts_what_it_paints():
    """The double flash, and why the fix cannot be left to callers.

    Live report: "when swapping between courses, it briefly flashes the previous
    course's stars and then flashes again… it should only trigger ONE animation."
    Two courses use the SAME row component, so Preact patched it rather than
    unmounting it; the inner `CellRow` survived and ran its own cell-set exchange
    while the outer one was still fading the surface in. The keyed Fragment makes
    surviving impossible, and it is keyed on what is PAINTED (`shownId`), not on
    what arrived — keying on the arrival would tear the outgoing content down
    mid-fade, which is the one frame this mechanism exists to hide."""
    source = code_only(UI / "components" / "cellrow.js")
    assert "key=${state.shownId}" in source, (
        "the painted subtree is no longer keyed on the adopted identity, so a "
        "child that survives an adoption can animate across it — the double "
        "flash")
    assert "key=${id}" not in source, (
        "keyed on the ARRIVING identity: that rebuilds the outgoing content "
        "mid-fade instead of freezing it")


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


def _surface_ids(cases: str) -> object:
    """Drive `stagecontext.selectorSurfaceId` in node. Cases are JS expressions
    building a fake store slot — `hasPracticeContext` needs only `view` plus a
    `stage.mode` off the practice list."""
    script = (f"import {{ selectorSurfaceId }} from "
              f"{(UI / 'stagecontext.js').as_uri()!r};\n"
              f"console.log(JSON.stringify({cases}));")
    done = subprocess.run(["node", "--input-type=module", "-"],
                          input=script, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_empty_state_is_its_own_surface():
    """His second round on this feature: "if there previously were no options
    available, but I transition to a stage with options, I would expect the
    animation to happen (right now it incorrectly cuts)." That only works if
    "nothing here" is a surface identity like any other."""
    ids = _surface_ids("""[
      selectorSurfaceId({view: {}, stage: {mode: null}}),
      selectorSurfaceId({view: {}, stage: {mode: "stars", level: 8, area: 1}}),
    ]""")
    assert ids[0] != ids[1] and ids[0] == "nothing-here"


def test_a_courses_own_subareas_are_the_same_surface():
    """SSL's pyramid interior offers the same seven stars as its desert, so the
    row must not blink on the way in. The cells decide their own changes."""
    ids = _surface_ids("""[
      selectorSurfaceId({view: {}, stage: {mode: "stars", level: 8, area: 1}}),
      selectorSurfaceId({view: {}, stage: {mode: "stars", level: 8, area: 2}}),
      selectorSurfaceId({view: {}, stage: {mode: "stars", level: 22, area: 1}}),
    ]""")
    assert ids[0] == ids[1], "a subarea change must not exchange the surface"
    assert ids[0] != ids[2], "a different course IS a different surface"


def test_the_castles_three_areas_are_three_surfaces():
    """The one place where the area DOES decide: the lobby, the basement and
    upstairs each offer a different set of movements."""
    ids = _surface_ids("""[1, 2, 3].map((area) =>
      selectorSurfaceId({view: {}, stage: {mode: "castle", level: 6, area}}))""")
    assert len(set(ids)) == 3, ids


def test_the_banner_wraps_everything_it_can_draw_in_one_exchange():
    """Including the placeholder, and ABOVE the row swap. A row component that
    unmounts takes its exchange state with it, so an exchange nested inside the
    rows can only ever animate a change WITHIN one row — which is exactly the
    gap he reported."""
    source = code_only(BANNER)
    start = source.index("export function StageBanner(")
    body = source[start:source.index("\nfunction ", start)]
    assert "return html`<${SurfaceExchange}" in body, (
        "the exchange must be the OUTERMOST thing StageBanner returns — inside "
        "a branch it unmounts with that branch, which is the bug")
    assert body.count("return") == 1, (
        f"StageBanner returns from more than one place, so at least one of its "
        f"surfaces bypasses the exchange:\n{body}")
    # The row components keep their own defensive `if (!course) return
    # StagePlaceholder` branches, and those are fine — they are already INSIDE
    # the exchange. What must never come back is StageBanner returning one.
    assert "StagePlaceholder" in body, \
        "the empty state must still be reachable, just from inside the wrapper"


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
