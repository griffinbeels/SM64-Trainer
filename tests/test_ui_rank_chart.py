# tests/test_ui_rank_chart.py
"""Two invariants of the Rank tab's history chart (rankpage.js), both of
which cost a round trip in the harness on 2026-07-25 (round 6, wheel zoom +
rank-up markers).

They are source scans rather than node runs because `rankUpKind` and the
wheel handler live in a module that imports preact through the browser
importmap, which node cannot resolve — the same reason ui/entities.js exists
as a separate importless module. Exporting them purely to test them would be
production API added for scaffolding."""
import re
from pathlib import Path

from sm64_events.ranks import scoring
from sm64_events.ranks.standards import RANK_COLORS

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
RANKPAGE_JS = UI / "components" / "rankpage.js"
RANKS_JS = UI / "components" / "ranks.js"


def test_rank_up_markers_require_a_rise_not_just_a_change():
    """MARELO falls too — a worse average, an excluded entity, an edited
    route. A boundary crossed on the way DOWN is not a rank-up, and marking
    it would contradict the server, whose celebration watermark
    (`scopes.celebration_delta`) fires only on a rise."""
    source = RANKPAGE_JS.read_text(encoding="utf-8")
    body = re.search(r"function rankUpKind\(point, previous\) \{(.*?)\n\}",
                     source, re.S)
    assert body, "rankUpKind not found — did it move or get renamed?"
    assert "point.marelo > previous.marelo" in body.group(1), (
        "rankUpKind must require the rating to have RISEN; comparing only "
        "tier/division would mark every fall through a boundary as a rank-up")


def test_wheel_zoom_composes_across_a_burst_of_events():
    """A fast scroll delivers several wheel events inside one frame. Reading
    `zoom` from the current render makes every handler in that burst compute
    the same result — measured in the harness as twelve events moving the
    window exactly as far as one."""
    source = RANKPAGE_JS.read_text(encoding="utf-8")
    handler = re.search(r"function onWheel\(event\) \{(.*?)\n  \}", source, re.S)
    assert handler, "the chart's onWheel handler not found"
    assert re.search(r"setZoom\(\s*\(current\)\s*=>", handler.group(1)), (
        "setZoom must take the functional form so consecutive wheel events "
        "in one frame compose instead of collapsing to a single step")


def test_every_tier_in_view_is_labelled():
    """A guard that hid a gridline's label when two sat within 11px dropped
    "Grandmaster" from a full-ladder axis, and the user reported it as a
    missing tier (2026-07-25 round 7). The crowding it was guarding against
    isn't real: a 9px font's cap height clears a 9px gap."""
    source = RANKPAGE_JS.read_text(encoding="utf-8")
    lines = re.search(r"const tierLines = TIERS_ASCENDING(.*?);\n", source, re.S)
    assert lines, "the tierLines builder not found"
    assert "labelled" not in lines.group(1), (
        "every tier in the domain must get a label — a conditional label is "
        "read as a missing rank")


def test_ui_mirrors_the_score_anchors_and_division_count():
    """The Mastery ladder draws tier bands and division pips from JS copies
    of `scoring.SCORE_ANCHORS` and `DIVISIONS_PER_TIER`. They are mirrors by
    the same convention as ranks.js's colour table, so they need a test that
    fails when the Python side moves — the bands would otherwise silently
    disagree with the tier the server assigns the same score."""
    source = RANKPAGE_JS.read_text(encoding="utf-8")
    anchors = dict(re.findall(r"(\w+): (\d+)\s*[,}]?",
                              re.search(r"const ANCHORS = \{(.*?)\};",
                                        source, re.S).group(1)))
    assert {tier: float(score) for tier, score in anchors.items()
            if tier != "Iron"} == scoring.SCORE_ANCHORS
    assert anchors["Iron"] == "0", "Iron is the implicit floor at 0"
    assert f"DIVISIONS_PER_TIER = {scoring.DIVISIONS_PER_TIER}" in source
    assert f"SCORE_CEILING = {int(scoring.TOP_SCORE)}" in source


def test_iron_is_not_a_shade_of_silver():
    """Iron read as a dim Silver at #8a8a8a and the two were indistinguishable
    side by side on a practice card (live report 2026-07-25). Both registries
    have to agree, and Iron has to stay far enough from Silver to tell apart:
    its own rusty hue, not a grey."""
    source = RANKS_JS.read_text(encoding="utf-8")
    # The colour table only -- ranks.js declares a second, unrelated map
    # right below it (FG, the medal's TEXT colour per rank).
    table = re.search(r"export const RANK_COLORS = \{(.*?)\};", source, re.S)
    assert table, "RANK_COLORS table not found in ranks.js"
    js_colors = dict(re.findall(r"(\w+): \"(#[0-9a-fA-F]{6})\"", table.group(1)))
    assert js_colors == RANK_COLORS, "ui/components/ranks.js mirrors standards.RANK_COLORS"

    def channels(hex_color):
        return [int(hex_color[index:index + 2], 16) for index in (1, 3, 5)]

    red, green, blue = channels(RANK_COLORS["Iron"])
    assert red - blue >= 24 and red - green >= 16, (
        f"Iron {RANK_COLORS['Iron']} must be visibly warm/rusty rather than "
        "a neutral grey, which is what made it read as Silver")
    silver = channels(RANK_COLORS["Silver"])
    assert sum(silver) - (red + green + blue) >= 120, (
        "Iron must be substantially darker than Silver")
