"""The Scene viewer's camera presets on the real page (M4 round 6, item 2).

His words: "I set up a specific camera angle that I really like, and assign it to a camera slot
position (numbers 1-9 on the keyboard). I should be able to name the camera, and be able to swap to
the different camera views by pressing each button. I should be able to easily reassign cameras to
different slots by dragging and dropping."

The UI fixture boots the real app with test_ui_attempt_scene.py's scratch lab (its lab folder under
tmp_path, so the presets file is scratch too) and fake renderer host; the lab's own preset routes
write the file. The synthetic recording names no level, so the open answer here places its first
three frames in level 9 area 1 and the rest in area 2. The page's logic is pinned in
tests/frontend/scenepresets.test.js; this is the same strip in a real browser: a real drag and drop,
the keyboard, the file on disk, a reload, and its layout at both supported widths.
"""
import json
from pathlib import Path

from fastapi import APIRouter
from playwright.sync_api import sync_playwright

import test_ui_attempt_scene as attempt_scene
from frontend_runner import run_frontend
from test_ui_scene_viewer import CAMERAS, DRAWN, HAS_KEYBOARD, PLAYING, _hold

STRIP = ".scene-lab-cameras"
# The strip as drawn: whether it scrolls sideways, and each slot's box.
LAYOUT = """(() => {
  const strip = document.querySelector('.scene-lab-cameras'), list = strip.querySelector('.scene-lab-slots');
  const box = list.getBoundingClientRect();
  return { overflow: Math.max(strip.scrollWidth - strip.clientWidth, list.scrollWidth - list.clientWidth),
    list: [Math.round(box.left), Math.round(box.right)], slots: [...list.children].map((slot) => {
      const r = slot.getBoundingClientRect();
      return { left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), width: Math.round(r.width) }; }) };
})()"""


def test_the_strip_in_jsdom():
    run_frontend("scenepresets.test.js")


def _placed(app, lab, seen) -> None:
    """POST /api/scene-lab/open {"attempt"} ahead of the stub's: the synthetic scene, its frames placed."""
    router = APIRouter()

    @router.post("/api/scene-lab/open")
    async def open_scene(body: dict):
        described = await lab.open(attempt_scene.PACKAGE)
        seen["host_pids"].append(described.get("renderer_pid"))
        frames = [{**frame, "level": 9, "area": 1 if index < 3 else 2} for index, frame in enumerate(described["frames"])]
        return {**described, "frames": frames, "attempt": body["attempt"]}

    app.include_router(router)
    for _ in router.routes:
        app.router.routes.insert(0, app.router.routes.pop())


def _open(page, base) -> None:
    page.goto(base)
    ready = page.locator(f"{attempt_scene.ROWS} .attempt-scene:not([disabled])").first
    ready.wait_for(timeout=20000)
    ready.click()                                    # the Ghost button: opens and plays
    page.wait_for_function(DRAWN, timeout=20000)
    page.wait_for_function(f"!({PLAYING})", timeout=20000)
    page.keyboard.press("Home")                      # area 1
    page.wait_for_function("document.querySelector('.scene-lab-cameras .scene-lab-hint').textContent.startsWith('Keys 1-9')",
                           timeout=20000)


def _saved(lab_root: Path) -> dict:
    return json.loads((lab_root / "camera-presets.json").read_text(encoding="utf-8"))["areas"]


LONG = "Behind the cannon looking up at the floating isle"[:48]     # the longest name a camera keeps
SEEDED = [{"slot": 7, "name": LONG, "camera": {"focus": [0, 0, 0], "position": [0, 400, 900], "roll": 0, "fov": 45}},
          {"slot": 9, "name": "Tower", "camera": {"focus": [10, 0, 0], "position": [10, 900, 900], "roll": 0, "fov": 60}}]


def _names(lab_root: Path) -> list:
    return [(entry["slot"], entry["name"]) for entry in _saved(lab_root)["9:1"]]


def _near(a, b) -> bool:
    return all(abs(x - y) < 0.01 for x, y in zip(a, b, strict=True))


def _fits(page, folder: Path) -> None:
    """One row of nine equal slots inside the strip at both supported widths, nothing scrolling sideways."""
    for width in (850, 1280):
        page.set_viewport_size({"width": width, "height": 1000})
        page.wait_for_timeout(150)
        layout = page.evaluate(LAYOUT)
        slots = layout["slots"]
        assert len(slots) == 9 and len({slot["top"] for slot in slots}) == 1, layout
        assert max(slot["width"] for slot in slots) - min(slot["width"] for slot in slots) <= 1, layout
        assert layout["overflow"] <= 0 and slots[0]["left"] >= layout["list"][0] and slots[-1]["right"] <= layout["list"][1], layout
        long_name = page.locator('.scene-lab-slot[data-slot="7"] .scene-lab-slot-name')
        assert long_name.inner_text() == LONG and long_name.evaluate("(name) => name.scrollWidth > name.clientWidth")
        print("camera slots", width, slots[0]["width"])
        page.locator(".scene-lab-player").screenshot(path=str(folder / f"pre-player-{width}.png"))
        page.locator(STRIP).screenshot(path=str(folder / f"pre-strip-{width}.png"))
    page.set_viewport_size({"width": 1280, "height": 1000})


