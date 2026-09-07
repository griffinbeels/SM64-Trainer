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
LABEL = CARD + " .scorecard-head .search-select-value"
TRIGGER = CARD + " .scorecard-head .search-select-trigger"


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


def _open_menu(page):
    page.click(TRIGGER)
    page.wait_for(CARD + " .search-menu")


def _restore_auto_from_manual(page):
    _open_menu(page)
    page.evaluate(_option_click("division:Bronze:I"))
    _label_is(page, "Toad 1")
    page.evaluate(_option_click(""))
    page.wait_for(CARD + ' .search-menu-option[data-value=""].is-picked')
    page.click(TRIGGER)


def _save_named_edit(page, base, route):
    payload = _read(base, "/api/scorecard?scope=" + route)
    row = payload["rows"][0]
    _expand_row_and_edit_goal(page, row["label"], row["tiles"][0]["label"], '1\'00"00')
    page.click(CARD + " .scorecard-savebar-input")
    page.evaluate("(() => { const input = document.querySelector("
                  "'.scorecard-savebar-input');"
                  "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')"
                  ".set.call(input, 'My route goal');"
                  "input.dispatchEvent(new Event('input', {bubbles:true})); })()")
    page.click(CARD + " .scorecard-savebar button:first-of-type")
    _label_is(page, "My route goal")
    assert page.count(CARD + " .score-line") == 1
    assert _read(base, "/api/scorecard?scope=" + route)["rows"][0]["tiles"][0]["goal_cs"] == 6000
    _scope(page, "overall")
    _label_is(page, "My route goal")
    _scope(page, route)
    _label_is(page, "My route goal")


@pytest.mark.parametrize("width", [1500, 850])
def test_automatic_manual_and_clear_flows_follow_the_visible_scope(width, tmp_path):
    with serve_ui_live() as (base, service):
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
            initial_label = page.evaluate(f"document.querySelector({json.dumps(LABEL)}).textContent")
            _check_browser_errors(page, base)
            _open_menu(page)
            page.wait_for(CARD + ' .search-menu-option[data-value=""].is-picked')
            assert not page.count(CARD + ' .search-menu-option[data-value="division:Iron:V"]')
            page.click(TRIGGER)

            _scope(page, route)
            assert page.evaluate(f"document.querySelector({json.dumps(LABEL)}).textContent") != initial_label
            assert page.count(CARD + " .score-line") == 1
            auto = _read(base, "/api/scorecard?scope=" + route)["goal"]
            rank = _read(base, "/api/marelo?scope=" + route)
            assert auto["kind"] == "automatic"
            assert rank["scope_id"] == route
            page.evaluate(f"document.querySelector({json.dumps(CARD)}).scrollIntoView()")
            (tmp_path / f"automatic-{width}.png").write_bytes(page.screenshot())

            _open_menu(page)
            page.evaluate(_option_click("division:Bronze:I"))
            _label_is(page, "Toad 1")
            page.wait_for(CARD + ' .search-menu-option[data-value="runner:RONC3NA"]')
            page.evaluate(_option_click("runner:RONC3NA"))
            _label_is(page, "2 picked")
            page.click(TRIGGER)
            _scope(page, "overall")
            _label_is(page, "2 picked")
            assert page.count(CARD + " .goal-pill") == 2
            _scope(page, route)
            _label_is(page, "2 picked")
            page.click(CARD + ' .goal-pill-remove[aria-label="Remove RONC3NA from the goal"]')
            _label_is(page, "Toad 1")

            # Unticking the last pick returns to automatic, with real times.
            _open_menu(page)
            page.evaluate(_option_click("division:Bronze:I"))
            page.wait_for(CARD + ' .search-menu-option[data-value=""].is-picked')
            assert _read(base, "/api/scorecard?scope=" + route)["goal"] == auto
            page.click(TRIGGER)

            # The explicit automatic option also clears an entire manual set.
            _restore_auto_from_manual(page)

            # Saving a named edit must keep this route's single-row card.
            _save_named_edit(page, base, route)
            page.evaluate(f"document.querySelector({json.dumps(CARD)}).scrollIntoView()")
            (tmp_path / f"custom-{width}.png").write_bytes(page.screenshot())
            _check_browser_errors(page, base)
        assert service.db.get_state("scorecard_goal", None) == {
            "kind": "custom", "name": "My route goal"}
