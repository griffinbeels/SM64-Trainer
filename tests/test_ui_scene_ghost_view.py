"""A ghost's view on the real page against the lab's REAL routes (M4 round 5, item 5): his words,
"we need one button per active ghost in the scene to follow their recorded POV."

The world is test_ui_scene_ghosts_end_to_end.py's: the real app through the UI fixture, the scene lab
on a scratch lab, the ghost catalog over real native clips (each record's camera stands at (0, 100,
500) looking at the origin, tests/ghost_native_fixture.py), fake_viewer_host.py's host and evaluator
(headless, no GPU). The page alone is driven: add Sully's ghost, press his view, step, start Free
camera from it, go back to the recorded camera (round 9: the eyes in the Ghosts tab show them, the views are
in the Camera tab's Camera row, and each ghost's own view button sits in "In the scene"). The pictures of each state are kept beside the test
at 850 and 1280 px for the visual check.
"""
import re

from playwright.sync_api import sync_playwright

import test_ui_scene_ghosts_end_to_end as world

# Every picture request's camera and every picture header's view, in order.
SOCKET = r"""
(() => {
  const seen = window.__views = { asked: [], headers: [] };
  const Native = window.WebSocket;
  window.WebSocket = class extends Native {
    constructor(...args) {
      super(...args);
      this.addEventListener("message", (event) => {
        if (typeof event.data === "string") return;
        const length = new DataView(event.data).getUint32(0, true);
        const header = JSON.parse(new TextDecoder().decode(new Uint8Array(event.data, 4, length)));
        seen.headers.push({ id: header.id, frame: header.frame, camera: header.camera, view: header.view ?? null,
                            reason: header.reason });
      });
    }
    send(data) {
      try { const message = JSON.parse(data); if (!message.kind) seen.asked.push({ id: message.id, frame: message.frame,
        camera: message.camera }); } catch { /* not a request */ }
      return super.send(data);
    }
  };
})()
"""
RECORDED_CAMERA = {"focus": [0.0, 0.0, 0.0], "position": [0.0, 100.0, 500.0], "roll": 0, "fov": 45.0}


def _answer_after(page, asked_before: int) -> dict:
    """The header answering the first picture the page asked for after `asked_before` requests."""
    page.wait_for_function("""(before) => {
      const ids = window.__views.asked.slice(before).map((m) => m.id);
      return window.__views.headers.some((h) => ids.includes(h.id));
    }""", arg=asked_before, timeout=20000)
    return page.evaluate("""(before) => {
      const ids = window.__views.asked.slice(before).map((m) => m.id);
      return { asked: window.__views.asked.slice(before), header: window.__views.headers.find((h) => ids.includes(h.id)) };
    }""", asked_before)


def _two_ghosts_in_the_stage(page) -> None:
    """Pick Shoot into the Wild Blue (the stage opens), then show Sully's ghost (Top) and one of his (Yours)
    beside it, each with its row's eye; then open the Camera tab."""
    page.locator('.nav-item[title="Ghost Lab"]:visible').click()
    page.locator(".scene-lab-target").click()
    page.locator("[role=dialog] button", has_text="Whomp's Fortress").first.click()
    page.locator("[role=dialog] .starcell", has_text="Shoot into the Wild Blue").first.click()
    page.wait_for_function("!!document.querySelector('.scene-lab-readout')", timeout=30000)
    for rows, who, segment in ((world.TOP_ROWS, world.SULLY, "Top"), (world.YOURS_ROWS, world.YOURS, "Yours")):
        page.locator(".ghost-lab-segments").get_by_role("button", name=segment, exact=True).click()
        page.locator(rows, has_text=who).locator(":scope > button.ghost-lab-eye").click()
        page.locator(f"{rows}.is-shown", has_text=who).wait_for(timeout=20000)
    page.get_by_role("tab", name=re.compile(r"^Camera")).click()
    page.locator("#ghost-lab-panel-camera:not([hidden])").wait_for(timeout=5000)


def _sullys_view(page, bar, folder) -> str:
    """His view button: the picture is his camera, the button and his row's look pressed, the corner
    names him. Returns his ref."""
    assert bar.locator(".scene-lab-view").count() == 2, bar.inner_text()
    sully = bar.get_by_role("button", name=f"{world.SULLY}'s view")
    asked = page.evaluate("window.__views.asked.length")
    sully.click()
    seen = _answer_after(page, asked)
    ref = seen["asked"][0]["camera"]["ghost"]
    assert seen["header"]["view"] == {"ghost": ref, "camera": RECORDED_CAMERA}, seen
    assert seen["header"]["camera"] == "free" and seen["header"]["reason"] is None, seen
    assert sully.get_attribute("aria-pressed") == "true"
    assert bar.get_by_role("button", name="Recorded camera").get_attribute("aria-pressed") == "false"
    assert page.locator(".scene-lab-tag").inner_text() == f"{world.SULLY}'s view"
    row = page.locator(".ghost-lab-scene-list li", has_text=world.SULLY)          # In the scene, Ghosts tab
    assert row.locator(".scene-lab-ghost-view").get_attribute("aria-pressed") == "true"
    for width in (1280, 850):
        page.set_viewport_size({"width": width, "height": 1000})
        page.locator(".scene-lab").screenshot(path=str(folder / f"ghost-view-{width}.png"))
    return ref


def _a_step_follows_and_free_camera_starts_there(page, bar, ref) -> None:
    asked = page.evaluate("window.__views.asked.length")
    page.keyboard.press(".")
    stepped = _answer_after(page, asked)
    assert stepped["asked"][0]["camera"] == {"ghost": ref} and stepped["header"]["frame"] == 1, stepped
    assert stepped["header"]["view"]["camera"] == RECORDED_CAMERA, stepped
    asked = page.evaluate("window.__views.asked.length")
    bar.get_by_role("button", name="Free camera").click()             # no jump: the ghost's camera
    free = _answer_after(page, asked)["asked"][0]["camera"]
    for key in ("focus", "position"):
        assert all(abs(a - b) < 1e-6 for a, b in zip(free[key], RECORDED_CAMERA[key], strict=True)), free
    assert (free["roll"], free["fov"]) == (0, 45.0), free
    assert page.locator(".scene-lab-tag").inner_text() == "Free camera"
    assert bar.get_by_role("button", name=f"{world.SULLY}'s view").get_attribute("aria-pressed") == "false"


def test_a_ghosts_view_button_shows_its_camera_follows_a_step_and_free_camera_starts_there(tmp_path, monkeypatch):
    with world._served(tmp_path, monkeypatch) as (base, lab), sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.add_init_script(SOCKET)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            _two_ghosts_in_the_stage(page)
            bar = page.locator(".scene-lab-bar[aria-label='Camera']")
            ref = _sullys_view(page, bar, tmp_path)
            _a_step_follows_and_free_camera_starts_there(page, bar, ref)
            asked = page.evaluate("window.__views.asked.length")
            bar.get_by_role("button", name="Recorded camera").click()
            back = _answer_after(page, asked)
            assert back["asked"][0]["camera"] is None and back["header"]["view"] is None, back
            assert page.locator(".scene-lab-tag").inner_text() == "Recorded camera"
            assert bar.get_by_role("button", name="Your view").get_attribute("aria-pressed") == "false"
            assert lab.session is not None and errors == []
        finally:
            browser.close()
