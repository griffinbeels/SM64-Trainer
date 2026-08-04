"""RankBanner's `layout="stacked"` must never fork the climb -- proof, live.

The compact (MARELO-shaped) rank display is a LAYOUT VARIANT, not a second
implementation (`.claude/rules/ui-climb.md`, `.claude/rules/ui-ranks.md`,
spec 2026-08-03-practice-log-entity-cards): same `useRankClimb` call, same
climb plan, same animated values -- only the CSS ARRANGEMENT of the same DOM
parts differs, selected by a class. Griffin's own requirement was explicit:
"I want the user to be able to use this new design AND STILL be able to have
all the animations that they'd normally get" -- so this is the deliverable,
not the code.

This drives the SAME climb `/ui/tune.html` already plays for the row layout
(tests/test_ui_rank_line.py), with its new Layout control switched to
"Stacked", and reuses that file's own `BANNERS` selectors and `check()`
contract UNCHANGED. That reuse is itself part of the proof: ranks.js renders
the identical tree either way and only a root class name differs, so if a
later change forked the render into two DOM trees, these same selectors would
start missing one of them -- there is no second, stacked-shaped selector set
to keep in sync.

The structural half of the guard -- that ranks.js has no such fork to begin
with -- is `tests/test_ui_rank_stacked_layout_source.py`, mutation-proved
against a tmp_path copy the same way tests/test_ui_logtuning.py's scans are.
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

from uilab import trace  # noqa: E402
from uilab.driver import get_driver  # noqa: E402

# Reused verbatim from the row-layout's own suite: same contract, same
# selectors, same PLAY script -- see that file's module docstring for what
# each of CUT/INVISIBLE/READABLE_MS bounds and why.
from test_ui_rank_line import BANNERS, PLAY, check, line_frames  # noqa: E402

BAR_FILL = {
    "strategy-bar": ".rank-slot .rank-banner:nth-child(1) .rank-progress-track i",
    "star-bar": ".rank-slot .rank-banner:nth-child(2) .rank-progress-track i",
}
NAME = {
    "strategy-name": ".rank-slot .rank-banner:nth-child(1) .rank-banner-name",
    "star-name": ".rank-slot .rank-banner:nth-child(2) .rank-banner-name",
}

SET_STACKED = (
    "(() => {"
    "  const sel = document.getElementById('layout-mode');"
    "  sel.value = 'stacked';"
    "  sel.dispatchEvent(new Event('change', {bubbles: true}));"
    "  return sel.value;"
    "})()"
)


def goto_stacked(page):
    """A fresh, unplayed climb with the Layout control set to Stacked --
    `layout` is local component state, so a reload (the same reset every
    test in test_ui_rank_line.py already performs between plays) drops it
    back to "row" and it has to be re-applied every time."""
    page.goto(page.evaluate("location.href"))
    page.wait_for(".rank-banner", timeout_ms=20_000)
    assert page.evaluate(SET_STACKED) == "stacked"


@pytest.fixture(scope="module")
def demo():
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/tune.html")
            page.wait_for(".rank-banner", timeout_ms=20_000)
            yield page


def test_the_control_actually_reaches_rankbanners_layout_prop(demo):
    """Sanity gate for every test below: if the control silently stopped
    reaching RankBanner's `layout` prop, they would all still pass -- against
    the untouched "row" shape -- and report nothing."""
    goto_stacked(demo)
    classes = demo.evaluate(
        "[...document.querySelectorAll('.rank-slot .rank-banner')]"
        ".map((el) => el.className)")
    assert classes, "no rank banners rendered"
    assert all("rank-banner-stacked" in c for c in classes), classes


def test_the_next_step_line_still_fades_on_a_rank_up(demo):
    """test_ui_rank_line.py::test_the_line_fades_on_a_rank_up, replayed
    against the stacked layout with the identical selectors and contract."""
    goto_stacked(demo)
    result = trace.record(demo, watch=BANNERS, properties=["--climb-reveal"],
                          trigger=lambda pg: pg.evaluate(PLAY), ms=9000)
    for name in BANNERS:
        check(name, line_frames(result.of(name)))


def test_the_bar_advances_and_the_rank_name_turns(demo):
    """The other two animated parts the spec names -- "the bar advances
    monotonically, the digit reel turns ... at a tier crossing" -- read off
    the RENDERED CSS (the fill's own `getBoundingClientRect().width`, the
    name's own `textContent`), never off the hook's internal state, which is
    untouched by `layout` by construction and so would prove nothing about
    whether this arrangement actually wires them to the DOM. The inspector's
    default climb (Capless V -> Waluigi IV) crosses several tiers, which is
    what gives the name multiple values to turn through."""
    goto_stacked(demo)
    result = trace.record(demo, watch={**BAR_FILL, **NAME},
                          trigger=lambda pg: pg.evaluate(PLAY), ms=9000)
    for key in BAR_FILL:
        widths = result.of(key).values("width")
        assert widths, f"{key}: the bar never rendered a width at all"
        assert max(widths) - min(widths) > 5, (
            f"{key}: the bar barely moved ({min(widths):.1f}..{max(widths):.1f}px)"
            " -- the climb did not visibly play in this layout")
    for key in NAME:
        distinct = {n for n in result.of(key).values("text") if n}
        assert len(distinct) >= 2, (
            f"{key}: the rank name never changed across the climb ({distinct}) "
            "-- the digit reel / cap swap did not reach the stacked layout")
