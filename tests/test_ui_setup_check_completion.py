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
LIVE = ".setup-modal > .setup-swap > .setup-swap-in"
_CHECKLIST = f"""(() => ({{
  cards: document.querySelectorAll('{LIVE} .setup-current').length,
  title: document.querySelector('{LIVE} .setup-current h3').innerText.trim(),
  done: [...document.querySelectorAll('{LIVE} .setup-done-steps li')].map(row => row.innerText.trim()),
}}))()"""


def _checklist(page):
    """What the checklist shows now: one card, and the finished checks above it."""
    page.wait_ms(450)
    return page.evaluate(_CHECKLIST)
CONTINUE = "[data-testid=setup-risk-continue]"


def _walk_the_download_checklist(page):
    """One sub-step at a time, each with a checkpoint; no later one before its turn."""
    from tests.test_ui_setup_modal import body, click_text, wait_page
    wait_page(page, "connect")
    assert _checklist(page) == {"cards": 1, "title": "Download Wermi's Build v7", "done": []}
    assert "Unpack" not in body(page) and "Open Project64.exe" not in body(page)
    click_text(page, "I downloaded it")
    assert _checklist(page) == {"cards": 1, "title": "Unpack Project64", "done": ["Wermi's Build v7 downloaded"]}
    click_text(page, "I unpacked it")
    assert _checklist(page) == {"cards": 1, "title": "Open Project64",
                                "done": ["Wermi's Build v7 downloaded", "Project64 unpacked"]}
    click_text(page, "Go back")
    assert _checklist(page)["title"] == "Unpack Project64"


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
    from tests.test_ui_setup_modal import ALL_CHECKS, US_ROM, body, click_text, closed, connect, installed, \
        scenario, wait_page, wait_step
    check = ServedCheck()
    check.value = stock_check(tmp_path / "stock")
    layer, observed = scenario()
    with with_setup_check(check), ui_fixture.serve_ui(capture_layer_status=layer,
                                                        setup_observer=lambda _: observed) as url, \
            get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        wait_page(page, "platform")
        page.click(".setup-platform-choice:first-child")
        # Getting Project64 is a checklist: one sub-step at a time, each with a
        # checkpoint, and no later sub-step on screen before its turn.
        _walk_the_download_checklist(page)
        connect(layer, observed)
        # Project64 is found, but it is not Wermi's build v7: getting it starts over.
        assert check.value["gate_differs"] == ["emulator_build", "rsp_plugin"]
        page.wait_for("[data-testid=setup-gate]")
        assert page.count("[data-testid=setup-gate] [data-item=emulator_build]") == 1
        assert page.count("[data-testid=setup-gate] [data-item=rsp_plugin]") == 0
        # Getting the right build is a checklist: one sub-step shows at a time.
        assert page.count(".setup-connect .setup-current") == 1
        assert page.evaluate("document.querySelector('.setup-connect').dataset.connectStep") == "download"
        assert page.evaluate("document.querySelector('.setup-download').href").startswith("https://www.dropbox.com/")
        (tmp_path / "gate-connect.png").write_bytes(page.screenshot())
        _continue_through_the_warning(page, "connect")
        wait_page(page, "install")
        layer.update(pj64_running=False)
        installed(layer)
        # The plugin is in, the fixups are not: their own step, before the game opens.
        wait_step(page, "fixups")
        assert _checklist(page) == {"cards": 1, "title": "Install the PJ64 Fixups",
                                    "done": ["Project64 closed", "Practice Replay installed"]}
        assert "Open your game" not in body(page)
        (tmp_path / "gate-install.png").write_bytes(page.screenshot())
        _continue_through_the_warning(page, "install")
        wait_step(page, "reopen")
        assert _checklist(page)["title"] == "Open your game"
        layer.update(state="active", layer_alive=True, pj64_running=True)
        observed.update(rom=dict(US_ROM), checks=dict(ALL_CHECKS))
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
