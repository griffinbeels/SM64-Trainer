"""ui/celebrations.js is THE celebration registry.

The user's requirement (2026-07-27) is that adding the next celebration — or
iterating on one of these — stays a single registry entry. That only holds if
a new entry is WIRED by construction, so what these tests check is that every
entry actually reaches something: a prop `Hat` reads, or a CSS variable
index.html uses. An entry that emits neither is dead on arrival and green
forever under any "is the registry imported" style of check.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "src" / "sm64_events" / "ui"
CELEBRATIONS_JS = UI / "celebrations.js"
HAT_JS = UI / "components" / "hat.js"
INDEX_HTML = UI / "index.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node on PATH")

RANKCLIMB_JS = UI / "rankclimb.js"
CLIMBPLAN_JS = UI / "climbplan.js"


def beat_kinds() -> set[str]:
    """Every kind the climb engine can emit, read OUT of the engine rather
    than listed here — a hand-kept list would let a registry entry listen for
    a kind that stopped existing and stay green.

    Two files now: the plan names the steps (`ui/climbplan.js`) and the loop
    adds the one kind no step carries, the settle. Extra names picked up here
    are harmless — the assertion below is that a registry entry never listens
    for something ABSENT, so a superset of the truth can only make it stricter
    where it matters and never looser."""
    kinds = set()
    for source in (CLIMBPLAN_JS, RANKCLIMB_JS):
        for line in source.read_text(encoding="utf-8").splitlines():
            if "kind" in line:
                kinds.update(re.findall(r'"(\w+)"', line))
    assert kinds, "no beat kinds found -- did makeBeat or the plan move?"
    return kinds


def run_node(body: str):
    script = (f"import {{ CELEBRATIONS, activeEffects, makeBeat }} "
              f"from {CELEBRATIONS_JS.as_uri()!r};\n{body}")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# A beat per kind, built through the registry's OWN makeBeat so the wing
# counts come from caps.js's policy rather than from this file's idea of it.
# Diamond II -> Diamond I is a division crossing that gains a wing pair;
# Gold I -> Platinum V is a tier crossing that sheds four; Gold V -> Gold I is
# a whole tier skipped at once, which grows all four (the `pop` style).
STEP_MS = 460
BEATS_JS = """
const beats = [
  makeBeat({ kind: "division", at: 0, level: 26, stepMs: 460,
             from: { tier: "Diamond", division: "II" },
             to: { tier: "Diamond", division: "I" },
             tiersGained: 0, divisionsGained: 1, anticipateMs: 900, payoffMs: 700 }),
  makeBeat({ kind: "tierskip", at: 0, level: 24, stepMs: 460,
             from: { tier: "Gold", division: "V" },
             to: { tier: "Gold", division: "I" },
             tiersGained: 1, divisionsGained: 5, anticipateMs: 900, payoffMs: 700 }),
  makeBeat({ kind: "anticipate", at: 0, level: 25, stepMs: 900,
             from: { tier: "Gold", division: "I" },
             to: { tier: "Platinum", division: "V" },
             tiersGained: 1, divisionsGained: 1, anticipateMs: 900, payoffMs: 700 }),
  makeBeat({ kind: "tier", at: 0, level: 25, stepMs: 700,
             from: { tier: "Gold", division: "I" },
             to: { tier: "Platinum", division: "V" },
             tiersGained: 1, divisionsGained: 1, anticipateMs: 900, payoffMs: 700 }),
  makeBeat({ kind: "settle", at: 0, level: 26, stepMs: 460,
             from: { tier: "Diamond", division: "I" },
             to: { tier: "Diamond", division: "I" },
             tiersGained: 1, divisionsGained: 6, anticipateMs: 900, payoffMs: 700 }),
];
"""


def contributions():
    """Every var and icon key the registry can actually produce, sampled
    across a beat of each kind and several points through each effect."""
    return run_node(BEATS_JS + """
const vars = new Set(), icon = new Set();
for (const at of [0, 60, 200, 400, 700, 1200, 1600]) {
  const active = activeEffects(beats, at);
  Object.keys(active.vars).forEach((key) => vars.add(key));
  Object.keys(active.icon).forEach((key) => icon.add(key));
}
console.log(JSON.stringify({ vars: [...vars].sort(), icon: [...icon].sort() }));
""")


def hat_props() -> set[str]:
    """The props Hat actually destructures — read out of the file, never a
    hand-kept list, or this guard drifts the moment Hat's signature does."""
    source = HAT_JS.read_text(encoding="utf-8")
    match = re.search(r"export function Hat\(\{(.*?)\}\)", source, re.S)
    assert match, "Hat's parameter list moved -- this guard cannot see it"
    return {name.strip() for name in re.findall(r"(\w+)\s*(?:=|,|$)", match.group(1))}


