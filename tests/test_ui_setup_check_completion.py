"""The setup wizard stops at the gate (Wermi's build v7, the PJ64 Fixups), and
its completion page shows the remaining setup differences as notes.

Its own file: it drives the wizard through the uilab driver, which must not
share a thread with test_ui_setup_check.py's module-wide Playwright browser.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

import ui_fixture  # noqa: E402
from tests.test_setup_profile import stock_check  # noqa: E402
from tests.test_ui_setup_check import ITEMS, ServedCheck, with_setup_check  # noqa: E402


RISK = "[data-testid=setup-risk]"
CONTINUE = "[data-testid=setup-risk-continue]"


def _continue_through_the_warning(page, expected_page):
    """The page never moves on by itself; Continue is a warning that asks first."""
    from tests.test_ui_setup_modal import click_text, wait_page
    page.wait_for(CONTINUE)
    page.wait_ms(1600)                       # longer than the wizard's own auto-advance
    wait_page(page, expected_page)
    assert page.count(RISK) == 0
    page.click(CONTINUE)
    page.wait_for(RISK)
    assert "only tested with Wermi's Build v7 and the PJ64 Fixups" in page.evaluate(
        f"document.querySelector('{RISK}').innerText")
    click_text(page, "Go back and fix it")   # backing out leaves the page where it was
    page.wait_ms(300)
    assert page.count(RISK) == 0
    wait_page(page, expected_page)
    page.click(CONTINUE)
    page.wait_for(RISK)
    click_text(page, "Continue anyway")


def test_another_build_stops_the_wizard_until_the_warning_is_accepted(tmp_path):
    from uilab.driver import get_driver
    from tests.test_ui_setup_modal import ALL_CHECKS, US_ROM, click_text, closed, connect, installed, \
        scenario, wait_page
    check = ServedCheck()
    check.value = stock_check(tmp_path / "stock")
    layer, observed = scenario()
    connect(layer, observed)
    observed.update(rom=dict(US_ROM), checks=dict(ALL_CHECKS))
    with with_setup_check(check), ui_fixture.serve_ui(capture_layer_status=layer,
                                                        setup_observer=lambda _: observed) as url, \
            get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        wait_page(page, "platform")
        page.click(".setup-platform-choice:first-child")
        # Project64 is found, but it is not Wermi's build v7.
        assert check.value["gate_differs"] == ["emulator_build", "rsp_plugin"]
        page.wait_for("[data-testid=setup-gate] [data-item=emulator_build]")
        assert page.count("[data-testid=setup-gate] [data-item=rsp_plugin]") == 0
        assert page.evaluate("document.querySelector('.setup-download').href").startswith("https://www.dropbox.com/")
        (tmp_path / "gate-connect.png").write_bytes(page.screenshot())
        _continue_through_the_warning(page, "connect")
        wait_page(page, "install")
        installed(layer)
        layer.update(state="active", layer_alive=True)
        # The setup checks out, and the fixups are still missing: a second stop.
        page.wait_for("[data-testid=setup-gate] [data-item=rsp_plugin]")
        assert page.count("[data-testid=setup-gate] [data-item=emulator_build]") == 0   # already accepted
        (tmp_path / "gate-install.png").write_bytes(page.screenshot())
        _continue_through_the_warning(page, "install")
        wait_page(page, "complete")
        accepted = page.evaluate("fetch('/api/setup').then(r => r.json())")["onboarding"]["baseline_risk"]
        assert accepted["items"] == ["emulator_build", "rsp_plugin"] and accepted["accepted_at"]
        page.wait_for("[data-testid=setup-check-notes] .setup-check-item")
        notes = page.evaluate(f"({ITEMS})('[data-testid=setup-check-notes]')")
        assert [row["id"] for row in notes] == check.value["flagged"]
        assert all(row["inside"] and not row["overflow"] for row in notes), notes
        assert "You can still practice." in page.evaluate(
            "document.querySelector('[data-testid=setup-check-notes]').innerText")
        # Notes never push the finish button out of sight.
        assert page.evaluate("(() => { const r = document.querySelector('.setup-finish').getBoundingClientRect();"
                             " const body = document.querySelector('.modal-body').getBoundingClientRect();"
                             " return r.bottom <= body.bottom && r.top >= body.top; })()")
        (tmp_path / "setupbase-completion.png").write_bytes(page.screenshot())
        click_text(page, "Ready to practice!")
        closed(page)
        assert page.problems() == []
