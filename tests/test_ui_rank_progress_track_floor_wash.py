"""The rank-progress track must not bisect the wash at the ladder FLOOR.

Live report 2026-08-04: "rank standard displays show a line in the middle of
the render (you can see that the gradient is bisected in the middle by a
line). I would expect the background gradient to be unbroken. This also
didn't happen in the tuning tool." Root cause: at the ladder floor (no time
recorded on a strategy that has one) the fill `<i>` inside `.rank-progress-
track` draws 0% wide, and in the two GRID layouts (Stacked/Column) the track
itself sits in the visual MIDDLE of `.rank-banner::before`'s colour wash --
between the icon row and the name row -- so its own near-opaque background
was all that remained visible: a flat dark line across the gradient. Row
layout was never touched (the track sits at the banner's own bottom edge
there, "has always looked right"). Fix: index.html's "Layout matrix" section,
`.rank-banner-stacked`/`.rank-banner-column .rank-progress-track` (and their
log-card ancestor-scoped siblings, covered separately by
test_ui_rank_progress_track_log_card.py) now paint `background: transparent`.

This defect is entirely in PAINT -- the track sits fully inside its own box,
so it never overflows, clips, truncates, or bleeds onto a neighbour, which is
why every uilab probe (`.claude/rules/ui-core.md`'s "a defect can be entirely
in paint" section) missed it. The only honest check is the COMPUTED
background colour, at the exact state that used to show the line.

This file drives `RankBanner`'s own `layout` prop directly through
`/ui/tune.html`'s manual preview -- one of the two independent mechanisms
that can render Stacked/Column (the other is the practice log's ancestor-
scoped CSS, covered by test_ui_rank_progress_track_log_card.py; kept in a
SEPARATE file because two `serve_ui()` fixtures in one module collide over
asyncio's "already running" event loop -- measured, not guessed, when this
suite was first written as one file).
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import serve_ui  # noqa: E402
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402


def _set_layout_script(layout: str) -> str:
    return (
        "(() => {"
        "  const sel = document.getElementById('layout-mode');"
        f"  sel.value = {layout!r};"
        "  sel.dispatchEvent(new Event('change', {bubbles: true}));"
        "  return sel.value;"
        "})()"
    )


TRACK_BG_SCRIPT = (
    "getComputedStyle(document.querySelector("
    "'.rank-slot .rank-banner:nth-child(1) .rank-progress-track')).backgroundColor"
)


def _alpha(rgba: str) -> float:
    """The alpha channel of a `getComputedStyle` colour string -- 1.0 for a
    bare `rgb(...)` (no fourth term at all), else the literal fourth term."""
    inside = rgba[rgba.index("(") + 1: rgba.rindex(")")]
    parts = [part.strip() for part in inside.split(",")]
    return float(parts[3]) if len(parts) == 4 else 1.0


@pytest.fixture(scope="module")
def tune_demo():
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/tune.html")
            page.wait_for(".rank-banner", timeout_ms=20_000)
            yield page


def _floor_track_alpha(page, layout: str) -> float:
    # A fresh load: `layout` is local component state, dropped on reload --
    # the same reset test_ui_rank_column_climb.py's own `goto_column` uses.
    # `startLevel` defaults to 0 ("Capless 5") and nothing has been played
    # yet, so this IS the ladder floor -- fill 0%, the exact state reported.
    page.goto(page.evaluate("location.href"))
    page.wait_for(".rank-banner", timeout_ms=20_000)
    reached = page.evaluate(_set_layout_script(layout))
    assert reached == layout, f"the layout control never reached {layout!r}"
    colour = page.evaluate(TRACK_BG_SCRIPT)
    assert colour, "no .rank-progress-track rendered"
    return _alpha(colour)


def test_row_layout_keeps_its_opaque_track_at_the_floor(tune_demo):
    """Row is untouched by this fix -- its track sits at the banner's own
    bottom edge, where a dark strip "has always looked right" (his words) and
    the objective card / header / Rank tab all still use it unmodified."""
    alpha = _floor_track_alpha(tune_demo, "row")
    assert alpha > 0.5, (
        f"row layout's track background lost its opacity ({alpha}) -- this "
        "fix must not touch the layout that already looked right")


def test_stacked_layout_reads_unbroken_at_the_floor(tune_demo):
    """Stacked puts "bar" in the wash's own vertical middle -- the reported
    defect. Its track must paint nothing of its own at the floor."""
    alpha = _floor_track_alpha(tune_demo, "stacked")
    assert alpha == 0, (
        f"stacked layout's track still paints a background at the floor "
        f"(alpha {alpha}) -- the wash behind it would still read as cut")


def test_column_layout_reads_unbroken_at_the_floor(tune_demo):
    """Same defect, Column's own arrangement -- the practice log's SHIPPED
    DEFAULT rank display for every card, at every width (`ui/logtuning.js`'s
    `rankStyle*` rows all default to `"column"`), so this is not a rare
    corner: it is what every new target renders until its first PB lands."""
    alpha = _floor_track_alpha(tune_demo, "column")
    assert alpha == 0, (
        f"column layout's track still paints a background at the floor "
        f"(alpha {alpha}) -- the wash behind it would still read as cut")
