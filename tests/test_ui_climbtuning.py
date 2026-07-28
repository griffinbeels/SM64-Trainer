"""ui/climbtuning.js — the registry the tuning inspector is generated from.

Two failure modes matter here and neither is caught by anything else:

  * A DEAD CONTROL. A row nobody reads renders a slider that does nothing —
    the same shape as the invisible feature that made "verify UI by rendering"
    a rule in this repo. Checked in both directions below.
  * A DEFAULT THAT DRIFTED. Every value here replaced a constant, so with the
    whole registry at its defaults the climb must be indistinguishable from
    the one that had no tuning system at all.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from source_scan import code_only

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "src" / "sm64_events" / "ui"
TUNING_JS = UI / "climbtuning.js"
CURVE_JS = UI / "climbcurve.js"
PLAN_JS = UI / "climbplan.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")

# Every .js that may legitimately READ a tunable. tune.js is the inspector and
# names them all reflectively, so it proves nothing about a value being used.
READERS = (CURVE_JS, UI / "celebrations.js", UI / "rankclimb.js")


def run_node(imports: str, body: str, module: Path = TUNING_JS):
    script = f"import {{ {imports} }} from {module.as_uri()!r};\n{body}"
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def registry() -> dict:
    return run_node("TUNABLES, CHOICES, DEFAULTS",
                    "console.log(JSON.stringify({ tunables: TUNABLES,"
                    " choices: CHOICES, defaults: DEFAULTS }));")


def reader_source() -> str:
    """Comment-free: a mention of /ui/tune.html in prose reads as `tune.html`
    to any regex hunting `tune.<key>`, which is the exact false positive this
    repo has been bitten by five times (see tests/source_scan.py)."""
    return "\n".join(code_only(path) for path in READERS)


def test_every_tunable_is_actually_read_by_the_climb():
    """A row nobody reads is a slider that does nothing. The inspector cannot
    tell the difference — it renders a control for every row regardless — so
    this is the only thing standing between a tunable and a dead control."""
    data = registry()
    source = reader_source()
    unread = [key for key in data["defaults"]
              if not re.search(rf"\btun\w*\.{re.escape(key)}\b", source)]
    assert not unread, (
        f"ui/climbtuning.js declares {unread}, which nothing in "
        f"{[p.name for p in READERS]} reads. The inspector would render a "
        "control that changes nothing.")


def test_no_module_reads_a_tunable_that_does_not_exist():
    """The mirror failure, and the worse one: `tune.wingGroMs` is `undefined`,
    every arithmetic on it is NaN, and a NaN duration is an animation that
    never ends — only the 20s hold ceiling would notice."""
    known = set(registry()["defaults"])
    used = set()
    for path in READERS:
        used.update(re.findall(r"\btune\.(\w+)", code_only(path)))
    unknown = sorted(used - known)
    assert not unknown, (
        f"{unknown} are read as tuning values but declared nowhere in "
        "climbtuning.js -- each one is a silent NaN")


def test_every_row_is_complete_enough_to_render_a_control():
    data = registry()
    for name, row in data["tunables"].items():
        assert row.get("group") and row.get("label") and row.get("why"), name
        for field in ("value", "min", "max", "step"):
            assert isinstance(row.get(field), (int, float)), f"{name}.{field}"
        assert row["min"] <= row["value"] <= row["max"], (
            f"{name} ships at {row['value']}, outside its own {row['min']}"
            f"..{row['max']} range -- the slider could not even show it")
    for name, row in data["choices"].items():
        assert row.get("group") and row.get("label") and row.get("why"), name
        assert row["value"] in row["options"], name


def test_a_tunable_never_collides_with_a_choice():
    data = registry()
    both = set(data["tunables"]) & set(data["choices"])
    assert not both, f"{both} is declared twice; one silently wins in DEFAULTS"


def test_the_settings_string_round_trips_every_value():
    """"a settings string that represents EXACTLY the settings that I used"
    (user, 2026-07-27) — so it carries every key, not just the changed ones. A
    differences-only string would silently re-point at new defaults the day one
    of them is codified, which is the one moment it exists to survive."""
    same, keys, defaults_count = run_node(
        "encodeTuning, decodeTuning, DEFAULTS",
        "const mine = { ...DEFAULTS, ladderStepMs: 123, skipStyle: 'chain' };\n"
        "const round = decodeTuning(encodeTuning(mine));\n"
        "console.log(JSON.stringify([\n"
        "  JSON.stringify(round) === JSON.stringify(mine),\n"
        "  Object.keys(JSON.parse(encodeTuning(mine))).length,\n"
        "  Object.keys(DEFAULTS).length]));")
    assert same, "a settings string did not survive its own round trip"
    assert keys == defaults_count, "the string must carry EVERY value"


def test_decoding_drops_unknown_keys_and_fills_missing_ones():
    result = run_node(
        "decodeTuning, DEFAULTS",
        "const got = decodeTuning(JSON.stringify("
        "  { ladderStepMs: 200, aTunableWeDeleted: 9 }));\n"
        "console.log(JSON.stringify([got.ladderStepMs,\n"
        "  'aTunableWeDeleted' in got, got.tierDwellMs === DEFAULTS.tierDwellMs]));")
    assert result == [200, False, True]


def test_changed_from_default_is_only_what_moved():
    changed = run_node(
        "changedFromDefault, DEFAULTS",
        "console.log(JSON.stringify(changedFromDefault("
        "  { ...DEFAULTS, settleMs: 999 })));")
    assert changed == {"settleMs": 999}


def test_the_defaults_reproduce_the_climb_that_shipped():
    """The golden property of the whole exercise: every value here replaced a
    constant, so the plan built with DEFAULTS in force must be identical to the
    one built by callers that pass no tuning at all. If this drifts, a tunable
    silently changed the shipped feel while every other test stayed green."""
    explicit, implicit = run_node(
        "climbTimings",
        f"import {{ buildClimbPlan }} from {PLAN_JS.as_uri()!r};\n"
        f"import {{ DEFAULTS }} from {TUNING_JS.as_uri()!r};\n"
        "const build = (timings) => buildClimbPlan({\n"
        "  fromLevel: 0, fromFill: 0.3, toLevel: 16, toFill: 0.04,\n"
        "  divisionsPerTier: 5, skipStyle: 'pop', timings });\n"
        "const strip = (plan) => plan.steps.map((s) =>\n"
        "  [s.kind, s.level, s.ms, s.at, s.barFrom, s.barTo]);\n"
        "console.log(JSON.stringify([\n"
        "  strip(build((counts) => climbTimings(counts, DEFAULTS))),\n"
        "  strip(build(climbTimings))]));",
        module=CURVE_JS)
    assert explicit == implicit
    assert len(explicit) == 15, "the worked example is 15 steps; the plan moved"


def test_a_tuning_change_actually_reaches_the_plan():
    """The inverse of the golden test, and it is what proves the golden test
    has teeth: with the SAME plan and a changed value, the timings must move.
    Without this, a `climbTimings` that ignored its tuning argument entirely
    would pass everything above."""
    shipped, tuned = run_node(
        "climbTimings",
        f"import {{ buildClimbPlan }} from {PLAN_JS.as_uri()!r};\n"
        f"import {{ DEFAULTS }} from {TUNING_JS.as_uri()!r};\n"
        "const total = (tuning) => buildClimbPlan({\n"
        "  fromLevel: 0, fromFill: 0.3, toLevel: 16, toFill: 0.04,\n"
        "  divisionsPerTier: 5, skipStyle: 'pop',\n"
        "  timings: (counts) => climbTimings(counts, tuning) }).totalMs;\n"
        "console.log(JSON.stringify([total(DEFAULTS),\n"
        "  total({ ...DEFAULTS, tierDwellMs: 400, ladderStepMs: 100 })]));",
        module=CURVE_JS)
    assert tuned < shipped * 0.6, (shipped, tuned)
