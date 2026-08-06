"""Task 0025 — "if the route leaves exactly ONE thing practicable here,
practice it".

His words, 2026-07-27: *"When I am in a course where, given the route, there's
literally only one star / segment that's able to be selected, we should always
select that one star... An example is DDD during 16 star. You will ONLY ever do
Board Bowser's Sub. Ever."* And the bound, in the same breath: *"When there are
multiple options in the route, then we cannot infer that the user is trying to
practice a specific star / segment."*

`ui/loneoption.js` is import-free so the RULE is driven directly by node here;
the source scans below are what stop the two selector rows growing their own
copies of it, which is the failure this surface has already had three times
(a star grab, an arena entry and the Bowser reds row each independently
overwrote a pick, 2026-08-01/02).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from source_scan import strip_comments

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node on PATH is the driver")

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
RULE_JS = UI / "loneoption.js"
RULE_URI = RULE_JS.as_uri()   # node ESM needs a file:// URL on Windows
BANNER = UI / "components" / "stagebanner.js"
TARGET_JS = UI / "target.js"


def run_node(expression: str):
    script = (f"import {{ loneOption, handIsEmpty }} from {RULE_URI!r};\n"
              f"console.log(JSON.stringify({expression}));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_one_option_is_the_pick():
    assert run_node('loneOption([{"i": 1}])') == {"i": 1}


def test_two_options_infer_NOTHING():
    """The explicit bound he stated, and the one part of this rule that has
    never moved. Picking the first would be inventing an intent, which is the
    whole class of bug this surface keeps producing."""
    assert run_node('loneOption([{"i": 1}, {"i": 4}])') is None


def test_where_the_narrowing_came_from_is_no_longer_part_of_the_rule():
    """WIDENED 2026-08-05. This module used to require that a ROUTE had done
    the narrowing, so a place genuinely offering one thing was left unpicked.
    Griffin: "either there genuinely being only one option (like in Bowser 3)
    OR because the route itself reduces the star/segment space down to only
    one option (like in DDD when the 16 star route is selected)."

    His bound survives the reversal untouched, because it was never about
    routes -- two options is what cannot be inferred from, and the count is
    still the whole test. A no-route course still holds its seven stars, and
    `test_two_options_infer_NOTHING` above is what keeps that safe."""
    assert run_node('loneOption([{"i": 1}])') == {"i": 1}
    assert run_node('loneOption([{"i": 1}, {"i": 2}])') is None


def test_no_options_at_all_is_not_a_pick():
    assert run_node("loneOption([])") is None
    assert run_node("loneOption(null)") is None
    assert run_node("loneOption(undefined)") is None


def test_a_hand_holding_anything_is_not_empty():
    """The third condition, and the one with a body count: a convenience
    default may FILL an empty hand; it may not take something out of one."""
    assert run_node("handIsEmpty({})") is True
    assert run_node("handIsEmpty(null)") is True
    # What the projector leaves after retiring a target: a kind, no identity.
    assert run_node('handIsEmpty({"kind": "star"})') is True
    assert run_node('handIsEmpty({"kind": "star", "course_id": 8, "star_id": 1})') is False
    assert run_node('handIsEmpty({"kind": "segment", "segment_id": 40})') is False
    # star_id 0 is a real star (the reds star) and course_id 0 is a real
    # course (the castle secret stars) -- a falsy-check here would read both
    # as an empty hand and overwrite them.
    assert run_node('handIsEmpty({"kind": "star", "course_id": 0, "star_id": 0})') is False


# --- one door, and both rows go through it ---------------------------------

def _banner() -> str:
    return strip_comments(BANNER.read_text(encoding="utf-8"))


def hook_call_sites(source: str) -> int:
    return len(re.findall(r"\buseLoneRouteOption\(", source))


def test_the_star_row_and_the_segment_row_share_ONE_implementation():
    """Rule 11 (star/segment parity) as a structural fact rather than a
    promise: one definition, one import of the rule, and exactly the two
    call sites — the hook's own and the two rows'."""
    source = _banner()
    assert source.count("function useLoneRouteOption(") == 1
    assert hook_call_sites(source) == 3       # the definition + two rows
    assert source.count("loneOption(") >= 2
    assert 'from "../loneoption.js"' in source, (
        "the rule must be imported, never restated in the banner")


def test_the_auto_pick_is_quiet_at_both_call_sites():
    """A write no gesture asked for must not raise the server's refusal notice
    at nobody, and must not cut short a rank-up celebration (target.js says
    why). Both are one flag, so both call sites have to pass it."""
    source = _banner()
    assert source.count("{ quiet: true }") == 2
    assert "quiet = false" in strip_comments(TARGET_JS.read_text(encoding="utf-8"))


def test_the_segment_row_reads_THE_LIST_IT_DRAWS():
    """REVERSED 2026-08-05, and worth stating as a reversal rather than
    quietly rewriting: this guard used to assert the opposite.

    It pinned `inRoute`, the route-filtered list, precisely so that a castle
    subarea holding a single segment would NOT auto-pick -- "a lone option in
    the fallback list is one the route said nothing about". Griffin then asked
    for exactly that case by name: "when there's only one valid option for a
    given course / area due to either there genuinely being only one option
    (like in Bowser 3...) OR because the route itself reduces the
    star/segment space down to only one option."

    So the row reads `segs` -- the list actually drawn -- and WHERE the
    narrowing came from is no longer part of the rule. What still holds the
    line is the count: two options infer nothing, route or no route
    (`test_two_options_infer_NOTHING`).
    """
    source = _banner()
    reads = re.findall(r"const lone = loneOption\((\w+)\)", source)
    assert reads == ["shown", "segs"], (
        "both rows must feed the rule the list they RENDER — the star row's "
        "`shown` and the segment row's `segs` — or a place that genuinely "
        f"offers one thing goes unpicked: reads {reads}")


@pytest.mark.parametrize("sample,expected", [
    ("useLoneRouteOption(v, lone, key, commit)", 1),
    ("// useLoneRouteOption(v, lone, key, commit)", 0),
])
def test_the_guard_can_still_fail(sample, expected):
    """Probed both ways: a raw substring scan cannot tell code from prose, and
    five guards in this repo were once green because a COMMENT named the thing
    they were checking for (.claude/rules/ui-core.md)."""
    assert hook_call_sites(strip_comments(sample)) == expected
