"""The installed app's Ghost Lab on the real page, before it found a ROM (D2 on by default, D6 the plain reason).

The UI fixture boots the real app offline; the lab is mounted the way main.py mounts it in the installed app: no
lab.json, its config from core/lab_inputs.py with a ROM store that holds nothing yet. The sidebar shows the Ghost
Lab as its own entry in the Play group, the page opens without an error, and its empty stage shows the ROM card:
the store's whole sentence (the bar's one line cuts it at 1280) and Choose ROM file. CPU only: a headless browser without the
GPU, and the renderer is never started (nothing can open without a ROM)."""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import gpu_gate  # noqa: E402
import ui_fixture  # noqa: E402
from sm64_events.core import lab_inputs  # noqa: E402
from sm64_events.scenelab import roms  # noqa: E402
from sm64_events.server import lab_roms_api, scene_lab_api  # noqa: E402

REASON = "The Ghost Lab needs the Super Mario 64 Usamune v1.93u ROM. Open it in Project64 once (a test's words)."
# A page that grabs the pointer confines his real cursor to the test's viewport (2026-11 note): stub it.


class EmptyStore:
    def rom_for(self, region):
        return None

    def missing_reason(self, region):
        return REASON


class NoRomStore(roms.RomStore):
    """The ROM routes' store with nothing found, that never looks at this computer's Project64."""

    def __init__(self, root):
        super().__init__(root, pj64_dir=lambda: None, registry=None, reader=lambda: None)

    def copy_from_game(self):
        return None

    def scan_folders(self, pj64_dir=None):
        return []


@pytest.mark.parametrize("width", [850, 1280, 1600])
def test_the_apps_ghost_lab_opens_and_says_it_needs_the_rom(width, tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    (tmp_path / "Plugin").mkdir()
    statuses = []
    original = ui_fixture.create_app

    def create_app(*args, **kwargs):
        app = original(*args, **kwargs)
        app.include_router(lab_roms_api.create_rom_router(NoRomStore(tmp_path / "roms")))   # before the lab's own
        scene_lab_api.mount_scene_lab(app, None, app_inputs=lambda: lab_inputs.app_inputs(
            store=EmptyStore(), lab_file=lambda name: None, plugin=lambda: tmp_path / "Plugin",
            lab_root=tmp_path / "lab-root"))
        return app
    monkeypatch.setattr(ui_fixture, "create_app", create_app)
    with ui_fixture.serve_ui() as base, sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": width, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("response", lambda response: statuses.append(response.json())
                    if response.url.endswith("/api/scene-lab/status") else None)
            page.goto(base)
            entry = page.locator(".nav-item", has_text="Ghost Lab")
            entry.wait_for(timeout=20000)
            entry.click()
            page.locator(".scene-lab").wait_for(timeout=20000)
            card = page.locator(".ghost-lab-empty .rom-pick[data-state=missing]")
            card.wait_for(timeout=20000)
            # One place says why: the card, in full; the bar's one line says nothing (the lead, 2026-11-09).
            page.wait_for_timeout(300)
            assert page.get_by_text(REASON).count() == 0
            assert page.get_by_text(roms.missing_sentence("us")).count() == 1
            sentence = card.locator(".rom-pick-reason")
            assert sentence.text_content() == roms.missing_sentence("us")
            assert sentence.evaluate("e => e.scrollWidth <= e.clientWidth && e.scrollHeight <= e.clientHeight + 1")
            assert card.get_by_role("button", name="Choose ROM file").is_visible()
            labels = page.locator(".app-sidebar .nav-group-label").all_text_contents()
            assert "Lab" not in [label.strip() for label in labels]                     # no one-item Lab group
            play = page.locator(".app-sidebar .nav-group", has=page.locator(".nav-group-label", has_text="Play"))
            assert play.locator(".nav-item", has_text="Ghost Lab").count() == 1
            assert page.locator(".nav-item.on").text_content().strip() == "Ghost Lab"   # 850: an icon, text hidden
            assert statuses and statuses[-1]["ready"] is False and statuses[-1]["problems"][0] == REASON
            assert errors == []
            page.screenshot(path=str(tmp_path / f"ghost-lab-no-rom-{width}.png"))
        finally:
            browser.close()
