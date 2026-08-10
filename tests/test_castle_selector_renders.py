"""The CASTLE selector renders at all, in every subarea.

THE GAP THIS CLOSES, and it is the reason the file exists rather than one more
assertion somewhere: no gate had ever rendered `stage.mode == "castle"`. The
responsive matrix and every story in it run `"stars"` or `"bowser_course"`, so
`stagebanner.js::SegmentRow` -- the row for the castle lobby, upstairs and
basement -- was executed by nothing.

What that cost (2026-08-09): round 22 dropped a single `const offered = ...`
line while editing that function. `node --check` passes on it, the whole suite
passed on it, and the branch shipped. On his machine the first walk out of a
course into the castle threw `offered is not defined` INSIDE Preact's render,
which stops the entire tree updating while DOM listeners keep firing -- so the
page looked completely normal and nothing on it responded. He reported it
twice, first as "I can no longer close the overall dropdown for each
star/segment" and then as "I still can't interact with dropdowns in any way...
can't interact with ANY of the dropdowns on this page". Seven experiments
against the practice page found nothing, because the practice page was never
the problem.

His own `data/ui_log.jsonl` is what finally placed it: the page posted its last
painted snapshot 26 ms after the `level_changed` into the castle basement, and
then nothing at all while the journal recorded 48 more events including a star
grab and a PB save.

So this asserts the weakest possible thing on purpose -- that the row RENDERS
and the page throws nothing -- because that is exactly what was missing.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from ui_fixture import serve_ui  # noqa: E402

# lobby / upstairs / basement -- addresses.CASTLE_AREA_NAMES' own three.
CASTLE_AREAS = (1, 2, 3)


def _page_errors(page):
    """Uncaught exceptions only. 404s and aborted fetches are fixture noise
    (the harness serves no replay/update endpoints); a `pageerror` is the
    render dying, which is the whole subject here."""
    return [problem for problem in page.problems()
            if problem.startswith("pageerror")
            or "Uncaught" in problem]


@pytest.mark.parametrize("area", CASTLE_AREAS)
def test_the_castle_selector_renders_without_throwing(area):
    with serve_ui(castle_stage=area) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(2000)
        assert not _page_errors(page), (
            f"the castle selector threw in area {area}: {_page_errors(page)}")
        # A render that throws leaves the LAST GOOD tree on screen, so "the
        # page has content" proves nothing on its own -- the selector card
        # itself has to be the castle's.
        assert page.count(".stagebanner") == 1, (
            "no selector card rendered for the castle interior")


def test_the_page_still_responds_after_entering_the_castle():
    """The symptom he reported was not a blank page -- it was a page that
    looks perfectly normal and answers nothing. So the assertion is that the
    tree still UPDATES: click a fold and require the DOM to change.

    Mutation-proved by deleting `const offered` again: the click lands, the
    handler runs, and the DOM is byte-identical afterwards.
    """
    with serve_ui(castle_stage=3) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(2000)
        before = page.evaluate("document.body.innerHTML.length")
        page.evaluate(
            "(document.querySelector('.log-card-fold')"
            " || document.querySelector('.collapse-toggle')).click()")
        page.wait_ms(600)
        after = page.evaluate("document.body.innerHTML.length")
        assert before != after, (
            "the DOM did not move after a click -- the render is frozen, "
            "which is what an exception inside Preact's render looks like")