def _slot_says(page, slot: int, name: str, timeout: int = 30000) -> None:
    page.wait_for_function(f"document.querySelector('.scene-lab-slot[data-slot=\"{slot}\"] .scene-lab-slot-name')"
                           f"?.textContent === {json.dumps(name)}", timeout=timeout)


def _type_name(page, name: str) -> None:
    """The name field has the typing (the click that opened it did not take it back); Enter keeps the name
    and gives the keyboard back to the viewer."""
    page.wait_for_function("document.activeElement.classList.contains('scene-lab-slot-name-field')", timeout=2000)
    page.keyboard.press("Control+a")
    page.keyboard.type(name)
    page.keyboard.press("Enter")


def _save_and_name(page, lab_root: Path, here: dict) -> None:
    """Slot 2 saves the free camera on screen and asks for its name at once; a digit typed there never cuts."""
    page.get_by_role("button", name="Save current view in slot 2").click()
    sent = page.evaluate("window.__cameras.length")
    _type_name(page, "Wide 7")
    _slot_says(page, 2, "Wide 7")
    assert page.evaluate("window.__cameras.length") == sent
    assert page.evaluate(HAS_KEYBOARD)
    page.wait_for_timeout(300)
    assert _names(lab_root) == [(2, "Wide 7"), (7, LONG), (9, "Tower")]
    stored = _saved(lab_root)["9:1"][0]
    assert _near(stored["camera"]["focus"], here["focus"]) and _near(stored["camera"]["position"], here["position"])
    assert page.locator('.scene-lab-slot[data-slot="2"] .scene-lab-slot-cut').get_attribute("aria-pressed") == "true"


def _key_cuts_back(page, here: dict) -> None:
    """Away to the recorded camera, then key 2 cuts back to the saved camera as a free camera."""
    page.keyboard.press("r")
    page.wait_for_function("document.querySelector('.scene-lab-tag').textContent === 'Recorded camera'")
    before = page.evaluate("window.__cameras.length")
    page.keyboard.press("2")
    page.wait_for_function(f"window.__cameras.length > {before}")
    cut = page.evaluate("window.__cameras")[-1]
    assert _near(cut["focus"], here["focus"]) and _near(cut["position"], here["position"]), (cut, here)
    assert page.locator(".scene-lab-tag").inner_text() == "Free camera"


def _drag_then_rename(page, lab_root: Path) -> None:
    """A real drag and drop moves slot 2 to slot 5; Rename with the mouse names it again."""
    page.locator('.scene-lab-slot[data-slot="2"]').drag_to(page.locator('.scene-lab-slot[data-slot="5"]'))
    _slot_says(page, 5, "Wide 7")
    page.wait_for_timeout(300)
    assert _names(lab_root) == [(5, "Wide 7"), (7, LONG), (9, "Tower")]
    page.get_by_role("button", name="Rename Wide 7").click()
    _type_name(page, "Bridge")
    _slot_says(page, 5, "Bridge")
    assert page.evaluate(HAS_KEYBOARD)
    page.wait_for_timeout(300)
    assert _names(lab_root) == [(5, "Bridge"), (7, LONG), (9, "Tower")]


def test_save_name_cut_drag_and_reload_on_the_real_page(tmp_path, monkeypatch):
    lab_root = tmp_path / "lab" / "lab-root"
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_placed) as (base, _seen), \
            sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.add_init_script(CAMERAS)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            lab_root.mkdir(parents=True)
            (lab_root / "camera-presets.json").write_text(json.dumps({"version": 1, "areas": {"9:1": SEEDED}}),
                                                          encoding="utf-8")
            _open(page, base)
            _slot_says(page, 9, "Tower")
            flown = _hold(page, "w")                          # free camera, moved forward
            assert flown, "W asked for no free camera"
            _save_and_name(page, lab_root, flown[-1])
            _key_cuts_back(page, flown[-1])
            _fits(page, tmp_path)
            _drag_then_rename(page, lab_root)
            page.keyboard.press("End")                        # area 2 has its own set, empty: the key says so
            page.wait_for_function("document.querySelectorAll('.scene-lab-cameras .scene-lab-slot-save').length === 9")
            page.keyboard.press("5")
            page.wait_for_function("document.querySelector('.scene-lab-reason')?.textContent === "
                                   "'No camera saved in slot 5 here yet.'")
            _open(page, base)                                 # the page loaded again reads the same file
            _slot_says(page, 5, "Bridge", timeout=20000)
            assert errors == []
        finally:
            browser.close()


