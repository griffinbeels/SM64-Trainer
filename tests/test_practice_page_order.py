# tests/test_practice_page_order.py
"""The practice log leads the page, and the auto-open slot obeys its own rule.

Two rulings from 2026-08-05, both of which had a correct mechanism sitting
one module away from the place that overrode it:

1. "The practice log should always stay at the top, under the quick select
   picker" -- picking a route pushed it below the whole route listing. The
   hierarchy existed, as a `@container (max-width: 1060px)` `order` block, so
   it held only when the pane was narrow and never covered the route-focus
   card at all. (That card was deleted outright on 2026-08-05 -- "this reads
   as noise... it ended up not being a useful feature" -- so the assertions
   about it are gone; the ordering rule it exposed is what these still hold.)
2. "If there are no attempts yet, it's closed by default. If there are
   attempts, then it's autoopened" -- `topEntityKey` already said exactly
   this and practice.js resolved the slot as `live.activeKey ?? frozen.topKey`,
   an unconditional override that never consulted it.

Both are structural claims about ONE render function and ONE stylesheet, so
they are checked against comment-stripped source (`tests/source_scan.py` --
a raw substring cannot tell code from the prose explaining it, and every
sentence above is now in a comment beside the code it describes). The order
half is additionally checked by RENDERING, which is what actually answers
"is the log above the analysis card on screen"; the source half is what keeps
a *route-active* page -- a state the offline fixture does not reach -- from
regressing silently.
"""
import re
import sys
import tempfile
from pathlib import Path

import pytest

from source_scan import strip_comments

from test_ui_practice_log import REPO, UI

PRACTICE_JS = UI / "components" / "practice.js"
INDEX_HTML = UI / "index.html"


def _render_body() -> str:
    """`Practice()`'s returned markup, comments stripped."""
    source = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    start = source.index('return html`<div class="practice-page"')
    return source[start:]


def _position(body: str, needle: str) -> int:
    at = body.find(needle)
    assert at != -1, f"{needle!r} not in the practice page render — renamed?"
    return at


def test_the_practice_log_is_rendered_before_the_analysis_card():
    body = _render_body()
    assert _position(body, "<${PracticeLog}") < _position(body, "<${EntityAnalysis}")


def test_the_practice_log_is_rendered_before_the_detail_drawer():
    body = _render_body()
    assert _position(body, "<${PracticeLog}") < _position(body, "<${EntityDrawer}")


def test_no_stylesheet_rule_reorders_the_practice_page_cards():
    """DOM order is the whole statement, so CSS `order` must not have a
    second opinion about it.

    This is the rule that actually regressed: an `order` block scoped to one
    container width put the log first only when narrow, and a card added to
    the page later (`.route-focus-card`, since deleted) defaulted to
    `order: 0` and landed above every card that had been given a number --
    which is the general hazard this guards, not a fact about that one card.
    A reorder here is legal again only if this test is deliberately rewritten.
    """
    css = strip_comments(INDEX_HTML.read_text(encoding="utf-8"))
    cards = ("log-list-card", "analysis-card", "detail-drawer")
    offenders = [
        block for block in re.findall(r"\.([a-z-]+)\s*\{[^}]*\border\s*:[^}]*\}",
                                       css)
        if block in cards
    ]
    assert offenders == [], f"CSS `order` on practice-page cards: {offenders}"


def test_the_auto_open_slot_is_resolved_against_the_rendered_list():
    """The slot may only be chosen from cards that are actually drawn.

    It was resolved in practice.js against the UNFILTERED view, and the two
    lists genuinely disagree: a Bowser course's reds star and its pipe segment
    tie on `last_activity` (measured on his live session -- both 1414), stars
    sort first, and `applyRedsPipeExclusivity` renders whichever half his
    star/pipe toggle names. So the slot named the star while the log drew the
    segment, and every card sat closed.

    Mutation-prove by resolving it in practice.js again and passing `topKey`
    down: this goes red, and every pure-rule test stays green -- which is the
    whole reason a "who decides" guard has to exist beside them.
    """
    practice = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    assert "playedKeys=${frozen.playedKeys}" in practice, (
        "the page must hand PracticeLog the frozen candidate LIST, not a "
        "pre-resolved key")
    assert "topKey" not in practice.replace("topKey:", ""), (
        "the page must not resolve the auto-open slot -- only PracticeLog "
        "knows which cards survive membership and Reds/Pipe exclusivity")

    log = strip_comments((UI / "components" / "practicelog.js")
                         .read_text(encoding="utf-8"))
    assert re.search(r"const topKey = autoOpenKey\(sections, activeKey, "
                     r"playedKeys\)", log), (
        "PracticeLog must resolve the slot from `sections` -- its own rendered "
        "list -- and nothing else")


def _tops(page):
    return page.evaluate("""
      (() => {
        const top = (sel) => {
          const el = document.querySelector(sel);
          return el ? Math.round(el.getBoundingClientRect().top) : null;
        };
        return {log: top('.log-list-card'),
                analysis: top('.analysis-card')};
      })()
    """)


def test_the_rendered_page_puts_the_log_above_the_analysis_card():
    """The source checks above are about the render FUNCTION; this is about
    the screen. A `@container` `order` rule reintroduced anywhere would
    satisfy every source check above and still move the card.

    NARROWED 2026-08-05: this used to select a real route and assert the log
    stayed above the route-focus card too. That card is deleted, so the state
    is unreachable and the assertion is gone rather than rewritten into
    something weaker.
    """
    sys.path.insert(0, str(REPO / "tools"))
    from find_uilab import find_uilab                    # noqa: E402
    missing = find_uilab()
    if missing:
        pytest.skip(missing)
    from uilab import driver                             # noqa: E402
    from ui_fixture import serve_ui                      # noqa: E402

    with tempfile.TemporaryDirectory() as scratch:
        with serve_ui(Path(scratch) / "order.db") as base:
            with driver.get_driver().launch(headless=True) as page:
                page.goto(base)
                page.evaluate("new Promise(r => setTimeout(r, 2500))")
                tops = _tops(page)

    assert tops["log"] is not None, "the practice log did not render"
    assert tops["analysis"] is not None, (
        "no analysis card rendered — this fixture cannot answer the question, "
        "which is a broken guard rather than a passing one")
    assert tops["log"] < tops["analysis"], (
        f"the analysis card sits above the practice log: {tops}")
