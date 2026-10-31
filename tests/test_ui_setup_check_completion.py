"""The setup wizard's completion page shows setup differences as notes.

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


def test_the_wizard_completion_shows_differences_as_notes_without_blocking(tmp_path):
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
        wait_page(page, "install")
        installed(layer)
        layer.update(state="active", layer_alive=True)
        wait_page(page, "complete")
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
