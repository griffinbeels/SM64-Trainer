"""The first-run setup screen (setupmodal.js) actually opens, and its
checklist reflects the capture layer's real status -- not a hand-built
harness page, the real header + the real modal, driven the way a person
reaches it (Settings -> Setup).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from ui_fixture import serve_ui  # noqa: E402
from uilab_project import _SETUP_SETUP  # noqa: E402

LONG_PJ64_DIR = (
    r"C:\Users\griff\Desktop\games\Project64 1.6 (wermi's build v7)\Project64 1.6")
WRAPPED_PLUGIN = "GLideN64_LINK_4.2.dll"


def _open_setup(page):
    page.evaluate(_SETUP_SETUP)
    page.wait_for(".setup-platform-picks")


def test_the_setup_entry_opens_the_platform_step():
    with serve_ui() as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(1500)
        _open_setup(page)
        assert page.count(".setup-platform-choice") == 2, (
            "expected the Emulator/N64 platform step")
        assert page.evaluate(
            "document.querySelector('.setup-platform-choice.is-selected')"
            ".textContent").strip().startswith("Emulator"), (
            "opening at the emu pane should also highlight the Emulator choice")


def test_picking_emulator_shows_the_three_row_checklist():
    with serve_ui() as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(1500)
        _open_setup(page)
        assert page.count(".setup-row") == 3, (
            "expected Project64 / Usamune ROM / Frame-exact capture")


def test_the_consent_card_names_the_dll_the_setting_and_the_wrapped_plugin():
    with serve_ui(capture_layer_status={
            "state": "not_installed", "pj64_dir": LONG_PJ64_DIR,
            "wrapped_name": WRAPPED_PLUGIN}) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(1500)
        _open_setup(page)
        body = page.evaluate("document.querySelector('.setup-consent-card').textContent")
        assert "sm64_trainer_gfx.dll" in body
        assert "Graphics Dll" in body
        assert WRAPPED_PLUGIN in body


def test_the_install_button_carries_its_disabled_reason():
    with serve_ui(capture_layer_status={
            "state": "not_installed", "pj64_dir": LONG_PJ64_DIR,
            "pj64_running": True,
            "problems": ["close Project64 before installing"]}) as url, \
            get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(1500)
        _open_setup(page)
        assert page.evaluate(
            "document.querySelector('.setup-consent-card button').disabled") is True
        reason = page.evaluate(
            "document.querySelector('.setup-disabled-reason').textContent")
        assert "close Project64 before installing" in reason


def test_an_active_layer_renders_active_and_a_remove_button():
    with serve_ui(capture_layer_status={
            "state": "active", "pj64_dir": LONG_PJ64_DIR}) as url, \
            get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(1500)
        _open_setup(page)
        line = page.evaluate(
            "document.querySelector('.setup-active-line').textContent")
        assert "Active" in line
        assert page.count(".setup-active-line button") == 1
        assert page.evaluate(
            "document.querySelector('.setup-active-line button').textContent"
            ).strip() == "Remove"


def test_the_screen_opens_by_itself_for_a_user_who_never_set_up_even_before_project64_is_seen():
    """His first launch after the layer shipped showed no screen because
    Project64 was not running yet. The rule now: not consented (or regressed)
    -> the screen opens on load, with the Project64 row as the door to
    finding the folder; a build with nothing to install stays quiet."""
    with serve_ui(capture_layer_status={"state": "not_installed", "pj64_dir": None,
                                        "pj64_running": False,
                                        "problems": ["start Project64 once so the trainer can find it"]}) as url, \
            get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_for(".setup-platform-picks", timeout_ms=8000)
        rows = page.evaluate("document.querySelector('.setup-checklist').textContent")
        assert "Start Project64 once" in rows
        reason = page.evaluate("document.querySelector('.setup-disabled-reason').textContent")
        assert "start Project64 once" in reason


def test_the_screen_stays_quiet_when_the_layer_is_active():
    with serve_ui(capture_layer_status={"state": "active", "pj64_dir": LONG_PJ64_DIR}) as url, \
            get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(2500)
        assert page.count(".setup-platform-picks") == 0
