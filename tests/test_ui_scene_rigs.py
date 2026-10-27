"""The Scene viewer's cinematic cameras on the real page (round 6, item 1): the Cinematic row asks the
lab for a rig, the corner names it, its settings fit beside the player at both supported widths, and
Free camera takes over from it.

The app boots through the UI fixture with test_ui_attempt_scene.py's scratch lab and fake renderer
host (headless, no GPU). That recording carries no scene state, so the lab answers each rig with the
recorded picture and its reason, which the page shows: the camera's own pictures are the GPU check's
(the round's report) and its requests are pinned in tests/frontend/sceneviewer.test.js.
"""
from playwright.sync_api import sync_playwright

import test_ui_attempt_scene as attempt_scene
from test_ui_scene_viewer import CAMERAS, DRAWN, PLAYING

LAST_RIG = "(window.__cameras.at(-1) || {}).rig?.kind ?? null"
# The player card's widest row against the card: nothing of the Cinematic rows may stick out.
FITS = r"""
(() => {
  const card = document.querySelector('.scene-lab-player').getBoundingClientRect();
  const rows = [...document.querySelectorAll('.scene-lab-rigs, .scene-lab-rig-options')];
  return { rows: rows.length, over: Math.max(...rows.flatMap((row) => [...row.children].map(
    (child) => child.getBoundingClientRect().right - card.right))) };
})()
"""


def test_the_cinematic_row_asks_for_a_rig_names_it_fits_both_widths_and_hands_over_to_free_camera(tmp_path, monkeypatch):
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True) as (base, _seen), sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.add_init_script(CAMERAS)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            ready = page.locator(f"{attempt_scene.ROWS} .attempt-scene:not([disabled])").first
            ready.wait_for(timeout=20000)
            ready.click()
            page.wait_for_function(DRAWN, timeout=20000)
            page.wait_for_function(f"!({PLAYING})", timeout=20000)
            rigs = page.locator(".scene-lab-rigs")
            assert page.locator(".scene-lab-rig-options").count() == 0              # no settings until one is on

            rigs.get_by_role("button", name="Front").click()
            page.wait_for_function(f"{LAST_RIG} === 'front'")
            assert page.locator(".scene-lab-tag").inner_text() == "Front · Mario"
            assert rigs.get_by_role("button", name="Front").get_attribute("aria-pressed") == "true"
            assert page.get_by_role("button", name="Recorded camera").get_attribute("aria-pressed") == "false"
            # This recording has no scene state: the lab says so on the picture's notice line.
            page.wait_for_function("document.querySelector('.scene-lab-reason')?.textContent.includes('no path to follow')")
            page.locator(".scene-lab-rig-options").get_by_role("button", name="Wide").click()
            page.wait_for_function("window.__cameras.at(-1)?.rig?.shot === 'wide'")

            rigs.get_by_role("button", name="Orbit").click()
            page.wait_for_function(f"{LAST_RIG} === 'orbit'")
            for width in (850, 1280):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(150)
                fits = page.evaluate(FITS)
                assert fits["rows"] == 2 and fits["over"] <= 0.5, (width, fits)
                page.locator(".scene-lab-player").screenshot(path=str(tmp_path / f"scene-rigs-orbit-{width}.png"))
            assert page.locator(".scene-lab-rig-options").get_by_role("slider", name="Speed").count() == 1

            page.get_by_role("button", name="Free camera").click()
            page.wait_for_function("!!window.__cameras.at(-1)?.position")
            assert page.locator(".scene-lab-tag").inner_text() == "Free camera"
            assert rigs.get_by_role("button", name="Orbit").get_attribute("aria-pressed") == "false"
            assert errors == []
        finally:
            browser.close()
