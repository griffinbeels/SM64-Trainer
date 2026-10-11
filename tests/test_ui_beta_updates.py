"""Drive the shipping settings and update popup through real local routes.

Only the remote release/update filesystem boundary is replaced. Scratch practice
data and hidden browser ownership match the existing UI fixture contract.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from find_uilab import find_uilab  # noqa: E402

if missing := find_uilab():
    pytest.skip(missing, allow_module_level=True)
from uilab.driver import get_driver  # noqa: E402
import ui_fixture  # noqa: E402


class ReleaseBoundary:
    def __init__(self):
        self.beta = False
        self.checks = []
        self.state = "idle"
        self.files_changed = False
        self.error = ""

    def beta_preferences(self):
        return {"receive_beta_updates": self.beta}

    def status(self, force=False, repair=False):
        self.checks.append((force, repair))
        available = self.beta or repair
        return {"current": "1.9.1", "frozen": True, "updates_enabled": True,
                "channel": "stable", "receive_beta_updates": self.beta,
                "update_available": available, "latest": "1.10.0-beta.1" if self.beta else "1.9.1",
                "notes": "- Race community ghosts.", "html_url": "https://example.com/release",
                "writable": True, "state": self.state, "progress": 0, "releases": [],
                "files_changed": self.files_changed, "error": self.error,
                "repair": repair, "download_bytes": 1048576}

    def set_beta_updates(self, enabled):
        self.beta = enabled
        return self.status(force=True)

    def skip(self, version):
        pass

    def begin_apply(self, **kwargs):
        self.state = "error"
        self.error = "Pause the trainer or close Project64 before updating."
        return {"state": "downloading"}


def settings(page):
    page.wait_for("button[aria-label='Settings']:visible,button[aria-label='Open settings']:visible")
    label = page.evaluate("""(() => {
      const button=Array.from(document.querySelectorAll('button')).find(b =>
        ['Settings','Open settings'].includes(b.getAttribute('aria-label')) && b.getClientRects().length);
      return button?.getAttribute('aria-label');
    })()""")
    assert label
    page.click("button[aria-label=" + json.dumps(label) + "]")
    page.wait_for(".settings-drawer")
    page.wait_for("#receive-beta-updates:not([disabled])")


def change_beta(page):
    page.evaluate("""(() => {
      const label=Array.from(document.querySelectorAll('.settings-field'))
        .find(node => node.textContent.includes('Receive beta updates'));
      label.querySelector('input').click();
    })()""")


@pytest.mark.parametrize("width", [1500, 850, 390])
def test_beta_opt_in_is_immediate_persistent_and_opt_out_removes_offer(tmp_path, monkeypatch, width):
    updater = ReleaseBoundary()
    create = ui_fixture.create_app
    monkeypatch.setattr(ui_fixture, "create_app", lambda *args, **kwargs:
        create(*args, **kwargs, updater=updater))
    with ui_fixture.serve_ui(tmp_path / "practice.db") as base:
        with get_driver().launch(headless=True, viewport=(width, 1000)) as page:
            page.goto(base)
            page.wait_for(".context-shell")
            settings(page)
            assert page.evaluate("""Array.from(document.querySelectorAll('.settings-field'))
              .find(node => node.textContent.includes('Receive beta updates')).querySelector('input').checked""") is False
            change_beta(page)
            page.wait_for(".update-summary")
            assert updater.beta is True
            assert updater.checks[-1][0] is True
            assert "1.10.0-beta.1" in page.evaluate("document.querySelector('.modal').textContent")
            page.evaluate("Array.from(document.querySelectorAll('.modal-actions button')).find(b=>b.textContent==='Later').click()")
            # Finish initial reads before deliberately navigating: an aborted
            # in-flight scorecard request is a harness navigation side effect.
            page._page.wait_for_load_state("networkidle")
            page.goto(base)
            page.wait_for(".update-summary")
            page.evaluate("Array.from(document.querySelectorAll('.modal-actions button')).find(b=>b.textContent==='Later').click()")
            settings(page)
            change_beta(page)
            page.wait_ms(200)
            assert updater.beta is False
            assert page.count(".update-summary") == 0
            folder = ROOT / ".iteration/adaptive-ghosts/round34-ui"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"settings-{width}.png").write_bytes(page.screenshot())
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.problems() == []


def test_repair_uses_existing_popup_and_does_not_offer_skip(tmp_path, monkeypatch):
    create = ui_fixture.create_app
    monkeypatch.setattr(ui_fixture, "create_app", lambda *args, **kwargs:
        create(*args, **kwargs, updater=ReleaseBoundary()))
    with ui_fixture.serve_ui(tmp_path / "repair.db") as base:
        with get_driver().launch(headless=True, viewport=(850, 1000)) as page:
            page.goto(base)
            page.wait_for(".context-shell")
            settings(page)
            page.evaluate("Array.from(document.querySelectorAll('.settings-actions button')).find(b=>b.textContent.includes('Repair installation')).click()")
            page.wait_for(".update-summary")
            text = page.evaluate("document.querySelector('.modal').textContent")
            assert "Repair installation" in text and "Repair now" in text
            assert "Skip this version" not in text
            assert page.problems() == []


@pytest.mark.parametrize("files_changed", [False, True])
def test_failed_update_explains_refusal_and_can_be_reopened(tmp_path, monkeypatch, files_changed):
    updater = ReleaseBoundary()
    updater.files_changed = files_changed
    create = ui_fixture.create_app
    monkeypatch.setattr(ui_fixture, "create_app", lambda *args, **kwargs:
        create(*args, **kwargs, updater=updater))
    with ui_fixture.serve_ui(tmp_path / "failure.db") as base:
        with get_driver().launch(headless=True, viewport=(850, 1000)) as page:
            page.goto(base)
            settings(page)
            change_beta(page)
            page.wait_for(".update-summary")
            page.click(".modal-actions .primary-button")
            page.wait_for(".update-error")
            text = page.evaluate("document.querySelector('.modal').textContent")
            assert "Pause the trainer or close Project64" in text
            assert ("Try again" in text) is not files_changed
            page.evaluate("Array.from(document.querySelectorAll('.modal-actions button')).find(b=>b.textContent==='Close').click()")
            page._page.locator(".update-summary").wait_for(state="hidden")
            page.evaluate("Array.from(document.querySelectorAll('.settings-actions button')).find(b=>b.textContent.includes('Check for updates')).click()")
            page.wait_for(".update-summary")
            assert page.problems() == []
