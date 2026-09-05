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
    """The Install button lives on its STEP now, and stays disabled until
    the step before it (close Project64) is ticked; the reason still
    prints on the consent card."""
    steps = [{"id": "close", "label": "Close Project64", "done": False, "action": None},
             {"id": "install", "label": "Install the capture layer", "done": False,
              "action": "install"},
             {"id": "start", "label": "Start Project64 and open the Usamune ROM", "done": False,
              "action": None}]
    with serve_ui(capture_layer_status={
            "state": "not_installed", "pj64_dir": LONG_PJ64_DIR,
            "pj64_running": True, "steps": steps,
            "problems": ["close Project64 before installing"]}) as url, \
            get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(1500)
        _open_setup(page)
        assert page.evaluate(
            "document.querySelector('.setup-steps button').disabled") is True
        assert page.count(".setup-consent-card button") == 0
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


def test_an_active_layer_with_a_problem_shows_it_on_the_row_and_offers_update():
    """2026-09-05: the row read a green "Active" while the layer refused
    every picture and the timeline said capture was off. The problem now
    sits on the row itself, and a newer build's layer gets its Update."""
    problem = ("the capture layer is loaded but no picture reaches it (no OpenGL "
               "context on the emulation thread, and the graphics plugin's ReadScreen "
               "gave nothing); recording uses desktop capture until this is fixed")
    with serve_ui(capture_layer_status={
            "state": "active", "pj64_dir": LONG_PJ64_DIR, "wrapper_current": False,
            "problems": [problem, "this build carries a newer capture layer; Update to install it"],
            }) as url, \
            get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(1500)
        _open_setup(page)
        assert "Active" in page.evaluate(
            "document.querySelector('.setup-active-line').textContent")
        problems = page.evaluate(
            "Array.from(document.querySelectorAll('.setup-row-problem')).map(p => p.textContent)")
        assert problems[0] == problem
        assert "newer capture layer" in problems[1]
        buttons = page.evaluate(
            "Array.from(document.querySelectorAll('.setup-active-line button')).map(b => b.textContent.trim())")
        assert buttons == ["Update", "Remove"]


def test_the_steps_render_in_order_with_live_ticks_and_the_screen_opens_for_a_stale_layer():
    """His rule (2026-09-05): onboarding is the exact set of steps. A newer
    build's layer with Project64 open: the row lists close / updated /
    start, the first unticked step is the one to do, and the screen opens
    by itself on load because those steps ARE the onboarding."""
    steps = [{"id": "close", "label": "Close Project64 -- the trainer then updates the capture layer by itself",
              "done": False, "action": None},
             {"id": "update", "label": "Capture layer updated", "done": False, "action": None},
             {"id": "start", "label": "Start Project64 and open the Usamune ROM", "done": False,
              "action": None}]
    with serve_ui(capture_layer_status={
            "state": "active", "pj64_dir": LONG_PJ64_DIR, "wrapper_current": False,
            "problems": ["this build carries a newer capture layer; close Project64, then Update"],
            "steps": steps}) as url, \
            get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_for(".setup-platform-picks")          # opened by itself
        labels = page.evaluate(
            "Array.from(document.querySelectorAll('.setup-tick-label')).map(e => e.textContent)")
        assert [label.split(" ")[0] for label in labels] == ["Close", "Capture", "Start"]
        marks = page.evaluate(
            "Array.from(document.querySelectorAll('.setup-tick')).map(e => e.textContent)")
        assert marks == ["○", "○", "○"]
        assert page.count(".setup-step.is-next") == 1


def test_a_first_install_puts_the_install_button_on_its_step_after_close(tmp_path):
    steps = [{"id": "close", "label": "Close Project64", "done": True, "action": None},
             {"id": "install", "label": "Install the capture layer", "done": False,
              "action": "install"},
             {"id": "start", "label": "Start Project64 and open the Usamune ROM", "done": False,
              "action": None}]
    with serve_ui(capture_layer_status={
            "state": "not_installed", "pj64_dir": LONG_PJ64_DIR, "consented_at": None,
            "wrapper_present": False, "wrapper_current": False, "wrapper_selected": False,
            "registry_graphics_dll": WRAPPED_PLUGIN, "layer_alive": False, "gl_context": False,
            "pj64_running": False, "problems": [], "steps": steps}) as url, \
            get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_for(".setup-platform-picks")
        marks = page.evaluate(
            "Array.from(document.querySelectorAll('.setup-tick')).map(e => e.textContent)")
        assert marks == ["✓", "○", "○"]
        button = page.evaluate(
            "(() => { const b = document.querySelector('.setup-step.is-next button'); return b && [b.textContent.trim(), b.disabled]; })()")
        assert button == ["Install the capture layer", False]
        assert page.count(".setup-consent-card button") == 0


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
