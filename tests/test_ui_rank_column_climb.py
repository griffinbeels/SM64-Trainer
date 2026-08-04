"""RankBanner's `layout="column"` must never fork the climb -- proof, live.

The Column rank display (Option 3, spec 2026-08-04-rank-variants: Griffin's
own sketch, cap / progress bar / rank name / a small "type" line under it, two
side by side) is a LAYOUT VARIANT, not a second implementation -- same
`useRankClimb` call, same climb plan, same animated values -- exactly the same
guarantee `layout="stacked"` already carries
(tests/test_ui_rank_stacked_climb.py, `.claude/rules/ui-climb.md`,
`.claude/rules/ui-ranks.md`). This is that same guard, replayed against the
third `layout` value: reused verbatim, not restated, is the whole point --
`test_ui_rank_stacked_layout_source.py` already proves ranks.js has no
per-layout render fork AT ALL (one `useRankClimb` call, two `return html`
statements total, for however many `layout` values exist), so this file's job
is only the LIVE half: does the climb actually reach the DOM in this
arrangement.

Reuses test_ui_rank_line.py's own `BANNERS` selectors and `check()` contract
unchanged -- ranks.js renders the identical tree regardless of `layout`, so if
a later change forked the render into a second DOM tree, these same selectors
would start missing one of the layouts, exactly as test_ui_rank_stacked_climb's
own docstring already argues for "stacked".
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

SET_COLUMN = (
    "(() => {"
    "  const sel = document.getElementById('layout-mode');"
    "  sel.value = 'column';"
    "  sel.dispatchEvent(new Event('change', {bubbles: true}));"
    "  return sel.value;"
    "})()"
)


def goto_column(page):
    """A fresh, unplayed climb with the Layout control set to Column --
    `layout` is local component state, so a reload (the same reset every
    test in test_ui_rank_line.py already performs between plays) drops it
    back to "row" and it has to be re-applied every time."""
    page.goto(page.evaluate("location.href"))
    page.wait_for(".rank-banner", timeout_ms=20_000)
    assert page.evaluate(SET_COLUMN) == "column"


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
    goto_column(demo)
    classes = demo.evaluate(
        "[...document.querySelectorAll('.rank-slot .rank-banner')]"
        ".map((el) => el.className)")
    assert classes, "no rank banners rendered"
    assert all("rank-banner-column" in c for c in classes), classes


def test_the_next_step_line_still_fades_on_a_rank_up(demo):
    """test_ui_rank_line.py::test_the_line_fades_on_a_rank_up, replayed
    against the column layout with the identical selectors and contract --
    even though Column's own CSS hides `.rank-banner-next` from view, the
    element and its `--climb-reveal` fade must still be computed exactly as
    every other layout computes them (ranks.js's own comment: "there is
    simply nothing to violate [the fade rule] when it is not rendered", but
    here it IS rendered, just visually dropped by index.html -- this proves
    the value feeding it never forked)."""
    goto_column(demo)
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
    whether this arrangement actually wires them to the DOM."""
    goto_column(demo)
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
            "-- the digit reel / cap swap did not reach the column layout")
