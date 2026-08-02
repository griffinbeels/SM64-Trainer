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
    script = (f"import {{ loneRouteOption, handIsEmpty }} from {RULE_URI!r};\n"
              f"console.log(JSON.stringify({expression}));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


ROUTE = 'new Set(["8:1"])'


def test_one_option_under_a_route_is_the_pick():
    assert run_node(f'loneRouteOption({ROUTE}, [{{"i": 1}}])') == {"i": 1}


def test_two_options_under_a_route_infer_NOTHING():
    """The explicit bound he stated. Picking the first would be inventing an
    intent, which is the whole class of bug this surface keeps producing."""
    assert run_node(f'loneRouteOption({ROUTE}, [{{"i": 1}}, {{"i": 4}}])') is None


def test_no_route_never_auto_picks_however_few_options_there_are():
    """A course with one star and no route active is still not an inference:
    "given the route" is the premise, not the count. The selector rows pass
    their own route filter here, which is null both when no route is active AND
    when the active route never visits this place — the fallback that stops
    the row rendering empty, and which must not become a pick."""
    assert run_node('loneRouteOption(null, [{"i": 1}])') is None
    assert run_node('loneRouteOption(undefined, [{"i": 1}])') is None


def test_no_options_at_all_is_not_a_pick():
    assert run_node(f"loneRouteOption({ROUTE}, [])") is None


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
    assert source.count("loneRouteOption(") >= 2
    assert 'from "../loneoption.js"' in source, (
        "the rule must be imported, never restated in the banner")


def test_the_auto_pick_is_quiet_at_both_call_sites():
    """A write no gesture asked for must not raise the server's refusal notice
    at nobody, and must not cut short a rank-up celebration (target.js says
    why). Both are one flag, so both call sites have to pass it."""
    source = _banner()
    assert source.count("{ quiet: true }") == 2
    assert "quiet = false" in strip_comments(TARGET_JS.read_text(encoding="utf-8"))


def test_the_segment_row_reads_the_ROUTE_filtered_list_not_the_fallback():
    """`segs` falls back to the unfiltered list so the row is never empty; a
    lone option in THAT list is one the route said nothing about. Reading it
    would auto-pick in every castle subarea with a single segment, route or
    no route — the exact inference he ruled out."""
    source = _banner()
    match = re.search(r"const lone = loneRouteOption\(routeSegs, (\w+)\)", source)
    assert match, "the segment row's lone-option call is gone or reshaped"
    assert match.group(1) == "inRoute"


@pytest.mark.parametrize("sample,expected", [
    ("useLoneRouteOption(v, lone, key, commit)", 1),
    ("// useLoneRouteOption(v, lone, key, commit)", 0),
])
def test_the_guard_can_still_fail(sample, expected):
    """Probed both ways: a raw substring scan cannot tell code from prose, and
    five guards in this repo were once green because a COMMENT named the thing
    they were checking for (.claude/rules/ui-core.md)."""
    assert hook_call_sites(strip_comments(sample)) == expected
