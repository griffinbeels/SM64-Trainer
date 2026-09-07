"""The real goal picker switches between a moving scope goal and saved picks."""
import json
import time
import urllib.request

import pytest

from test_ui_scorecard import (
    _OPEN_RANK_TAB, _expand_row_and_edit_goal, _option_click,
    get_driver, serve_ui_live,
)
from ui_fixture import FIXTURE_COURSE, FIXTURE_STAR

CARD = ".rank-page .scorecard-card"
LABEL = CARD + ' [data-goal-control="Rank Goal"] .search-select-value'
TRIGGER = CARD + ' [data-goal-control="Rank Goal"] .search-select-trigger'


def _wait(page, expression):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if page.evaluate(expression):
            return
        page.wait_ms(50)
    pytest.fail(f"Page did not reach: {expression}")


def _read(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return json.loads(response.read())


def _check_browser_errors(page, base):
    # The offline fixture omits replay and update controllers; Rank also
    # asks for replay availability on each scope. Account for only these
    # exact requests and their paired console messages.
    problems = page.problems()
    omitted = {f"HTTP 404 {base}/api/{path}" for path in (
        "replay/status", "replay/available", "update/status")}
    resource_error = "console.error: Failed to load resource: the server responded with a status of 404 (Not Found)"
    assert all(p in omitted or p == resource_error for p in problems), "\n".join(problems)
    assert len(problems) == 2 * sum(p in omitted for p in problems), "\n".join(problems)


def _scope(page, scope):
    page.evaluate("(() => { const select = document.querySelector("
                  "'.rank-page .route-focus-control select');"
                  f"select.value = {json.dumps(scope)};"
                  "select.dispatchEvent(new Event('change', {bubbles:true})); })()")
    page.wait_for(CARD + f'[data-scope="{scope}"] .score-card')


def _label_is(page, text):
    _wait(page, f"document.querySelector({json.dumps(LABEL)})?.textContent === "
          + json.dumps(text))


def _open_menu(page, label="Rank Goal"):
    page.click(CARD + f' [data-goal-control="{label}"] .search-select-trigger')
    page.wait_for(CARD + " .search-menu")


def _pick_rank(page, value):
    _open_menu(page)
    page.evaluate(_option_click(value))
    _wait(page, f"!document.querySelector({json.dumps(CARD + ' .search-menu')})")


def _name_and_save(page, name):
    page.evaluate("(() => { const input = document.querySelector('.scorecard-savebar-input');"
                  + f"input.value = {json.dumps(name)};"
                  + "input.dispatchEvent(new Event('input', {bubbles:true})); })()")
    page.click(CARD + " .scorecard-savebar button:first-of-type")
    _wait(page, "!document.querySelector('.scorecard-savebar')")


@pytest.mark.parametrize("width", [1500, 850])
def test_automatic_manual_and_clear_flows_follow_the_visible_scope(width, tmp_path):
    with serve_ui_live(reconcile_full_corpus=True) as (base, service):
        route = "route:" + str(service.db.insert_route("Practiced star", [
            {"need": 1, "candidates": [{"type": "star", "course": FIXTURE_COURSE,
                                      "star": FIXTURE_STAR}]}
        ], "2026-09-06T00:00:00Z"))
        with get_driver().launch(headless=True, viewport=(width, 1000)) as page:
            page.goto(base + "/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(TRIGGER)
            _wait(page, f"document.querySelector({json.dumps(LABEL)})?.textContent.startsWith('Automatic · ')")
            initial = page.evaluate(f"document.querySelector({json.dumps(LABEL)}).textContent")
            assert page.count(CARD + " .goal-pill, " + CARD + " .score-line-source") == 0
            _open_menu(page)
            values = page.evaluate("[...document.querySelectorAll('.scorecard-card .search-menu-option')].map(e=>e.dataset.value)")
            assert values[0] == "" and all(v.startswith("division:") for v in values[1:])
            assert "division:Iron:V" not in values
            page.click(TRIGGER)
            _scope(page, route)
            assert page.evaluate(f"document.querySelector({json.dumps(LABEL)}).textContent") != initial
            auto = _read(base, "/api/scorecard?scope=" + route)["goal"]
            page.evaluate(f"document.querySelector({json.dumps(CARD)}).scrollIntoView()")
            (tmp_path / f"automatic-{width}.png").write_bytes(page.screenshot())

            _pick_rank(page, "division:Bronze:I")
            _label_is(page, "Toad 1")
            _open_menu(page, "Player Goal")
            page.wait_for(CARD + ' .search-menu-option[data-value="runner:RONC3NA"]')
            values = page.evaluate("[...document.querySelectorAll('.scorecard-card .search-menu-option')].map(e=>e.dataset.value)")
            assert all(v.startswith("runner:") for v in values)
            page.evaluate(_option_click("runner:RONC3NA"))
            page.wait_for(CARD + ' .search-menu-option.is-picked')
            page.click(CARD + ' [data-goal-control="Player Goal"] .search-select-trigger')
            _scope(page, "overall")
            _label_is(page, "Toad 1")
            page.wait_for(CARD + " .goal-pill:nth-child(2)")
            assert page.count(CARD + " .goal-pill:first-child button") == 0
            assert page.count(CARD + " .goal-pill:first-child > *") == 2
            _scope(page, route)
            _label_is(page, "Toad 1")

            # Automatic changes only the rank; a selected player stays on.
            _pick_rank(page, "")
            _wait(page, f"document.querySelector({json.dumps(LABEL)}).textContent.startsWith('Automatic · ')")
            assert _read(base, "/api/scorecard?scope=" + route)["goal"]["sources"][0] == auto
            page.click(CARD + ' .goal-pill-remove[aria-label="Remove RONC3NA from the goal"]')
            _wait(page, "!document.querySelector('.goal-pill')")
            assert _read(base, "/api/scorecard?scope=" + route)["goal"] == auto

            # A named set stores just edited entries and accumulates across scopes.
            page.click(CARD + " .scorecard-new-set")
            first = _read(base, "/api/scorecard?scope=" + route)["rows"][0]
            _expand_row_and_edit_goal(page, first["label"], first["tiles"][0]["label"], '1"00')
            _name_and_save(page, "Practice set")
            key = first["tiles"][0]["key"]
            assert service.db.get_state("scorecard_custom_goals", {})["Practice set"] == {key: 100}
            _scope(page, "overall")
            payload = _read(base, "/api/scorecard")
            row, tile = next((r, t) for r in payload["rows"] for t in r["tiles"] if t["key"].startswith("segment:"))
            _expand_row_and_edit_goal(page, row["label"], tile["label"], '2"00')
            assert page.evaluate("document.querySelector('.scorecard-savebar-input').value") == "Practice set"
            _name_and_save(page, "Practice set")
            assert service.db.get_state("scorecard_custom_goals", {})["Practice set"] == {key: 100, tile["key"]: 200}
            page.click(CARD + ' .goal-pill-remove[aria-label="Remove Practice set from the goal"]')
            _wait(page, "!document.querySelector('.goal-pill')")
            _open_menu(page, "Custom Goals")
            assert page.count(CARD + " .search-menu-option") == 1
            page.evaluate(_option_click("custom:Practice set"))
            page.click(CARD + ' [data-goal-control="Custom Goals"] .search-select-trigger')
            _wait(page, "document.querySelectorAll('.goal-pill').length === 2")
            _wait(page, "document.querySelectorAll('.score-line-source').length > 0")
            _scope(page, route)
            assert _read(base, "/api/scorecard?scope=" + route)["rows"][0]["tiles"][0]["goal_cs"] == 100
            page.evaluate(f"document.querySelector({json.dumps(CARD)}).scrollIntoView()")
            (tmp_path / f"custom-{width}.png").write_bytes(page.screenshot())
            _check_browser_errors(page, base)


def test_a_goal_set_save_does_not_discard_a_later_time_edit():
    from test_ui_scorecard import _HOLD_GOAL_RESPONSES

    with serve_ui_live() as (base, service):
        with get_driver().launch(headless=True, viewport=(850, 1000)) as page:
            page.goto(base)
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(CARD + " .score-line")
            row = _read(base, "/api/scorecard")["rows"][0]
            tile = row["tiles"][0]
            page.click(CARD + " .scorecard-new-set")
            _expand_row_and_edit_goal(page, row["label"], tile["label"], '1"00')
            page.evaluate(_HOLD_GOAL_RESPONSES.replace("FAIL_FIRST", "false"))
            page.evaluate("window.releaseOldCard = () => {}")
            page.evaluate("(() => { const input = document.querySelector('.scorecard-savebar-input');"
                          "input.value = 'Practice set';"
                          "input.dispatchEvent(new Event('input', {bubbles:true})); })()")
            page.click(CARD + " .scorecard-savebar button:first-of-type")
            _wait(page, "!!window.releaseGoal")
            _expand_row_and_edit_goal(page, row["label"], tile["label"], '2"00')
            page.evaluate("window.releaseGoal()")
            _wait(page, "!document.querySelector('.scorecard-savebar button').disabled")
            assert service.db.get_state("scorecard_custom_goals", {})["Practice set"] == {tile["key"]: 100}
            _name_and_save(page, "Practice set")
            assert service.db.get_state("scorecard_custom_goals", {})["Practice set"] == {tile["key"]: 200}
            _check_browser_errors(page, base)


def test_reported_scorecard_times_open_the_corresponding_library_rows(tmp_path):
    from test_ui_scorecard import _put_division_goal

    with serve_ui_live(reconcile_full_corpus=True) as (base, service):
        cases = [(4, 6, "100c + Race · Standard", "Big Penguin Race + 100c", '1\'30"94'),
                 (8, 2, "Pillarless", "Inside the Ancient Pyramid", '1\'11"46')]
        for course, star, active, _label, _expected in cases:
            service.strat_by_star[(course, star)] = active
        _put_division_goal(base, "Iron", "II")
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".log-list-card")
            for course, _star, _active, label, expected in cases:
                page.evaluate(_OPEN_RANK_TAB)
                page.wait_for(CARD + " .score-line")
                link = CARD + f' .score-line-link[title="Open {label} in the Library"]'
                page.wait_for(link)
                shown = page.evaluate(f"document.querySelector({json.dumps(link)})"
                                      ".closest('.score-line').querySelector('.score-line-goal').textContent.trim()")
                assert shown == expected
                page.evaluate(f"document.querySelector({json.dumps(link)}).closest('.score-card').scrollIntoView()")
                (tmp_path / f"goal-course-{course}.png").write_bytes(page.screenshot())
                page.click(link)
                page.wait_for(".library-target .library-section.open")
                _wait(page, "[...document.querySelectorAll('.library-section.open .library-section-name')]"
                      f".some(el => el.textContent === {json.dumps(label)})")
                (tmp_path / f"library-course-{course}.png").write_bytes(page.screenshot())
            _check_browser_errors(page, base)
