"""Task 0025 in the REAL app: the row really does narrow to one cell, and the
auto-pick really does keep its hands off a target he already holds.

`tests/test_ui_lone_route_option.py` drives the rule and scans the wiring.
Neither can answer "does the browser reach the state the rule is about" — the
combination of unit tests and `node --check` has shipped an invisible feature
here before (.claude/rules/ui-core.md), so this renders it.

COVERAGE OWED, stated rather than left as a silent gap: the POSITIVE write —
an EMPTY hand being filled — is not gated. `tools/ui_fixture.py` always seeds
an active target (`_seed_target`, and the page is useless without one), and
`POST /api/target` cannot clear one: every shape of a null identity comes back
409 "star target needs course_id and star_id", measured 2026-08-02. Reaching an
empty hand therefore needs a fixture option, and changing what the fixture
seeds changes what the whole responsive matrix measures — its own change, with
its own `known_defects` reckoning. What IS gated here is the half with a body
count: three separate mechanisms on this surface have overwritten a pick the
player made (a star grab, an arena entry, the Bowser reds row, 2026-08-01/02).
"""
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import FIXTURE_COURSE, FIXTURE_STAR, serve_ui  # noqa: E402

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402

SETTLE = "new Promise(r => setTimeout(r, 2500))"
COUNT_CELLS = ("document.querySelectorAll('.stagebanner .starcell').length")


def _post(base: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as response:
        return json.loads(response.read())


def _lone_star_route(base: str, star_id: int) -> int:
    """A route whose only star in the fixture's course is `star_id` — the
    shape of DDD during 16 Star, which is the case he described."""
    route = _post(base, "/api/routes", {
        "name": "lone-option probe",
        "steps": [{"need": 1, "candidates": [
            {"type": "star", "course": FIXTURE_COURSE, "star": star_id}]}]})
    return route["id"]


def test_a_route_that_leaves_one_star_renders_exactly_one_cell(tmp_path):
    """The precondition the rule is written against has to be REACHABLE. If
    the row never narrows to one in a real render, every unit test above is
    about a state the app cannot be in."""
    with serve_ui(tmp_path / "lone.db") as base:
        before = _get(base, "/api/session")["target"]
        route_id = _lone_star_route(base, FIXTURE_STAR)
        _post(base, "/api/route/select", {"route_id": route_id})
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            page.evaluate(SETTLE)
            cells = page.evaluate(COUNT_CELLS)
    assert before["course_id"] == FIXTURE_COURSE
    assert cells == 1, (
        f"the route names one star of this course; the selector drew {cells} "
        f"cells, so it is not narrowing and the rule never applies")


def test_a_lone_route_option_does_not_steal_a_target_he_already_holds(tmp_path):
    """The empty-hand rule, live. The route names star 1; the fixture has
    already targeted FIXTURE_STAR. A convenience default may fill an empty
    hand; it may not take something out of one — and this surface has broken
    that rule three times in two days."""
    other = 1 if FIXTURE_STAR != 1 else 2
    with serve_ui(tmp_path / "lone-guard.db") as base:
        route_id = _lone_star_route(base, other)
        _post(base, "/api/route/select", {"route_id": route_id})
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            page.evaluate(SETTLE)
            errors = page.evaluate(
                "JSON.stringify(window.__uilabErrors || [])")
        after = _get(base, "/api/session")["target"]
    assert after["star_id"] == FIXTURE_STAR, (
        f"the auto-pick moved the target to {after['star_id']} — it must only "
        f"ever fill an EMPTY hand")
    assert after["course_id"] == FIXTURE_COURSE
    assert json.loads(errors) == []


def test_the_page_still_renders_with_no_route_at_all(tmp_path):
    """The regression the change could most easily cause: a hook added above
    an early return, in a row whose own comment says the early return must
    never change the hook count."""
    with serve_ui(tmp_path / "lone-noroute.db") as base:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            page.evaluate(SETTLE)
            cells = page.evaluate(COUNT_CELLS)
        after = _get(base, "/api/session")["target"]
    assert cells > 1, "no route active: the full star row must still render"
    assert after["star_id"] == FIXTURE_STAR
