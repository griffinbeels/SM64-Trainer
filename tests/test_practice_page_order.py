# tests/test_practice_page_order.py
"""The practice log leads the page, and the auto-open slot obeys its own rule.

Two rulings from 2026-08-05, both of which had a correct mechanism sitting
one module away from the place that overrode it:

1. "The practice log should always stay at the top, under the quick select
   picker" -- picking a route pushed it below the whole route listing. The
   hierarchy existed, as a `@container (max-width: 1060px)` `order` block, so
   it held only when the pane was narrow and never covered the route-focus
   card at all.
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
import json
import re
import sys
import tempfile
import urllib.request
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


def test_the_practice_log_is_rendered_before_the_route_focus_card():
    """The report that opened this: "when I select an actual route, it
    incorrectly moves the practice log to be BELOW the route focus info"."""
    body = _render_body()
    assert _position(body, "<${PracticeLog}") < _position(body, "route-focus-card")


def test_the_practice_log_is_rendered_before_the_detail_drawer():
    body = _render_body()
    assert _position(body, "<${PracticeLog}") < _position(body, "<${EntityDrawer}")


def test_no_stylesheet_rule_reorders_the_practice_page_cards():
    """DOM order is the whole statement, so CSS `order` must not have a
    second opinion about it.

    This is the rule that actually regressed: an `order` block scoped to one
    container width put the log first only when narrow, and a card added to
    the page later (`.route-focus-card`) defaulted to `order: 0` and landed
    above every card that had been given a number. A reorder here is legal
    again only if this test is deliberately rewritten.
    """
    css = strip_comments(INDEX_HTML.read_text(encoding="utf-8"))
    cards = ("log-list-card", "analysis-card", "detail-drawer",
             "route-focus-card")
    offenders = [
        block for block in re.findall(r"\.([a-z-]+)\s*\{[^}]*\border\s*:[^}]*\}",
                                       css)
        if block in cards
    ]
    assert offenders == [], f"CSS `order` on practice-page cards: {offenders}"


def test_the_auto_open_slot_asks_whether_the_active_entity_has_played():
    """`live.activeKey ?? frozen.topKey` is the exact regression.

    The nullish fallback reads as "the active card leads, else the newest",
    which is right about ORDER and wrong about the slot: it hands the slot to
    an entity with nothing recorded, which is the one thing the rule forbids.
    Mutation-prove by restoring the `??` form -- this goes red, and the three
    `played_keys` tests in test_ui_practice_log.py stay green, which is
    precisely why the composition needs its own guard.
    """
    source = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    slot = re.search(r"^\s*const topKey = .*$", source, re.M)
    assert slot, "the topKey derivation is gone — renamed?"
    assert "activeHasPlayed" in slot.group(0), (
        f"the slot no longer consults the eligibility rule: {slot.group(0)!r}")
    assert "playedKeys" in source, (
        "practice.js never reads the frozen playedKeys — the slot cannot be "
        "asking whether the active entity has recorded anything")


def _tops(page):
    return page.evaluate("""
      (() => {
        const top = (sel) => {
          const el = document.querySelector(sel);
          return el ? Math.round(el.getBoundingClientRect().top) : null;
        };
        return {log: top('.log-list-card'),
                analysis: top('.analysis-card'),
                route: top('.route-focus-card')};
      })()
    """)


def test_the_rendered_page_puts_the_log_above_everything_it_leads():
    """The source checks above are about the render FUNCTION; this is about
    the screen, which is what he actually reported.

    Both states in one browser, because they are one claim: a `@container`
    `order` rule reintroduced anywhere would satisfy every source check above
    and still move the card. The route half is the reported bug exactly --
    "when I select an actual route, it incorrectly moves the practice log to
    be BELOW the route focus info" -- and it needs a real route selected,
    which is why this cannot be a screenshot of the default fixture.
    """
    sys.path.insert(0, str(REPO / "tools"))
    from find_uilab import find_uilab                    # noqa: E402
    missing = find_uilab()
    if missing:
        pytest.skip(missing)
    from uilab import driver                             # noqa: E402
    from ui_fixture import FIXTURE_COURSE, FIXTURE_STAR, serve_ui  # noqa: E402

    with tempfile.TemporaryDirectory() as scratch:
        with serve_ui(Path(scratch) / "order.db") as base:
            with driver.get_driver().launch(headless=True) as page:
                page.goto(base)
                page.evaluate("new Promise(r => setTimeout(r, 2500))")
                plain = _tops(page)

                route = _post(base, "/api/routes", {
                    "name": "page-order probe",
                    "steps": [{"need": 1, "candidates": [
                        {"type": "star", "course": FIXTURE_COURSE,
                         "star": FIXTURE_STAR}]}]})
                _post(base, "/api/route/select", {"route_id": route["id"]})
                page.goto(base)
                page.evaluate("new Promise(r => setTimeout(r, 2500))")
                routed = _tops(page)

    assert plain["log"] is not None, "the practice log did not render"
    assert plain["analysis"] is not None, (
        "no analysis card rendered — this fixture cannot answer the question, "
        "which is a broken guard rather than a passing one")
    assert plain["log"] < plain["analysis"], (
        f"the analysis card sits above the practice log: {plain}")

    assert routed["route"] is not None, (
        "route focus never rendered, so the reported state was never reached")
    assert routed["log"] < routed["route"], (
        f"picking a route pushed the practice log below it: {routed}")


def _post(base, path, payload):
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())
