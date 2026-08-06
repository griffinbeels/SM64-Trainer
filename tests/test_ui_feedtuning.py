# tests/test_ui_feedtuning.py
"""The feed/disclosure rig cannot lie about what it is tuning.

Two guards, and they are the pair `.claude/skills/tuning-demo` names as
non-optional, because each failure is silent in the opposite direction:

  * a tunable NO consumer reads is a slider that changes nothing, and the
    inspector draws it exactly like one that works;
  * a consumer reading a key the registry lacks gets `undefined`, which becomes
    a NaN duration -- an animation that never ends, which nothing would notice
    without a hold ceiling.

Plus the golden pair: `withFeedDefaults(null)` must produce byte-identical
plans to passing the shipped defaults explicitly, AND a changed value must
actually move the output -- so neither can pass by accident.

NO TEST HERE MAY ASSERT A SHIPPED VALUE. Once SAVE writes the registry those
numbers belong to whoever last used the inspector; eight tests went red the
first time he tuned the climb. The law is pinned against `REFERENCE` below,
defined in this file, and the live values are only checked for coherence and
range.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from source_scan import strip_comments

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "src" / "sm64_events" / "ui"
REGISTRY = UI / "feedtuning.js"
PLANS = UI / "disclosure.js"
CONSUMERS = [PLANS, UI / "components" / "collapsible.js",
             UI / "components" / "feedmotion.js"]

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")

# An explicit tuning to pin the LAW against, so a tuning session cannot turn
# this file red. Values chosen to be obviously not the shipped ones.
REFERENCE = {
    "enterMs": 400, "enterRisePx": 30, "enterFadeMs": 250,
    "shiftMs": 500, "shiftStaggerMs": 20,
    "openMs": 300, "closeMs": 100, "contentFadeMs": 90, "contentFadeDelayMs": 30,
    "c1x": 0.3, "c1y": 0.0, "c2x": 0.4, "c2y": 1.0,
}


def node(expression: str):
    script = (
        f'import {{ FEED_TUNABLES, FEED_DEFAULTS, withFeedDefaults }} '
        f'from {REGISTRY.as_uri()!r};\n'
        f'import {{ disclosurePlan, feedPlan, feedSettleMs, curve }} '
        f'from {PLANS.as_uri()!r};\n'
        f'console.log(JSON.stringify({expression}));')
    result = subprocess.run(["node", "--input-type=module", "-"], input=script,
                            capture_output=True, text=True, encoding="utf-8",
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


REF = json.dumps(REFERENCE)


# ---- the two guards -------------------------------------------------------

def _registry_keys() -> set[str]:
    source = strip_comments(REGISTRY.read_text(encoding="utf-8"))
    body = source[source.index("FEED_TUNABLES"):source.index("FEED_DEFAULTS")]
    return set(re.findall(r"^  (\w+): \{", body, re.M))


def _keys_consumers_read() -> set[str]:
    """Every `tuning.<key>` / destructured key any consumer reads."""
    seen: set[str] = set()
    for path in CONSUMERS:
        source = strip_comments(path.read_text(encoding="utf-8"))
        seen |= set(re.findall(r"\btuning\.(\w+)", source))
        for group in re.findall(r"const \{ ([^}]+) \} = tuning", source):
            seen |= {part.strip() for part in group.split(",") if part.strip()}
    return seen


def test_every_tunable_is_actually_read_by_a_consumer():
    unread = _registry_keys() - _keys_consumers_read()
    assert not unread, (
        f"these sliders change nothing, and the inspector draws them exactly "
        f"like ones that work: {sorted(unread)}")


def test_no_consumer_reads_a_key_the_registry_lacks():
    invented = _keys_consumers_read() - _registry_keys()
    assert not invented, (
        f"an undefined tunable becomes a NaN duration -- an animation that "
        f"never ends: {sorted(invented)}")


def test_the_guards_can_still_fail():
    """Probe both directions, per this repo's rule that a source scan which
    matches nothing is green forever."""
    assert _registry_keys(), "the registry scan found no rows at all"
    assert _keys_consumers_read(), "the consumer scan found no reads at all"


# ---- the golden pair ------------------------------------------------------

def test_no_config_is_byte_identical_to_the_shipped_defaults():
    without = node("[disclosurePlan(true, 120, withFeedDefaults(null)),"
                   " feedPlan([{key:'a',dy:40}], withFeedDefaults(null))]")
    explicit = node("[disclosurePlan(true, 120, FEED_DEFAULTS),"
                    " feedPlan([{key:'a',dy:40}], FEED_DEFAULTS)]")
    assert without == explicit


def test_a_changed_value_actually_moves_the_output():
    """The inverse, without which the golden test above passes even if the
    plans ignore their tuning entirely."""
    shipped = node("disclosurePlan(true, 120, FEED_DEFAULTS).durationMs")
    other = node(f"disclosurePlan(true, 120, withFeedDefaults("
                 f"{{...{REF}, openMs: 777}})).durationMs")
    assert other == 777 and other != shipped


# ---- the laws, pinned against REFERENCE never against what ships ----------

def test_closing_never_fades_the_contents():
    plan = node(f"disclosurePlan(false, 200, withFeedDefaults({REF}))")
    assert plan["contentFadeMs"] == 0 and plan["contentDelayMs"] == 0, (
        "contents dissolving while the box collapses reads as two things "
        f"leaving: {plan}")


def test_the_content_fade_never_outlives_the_box_it_happens_inside():
    """A fade still running after the edge has stopped is the 'still moving
    when it lands' shape he reports at a single frame's worth of change."""
    tuning = dict(REFERENCE, openMs=100, contentFadeMs=900, contentFadeDelayMs=900)
    plan = node(f"disclosurePlan(true, 200, withFeedDefaults({json.dumps(tuning)}))")
    assert plan["contentFadeMs"] + plan["contentDelayMs"] <= plan["durationMs"], plan


def test_the_stagger_is_applied_in_the_order_given():
    plan = node(f"feedPlan([{{key:'a',dy:10}},{{key:'b',dy:20}},{{key:'c',dy:30}}],"
                f" withFeedDefaults({REF}))")
    delays = [shift["delayMs"] for shift in plan["shifts"]]
    assert delays == [0, REFERENCE["shiftStaggerMs"],
                      2 * REFERENCE["shiftStaggerMs"]], delays


def test_the_settle_time_covers_the_slowest_thing_in_the_gesture():
    """The hold ceiling the wiring layer needs: a window something can hold
    open indefinitely is a surface that never comes back."""
    shifted = "[{key:'a',dy:10},{key:'b',dy:20},{key:'c',dy:30}]"
    settle = node(f"feedSettleMs({shifted}, withFeedDefaults({REF}))")
    plan = node(f"feedPlan({shifted}, withFeedDefaults({REF}))")
    slowest = max([plan["enter"]["durationMs"]]
                  + [s["delayMs"] + s["durationMs"] for s in plan["shifts"]])
    assert settle == slowest


def test_one_curve_drives_every_displacement():
    """A card arriving and a card being pushed are the same physical event seen
    from two sides; two curves is how they end up disagreeing about it."""
    plan = node(f"feedPlan([{{key:'a',dy:10}}], withFeedDefaults({REF}))")
    disclosure = node(f"disclosurePlan(true, 120, withFeedDefaults({REF}))")
    easings = {plan["enter"]["easing"], plan["shifts"][0]["easing"],
               disclosure["easing"]}
    assert len(easings) == 1, easings


# ---- the live values: coherence and range only ---------------------------

def test_every_shipped_value_is_inside_its_own_declared_range():
    rows = node("Object.entries(FEED_TUNABLES).map(([k, r]) => "
                "[k, r.value, r.min, r.max])")
    for key, value, low, high in rows:
        assert low <= value <= high, f"{key}={value} is outside [{low}, {high}]"


def test_every_row_carries_the_fields_the_inspector_draws_from():
    rows = node("Object.entries(FEED_TUNABLES).map(([k, r]) => "
                "[k, Object.keys(r)])")
    needed = {"group", "label", "value", "min", "max", "step", "unit", "why"}
    for key, fields in rows:
        missing = needed - set(fields)
        assert not missing, f"{key} is missing {sorted(missing)}"
