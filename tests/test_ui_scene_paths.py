"""The Scene viewer's camera path block on the real page (2026-11-08): Play speed, the Action row, Path and
its keys, and the Mario switch beside HUD, at 850, 1200 and 1600 px; and none of them moves another
control as it changes state (viewer round 9's rule, measured as tests/test_ui_scene_steady.py does).

The UI fixture boots the real app with test_ui_attempt_scene.py's scratch lab (its lab folder under
tmp_path, so the camera paths file is scratch too) and fake renderer host, as test_ui_scene_presets.py
does; the open answer places every frame in level 9 area 1, whose path the test seeds with the longest
row a key can show (a recorded camera, a hold, a slow stretch). The page's logic is pinned in
tests/frontend/scenepath.test.js; this is the block's layout: one block, nothing scrolling sideways,
every key's row whole, at 850 and 1280 px. SCENE_PATH_SHOTS names a folder to keep its pictures in.
"""
import json
import os
from pathlib import Path

from fastapi import APIRouter
from playwright.sync_api import sync_playwright

import test_ui_attempt_scene as attempt_scene
from test_ui_scene_steady import _moved, _settle
from test_ui_scene_viewer import DRAWN, PLAYING

PANEL = ".scene-lab-path-panel"
CAMERA = {"focus": [0.0, 160.0, 0.0], "position": [0.0, 560.0, 1500.0], "roll": 0, "fov": 45.0}
KEYS = [{"frame": 0, "camera": None, "ease": False, "hold": 0, "speed": 1, "aim": None},
        {"frame": 2, "camera": CAMERA, "ease": False, "hold": 0, "speed": 0.5, "aim": "stage"},
        {"frame": 2, "camera": {**CAMERA, "position": [900.0, 300.0, 900.0]}, "ease": False, "hold": 2500, "speed": 1,
         "aim": "a ghost that left"},
        {"frame": 3, "camera": CAMERA, "ease": True, "hold": 0, "speed": 1, "aim": None},
        {"frame": 5, "camera": None, "ease": False, "hold": 0, "speed": 0.25, "aim": None}]
LAYOUT = """(() => {
  const panel = document.querySelector('.scene-lab-path-panel'), box = panel.getBoundingClientRect();
  const rows = [...panel.querySelectorAll('.scene-lab-path-keys li')].map((row) => {
    const r = row.getBoundingClientRect();
    return { left: Math.round(r.left), right: Math.round(r.right), height: Math.round(r.height),
      inside: [...row.children].every((part) => { const p = part.getBoundingClientRect();
        return p.left >= r.left - 1 && p.right <= r.right + 1; }) };
  });
  const mario = [...document.querySelectorAll('.scene-lab-bar button')].find((b) => b.textContent.trim() === 'Mario');
  return { left: Math.round(box.left), right: Math.round(box.right), overflow: panel.scrollWidth - panel.clientWidth,
    page: document.documentElement.scrollWidth - document.documentElement.clientWidth, rows,
    speeds: [...panel.querySelectorAll('[aria-label="Play speed"] button')].map((b) => b.textContent.trim()),
    action: panel.querySelector('.scene-lab-action-name')?.textContent,
    mario: mario ? mario.previousElementSibling.textContent.trim() : null };
})()"""


def _placed(app, lab, seen) -> None:
    """POST /api/scene-lab/open {"attempt"} ahead of the stub's: the synthetic scene, its frames in level 9 area 1."""
    router = APIRouter()

    @router.post("/api/scene-lab/open")
    async def open_scene(body: dict):
        described = await lab.open(attempt_scene.PACKAGE)
        seen["host_pids"].append(described.get("renderer_pid"))
        frames = [{**frame, "level": 9, "area": 1} for frame in described["frames"]]
        return {**described, "frames": frames, "attempt": body["attempt"]}

    app.include_router(router)
    for _ in router.routes:
        app.router.routes.insert(0, app.router.routes.pop())


def test_the_block_fits_every_width_with_each_key_row_whole_and_holds_still(tmp_path, monkeypatch):
    lab_root = tmp_path / "lab" / "lab-root"
    shots = Path(os.environ.get("SCENE_PATH_SHOTS") or tmp_path)
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_placed) as (base, _seen), \
            sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            lab_root.mkdir(parents=True)
            (lab_root / "camera-paths.json").write_text(json.dumps({"version": 1, "areas": {"9:1": KEYS}}),
                                                        encoding="utf-8")
            page.goto(base)
            ready = page.locator(f"{attempt_scene.ROWS} .attempt-scene:not([disabled])").first
            ready.wait_for(timeout=20000)
            ready.click()
            page.wait_for_function(DRAWN, timeout=20000)
            page.wait_for_function(f"!({PLAYING})", timeout=20000)
            page.wait_for_function("document.querySelectorAll('.scene-lab-path-keys li').length === 5", timeout=20000)
            page.keyboard.press("Home")
            page.locator(f"{PANEL} button", has_text="Path").first.click()
            page.wait_for_function("document.querySelector('.scene-lab-tag').textContent === 'Camera path'")
            moved = {}
            for width in (850, 1200, 1600):
                page.set_viewport_size({"width": width, "height": 1100})
                page.wait_for_timeout(200)
                layout = page.evaluate(LAYOUT)
                assert layout["overflow"] <= 0 and layout["page"] <= 0, layout
                assert len(layout["rows"]) == 5 and all(row["inside"] for row in layout["rows"]), layout
                assert all(row["left"] >= layout["left"] and row["right"] <= layout["right"] for row in layout["rows"]), layout
                assert layout["speeds"] == ["1x", "½x", "¼x"] and layout["mario"] == "HUD", layout
                assert layout["action"].startswith("Mario: "), layout
                print("path block", width, layout["rows"][0], layout["action"])
                page.locator(PANEL).screenshot(path=str(shots / f"scene-cameras-panel-{width}.png"))
                page.locator(".scene-lab-player").screenshot(path=str(shots / f"scene-cameras-player-{width}.png"))
                moved[width] = _moved(_states(page))
            assert errors == []
        finally:
            browser.close()
    assert all(not rows for rows in moved.values()), json.dumps(moved, indent=1)[:4000]


def _states(page) -> dict:
    """The player card's boxes (test_ui_scene_steady.py's measure) as each new control changes state:
    Play speed, the Mario switch, the camera path off and on. None may move another (viewer round 9)."""
    seen = {"path on": _settle(page)}
    speeds = page.locator(f'{PANEL} [aria-label="Play speed"] button')
    for index, name in ((1, "half speed"), (2, "quarter speed"), (0, "full speed")):
        speeds.nth(index).click()
        seen[name] = _settle(page)
    mario = page.get_by_role("button", name="Mario", exact=True)
    mario.click()
    seen["Mario off"] = _settle(page)
    mario.click()
    page.get_by_role("button", name="Recorded camera").click()
    seen["path off"] = _settle(page)
    page.locator(f"{PANEL} button", has_text="Path").first.click()
    page.wait_for_function("document.querySelector('.scene-lab-tag').textContent === 'Camera path'")
    seen["path on again"] = _settle(page)
    return seen
