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

RANKPAGE_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
               / "ui" / "components" / "rankpage.js")


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
