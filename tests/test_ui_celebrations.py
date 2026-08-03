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
TUNING_JS = UI / "climbtuning.js"


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
              f"from {CELEBRATIONS_JS.as_uri()!r};\n"
              f"import {{ DEFAULTS }} from {TUNING_JS.as_uri()!r};\n{body}")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# A beat per kind, built through the registry's OWN makeBeat so the wing
# counts come from caps.js's policy rather than from this file's idea of it.
# Diamond II -> Diamond I is a division crossing that gains a wing pair;
# Gold I -> Platinum V is a tier crossing that sheds four; Gold V -> Gold I is
# a whole tier skipped at once, which grows all four (the `pop` style).
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
const resolve = (value) => (typeof value === "function" ? value(beat, DEFAULTS) : value);
const delay = resolve(wingFlap.delay), ms = resolve(wingFlap.ms);
const before = activeEffects(beats, delay - 20);
const during = activeEffects(beats, delay + ms / 2);
const after = activeEffects(beats, delay + ms + 20);
console.log(JSON.stringify([delay, ms,
  'flapPhase' in before.icon, 'flapPhase' in during.icon, 'flapPhase' in after.icon]));
""")
    delay, length, before, during, after = windows
    # NOT `delay == STEP_MS`. That pinned `wingFlapDelayScale` at 1, which is a
    # tunable the user owns — he moved it to 0.5 and the assertion failed for
    # the tool working exactly as intended (2026-07-27). What this test is
    # actually for is that the window BOUNDS the effect; the scale's value is
    # nobody's business here.
    assert delay >= 0 and length > 0, (delay, length)
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


def test_the_next_step_fade_is_driven_by_the_engine_not_by_a_beat():
    """`--climb-reveal` fades the "X.XXs to rank up" line out as the bar fills
    at the start of a climb and back in as it fills at the end.

    It must be computed where the BAR is computed (ui/rankclimb.js), off the
    same step and the same curve — though NOT the same duration, since a
    sweep's length scales with distance travelled and legibility does not
    (`revealMinMs`; tests/test_ui_rank_line.py measures the result). It used to
    be a registry entry fired
    on the settle beat, and a beat fires at a MOMENT: its timing could only ever
    be tuned to line up with the bar, never tied to it, and it did not line up
    — "it looks like the text that appears while we're doing this is totally
    glitchy… it should finish right when the bar finishes loading. These should
    be in sync." (user, 2026-07-27).

    So this asserts the OWNERSHIP, which is the part that can silently regress:
    the day someone adds a `nextReveal` entry back to the registry, the fade
    stops being a function of the bar and starts being a duration again.
    """
    from source_scan import strip_comments
    registry = strip_comments(CELEBRATIONS_JS.read_text(encoding="utf-8"))
    engine = strip_comments(RANKCLIMB_JS.read_text(encoding="utf-8"))
    banner = strip_comments((UI / "components" / "ranks.js").read_text(encoding="utf-8"))
    assert "--climb-reveal" not in registry, (
        "ui/celebrations.js writes --climb-reveal again -- a beat cannot be in "
        "sync with the bar, which is the whole reason this moved to the engine")
    # The chain, end to end: the engine computes `reveal` beside the bar, and
    # the banner binds THAT to the variable. Checking only that the variable is
    # written somewhere would pass on a hardcoded 1.
    assert "reveal" in engine, "the engine no longer computes a reveal"
    assert '"--climb-reveal": climb.reveal' in banner, (
        "the banner must take the fade straight from the climb, or it is back "
        "to being a number someone matched to the bar by hand")

    # And it fades rather than wiping: a moving mask gradient over text that is
    # also changing its contents is what read as tearing.
    stylesheet = strip_comments(INDEX_HTML.read_text(encoding="utf-8"))
    rule = re.search(r"\.rank-banner-next\s*\{([^}]*)\}", stylesheet)
    assert rule, ".rank-banner-next lost its rule"
    assert "opacity: var(--climb-reveal" in rule.group(1), rule.group(1)
    assert "mask" not in rule.group(1), (
        "the reveal is a fade now, not a wipe -- see the CSS comment")


def test_a_banners_flap_never_gates_the_next_banners_climb():
    """The two ranks on a card climb in turn, and the hand-off is the first
    one's PLAN ending — never its celebration tail.

    "if the wings are flapping for a while for the strategy rank, it shouldn't
    block the star rank from starting its level up animation" (user,
    2026-07-27). Measured before pinning: doubling `wingFlapMs` from 1500 to
    3000 moved the star's first rank tick by 25ms (frame noise) while the
    strategy went on animating 1529ms longer, so the two overlap for ~2.1s.

    `tailMs` and `totalMs` sit four lines apart and mean almost the same thing
    in English, which is exactly how a future edit swaps them: the gap would
    grow by however long the flap is, and nothing else would look wrong.
    """
    from source_scan import strip_comments
    engine = strip_comments(RANKCLIMB_JS.read_text(encoding="utf-8"))
    hand_off = re.search(r"const laneEndsAt = ([^;]+);", engine)
    assert hand_off, "the lane hand-off moved -- this guard cannot see it"
    assert "totalMs" in hand_off.group(1), hand_off.group(1)
    assert "tail" not in hand_off.group(1).lower(), (
        "the lane hand-off waits for the celebration TAIL, so a longer wing "
        f"flap now delays the next banner: {hand_off.group(1)}")
    # `tailMs` still has a job -- keeping the loop alive so the flap finishes.
    assert "tailMs" in engine, (
        "nothing keeps ticking for the tail; the last flap would freeze mid-beat")


# Effects allowed to be travelling at full speed when their window closes, and
# why. A wind-up SHOULD end at maximum violence -- that is the anticipation
# principle -- and `tierBurst` picks the motion up on the very next frame, so
# nothing on screen actually stops.
ENDS_AT_SPEED_ON_PURPOSE = {"tierAnticipate"}


def test_no_effect_stops_dead_unless_something_takes_the_motion_over():
    """"we NEVER want to abruptly stop" (user, 2026-07-27), reported against
    the wing flap.

    The trap is that VALUE and SPEED are different questions, and only the
    first one is obvious. `sin(2*pi*p)` returns to exactly zero, so the flap
    ended on precisely the right rotation -- having never slowed down. Sampled
    as values it looked perfect; sampled as per-frame DELTAS the closing six
    frames were 0.88, 0.89, 0.92, 0.92, 0.94, 0.82 degrees, a flat line into a
    wall.

    So this measures the slope over the last 1% of each effect's own run,
    which is the thing that was wrong and the thing no value-based check sees.
    """
    speeds = run_node("""
