"""The practice log's defaults on the real page: resets and deaths start hidden.

His words (2026-11-09): "By default "Hide resets & deaths" should be enabled
(if it's not already)". A fresh profile (nothing in localStorage) hides them; a
stored "0" -- his own earlier choice to show them -- keeps them shown; the
settings switch still flips the log and remembers. The expected rows come from
the /api/session payload the page draws (test_ui_pb_filter.py's helpers).
"""
from playwright.sync_api import sync_playwright

from frontend_runner import run_frontend
from test_ui_pb_filter import (FAILURES, ROWS, expected_rows, fixture_star,
                               seed_pb_history, wait_for_rows)
from ui_fixture import serve_ui_live

FAILED = f"{ROWS} .attempt-result.badx"


def test_the_settings_panel_defaults_and_remembers_in_jsdom():
    run_frontend("practicelogsettings.test.js")


def _resets_switch(page):
    page.get_by_role("button", name="Practice log settings", exact=True).click()
    return page.get_by_role("dialog", name="Practice log settings").get_by_role(
        "switch", name="Hide resets and deaths")


def test_a_fresh_profile_hides_resets_and_deaths_and_his_choice_to_show_them_stays():
    with serve_ui_live() as (base, service):
        seed_pb_history(service)
        sec = fixture_star(base, "session")
        assert sum(row["outcome"] in FAILURES for row in sec["attempts"]) >= 3, "the fixture holds failures"
        hidden, shown = expected_rows(sec), expected_rows(sec, hide_resets=False)
        assert hidden != shown
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
            try:
                fresh = browser.new_page(viewport={"width": 1280, "height": 1100})
                fresh.goto(base)
                assert wait_for_rows(fresh, hidden) == hidden
                assert fresh.locator(FAILED).count() == 0
                assert fresh.evaluate("localStorage.getItem('sm64.hideResets')") is None
                resets = _resets_switch(fresh)
                assert resets.is_checked()
                resets.click()                                   # the switch still flips the log
                assert wait_for_rows(fresh, shown) == shown
                assert fresh.locator(FAILED).count() > 0
                assert fresh.evaluate("localStorage.getItem('sm64.hideResets')") == "0"
                fresh.reload()
                assert wait_for_rows(fresh, shown) == shown           # and remembers
                assert not _resets_switch(fresh).is_checked()
                fresh.close()

                chosen = browser.new_page(viewport={"width": 1280, "height": 1100})
                chosen.add_init_script("if (!sessionStorage.getItem('seeded')) {"
                                       " localStorage.setItem('sm64.hideResets', '0');"
                                       " sessionStorage.setItem('seeded', '1'); }")
                chosen.goto(base)
                assert wait_for_rows(chosen, shown) == shown          # a stored "0" stays honored
                assert chosen.locator(FAILED).count() > 0
                resets = _resets_switch(chosen)
                assert not resets.is_checked()
                resets.click()
                assert wait_for_rows(chosen, hidden) == hidden
                assert chosen.evaluate("localStorage.getItem('sm64.hideResets')") == "1"
                chosen.reload()
                assert wait_for_rows(chosen, hidden) == hidden
                assert chosen.locator(FAILED).count() == 0
            finally:
                browser.close()