def test_every_registry_entry_declares_a_kind_the_engine_emits():
    kinds = run_node("console.log(JSON.stringify(Object.fromEntries("
                     "Object.entries(CELEBRATIONS).map(([name, entry]) => "
                     "[name, Array.isArray(entry.on) ? entry.on : [entry.on]]))));")
    emitted = beat_kinds()
    for name, listens_for in kinds.items():
        unknown = [kind for kind in listens_for if kind not in emitted]
        assert not unknown, (
            f"celebration {name!r} listens for {unknown}, which ui/rankclimb.js "
            f"never emits -- it would never fire. Emitted: {sorted(emitted)}")


def test_every_registry_entry_contributes_something():
    entries = run_node("console.log(JSON.stringify(Object.fromEntries("
                       "Object.entries(CELEBRATIONS).map(([name, entry]) => "
                       "[name, [!!entry.vars, !!entry.icon]]))));")
    for name, (has_vars, has_icon) in entries.items():
        assert has_vars or has_icon, (
            f"celebration {name!r} declares neither `vars` nor `icon`, so it "
            "cannot reach the page at all")


def test_every_icon_key_is_a_prop_the_hat_actually_reads():
    """The failure this catches: an entry emitting `wingGrow` when the prop is
    called `growWings`. Nothing errors — an unread prop is silently dropped —
    and the celebration simply never appears."""
    produced = contributions()["icon"]
    assert produced, "the registry produced no icon props at all"
    unread = sorted(set(produced) - hat_props())
    assert not unread, (
        f"ui/celebrations.js emits {unread}, which components/hat.js does not "
        "read. An unread prop is dropped silently, so the effect would just "
        "never render.")


def test_every_css_variable_is_used_by_the_stylesheet():
    """The mirror failure: an entry writing a variable no rule consumes."""
    produced = contributions()["vars"]
    assert produced, "the registry produced no CSS variables at all"
    stylesheet = INDEX_HTML.read_text(encoding="utf-8")
    unused = [name for name in produced if f"var({name}" not in stylesheet]
    assert not unused, (
        f"ui/celebrations.js writes {unused}, which nothing in index.html "
        "reads -- the effect would compute correctly and paint nothing")


def test_an_effect_is_only_active_inside_its_own_window():
    """`delay` and `ms` have to bound an effect, or two celebrations overlap
    into each other and a long climb becomes one continuous smear.

    Both may be FUNCTIONS of the beat now — the flap starts where the wings
    finish growing, and the grow fills whatever step length the plan handed
    it — so the window is resolved against a real beat rather than read off
    the entry as a constant."""
    windows = run_node(BEATS_JS + """
const wingFlap = CELEBRATIONS.wingFlap;
const beat = beats.find((one) => one.kind === "division");
const resolve = (value) => (typeof value === "function" ? value(beat) : value);
const delay = resolve(wingFlap.delay), ms = resolve(wingFlap.ms);
const before = activeEffects(beats, delay - 20);
const during = activeEffects(beats, delay + ms / 2);
const after = activeEffects(beats, delay + ms + 20);
console.log(JSON.stringify([delay, ms,
  'flapPhase' in before.icon, 'flapPhase' in during.icon, 'flapPhase' in after.icon]));
""")
    delay, length, before, during, after = windows
    assert delay == STEP_MS, "the flap must start where its own step's grow ends"
    assert length > 0
    assert [before, during, after] == [False, True, False], (
        "wingFlap leaked outside its own delay..delay+ms window")


def test_the_climb_holds_the_practice_page_through_a_celebration():
    """User, 2026-07-27: leaving the stage right after the grab must not cut
    the celebration off. What is asserted is the WIRING — that Practice reads
    a held selection rather than the live one — because the behaviour itself
    is only observable in a render (verified by a frame-by-frame CDP trace,
    see the spec's Testing section)."""
    practice = (UI / "components" / "practice.js").read_text(encoding="utf-8")
    assert "useHeldWhileCelebrating" in practice, (
        "practice.js no longer holds its selection during a celebration")
    # The held SELECTION only — holding the whole view deadlocks, because the
    # header's MARELO bar starts climbing first and the frozen view then
    # withholds the rank-up the card's own banner needed to climb at all.
    assert re.search(r"const v = t\.view && \{ \.\.\.t\.view, target: frozen\.target \}",
                     practice), (
        "practice.js must hold only the TARGET, letting section data through; "
        "holding the whole view deadlocked the celebration it was protecting")