const beat = { kind: "division", at: 0, stepMs: 100, anticipateMs: 100,
  payoffMs: 100, wingsAfter: 4, wingsBefore: 2, tier: "Gold", division: "I",
  fromTier: "Silver", fromDivision: "V" };
const numbers = (out) => Object.entries(out || {}).flatMap(([key, value]) =>
  typeof value === "number" ? [[key, value]]
  : (value && typeof value.progress === "number") ? [[key + ".progress", value.progress]] : []);
const worst = {};
for (const [name, entry] of Object.entries(CELEBRATIONS)) {
  for (const field of ["icon", "vars"]) {
    if (typeof entry[field] !== "function") continue;
    const before = numbers(entry[field](beat, 0.99, DEFAULTS));
    const after = numbers(entry[field](beat, 1, DEFAULTS));
    before.forEach(([key, value], index) => {
      const delta = Math.abs(after[index][1] - value);
      if (!(worst[name] >= delta)) worst[name] = delta;
    });
  }
}
console.log(JSON.stringify(worst));
""")
    assert speeds, "no effect produced a numeric output at all"
    stopping_dead = {name: round(speed, 4) for name, speed in speeds.items()
                     if speed > 0.02 and name not in ENDS_AT_SPEED_ON_PURPOSE}
    assert not stopping_dead, (
        f"{stopping_dead} are still moving when their window closes, so they "
        "stop dead rather than coming to rest. Give the amplitude an envelope "
        "(ui/celebrations.js::envelope) or add the effect to "
        "ENDS_AT_SPEED_ON_PURPOSE with the thing that takes its motion over.")
    # And the allowlist must not rot into a way of ignoring the check: every
    # name in it has to still exist and still actually end at speed.
    for name in ENDS_AT_SPEED_ON_PURPOSE:
        assert speeds.get(name, 0) > 0.02, (
            f"{name} no longer ends at speed -- take it out of the allowlist "
            "rather than leaving a permanent exemption behind")
