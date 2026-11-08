"""Star save-state sets on the save-state strip, on the real page (round 9, item 12). His words: "for every single
star, we should be able to have custom save states that override the default stage-level save states (if there's a
slot set) ... when the user grabs a star or selects a star ... load their star-specific save states".

The fixture's Ghost Lab with a stand-in GET /api/scene-lab/slot-sets (the lead's frozen shape; the server lives on
claude/star-sets) and its move and remove routes. At each supported width: a ★ marks the star's own saves and the
course's stay plain; the course and star sit at the right end of the line under the tiles without making it a
second line; every tile keeps one size; a saved tile's menu button shows on hover, opens upward over the player,
inside the window, on top; a choice moves the slot and the ★ goes. A lab without slot sets keeps today's strip: the
other strip tests (test_ui_scene_moment.py) run against the real /slots route.
"""
import copy
import os
from pathlib import Path

import pytest
from fastapi import APIRouter, Query
from playwright.sync_api import sync_playwright

import test_ui_attempt_scene as attempt_scene
from frontend_runner import run_frontend
from test_ui_scene_presets import _open, _placed
from test_ui_scene_viewer import CAMERAS

ROW = ".ghost-lab-strip"
WHOMP = {"course_id": 2, "course": "Whomp's Fortress", "star_id": 1, "star": "Shoot into the Wild Blue",
         "entity_key": "star:2:1"}


def _kept(identifier: str, place: str, build: str = "us") -> dict:
    return {"id": identifier, "name": f"{place} {identifier}", "level": 24, "area": 1, "place": place, "build": build,
            "source": "lab", "first_seen_utc": "2026-11-08T12:00:00Z"}


def _slot(slot: int, **fields) -> dict:
    return {"slot": slot, "set": None, "state": None, "translated": None, "under": None, "picture": None,
            "loaded_last": False, **fields}


# A flat sky-blue JPEG: a written slot's picture.
PICTURE = ("data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP///////////////////////////////////////////"
           "///////////////////////////////////////////2wBDAf///////////////////////////////////////////////////"
           "///////////////////////////////////wAARCAAQABADASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAA"
           "AAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AKpgA"
           "//Z")
ANSWER = {"context": WHOMP, "game": {"build": "us"}, "slots": [
    _slot(1, set="star", state=_kept("s1", "Whomp's Fortress"), translated=False, under=_kept("c1", "Whomp's Fortress"),
          loaded_last=True),
    _slot(2, set="course", state=_kept("c2", "Whomp's Fortress"), translated=False),
    _slot(3, set="star", state=_kept("s3", "Whomp's Fortress", "jp"), translated=True, picture=PICTURE),
    _slot(4, state=_kept("g4", "Bob-omb Battlefield"), translated=False),
    _slot(5), _slot(6), _slot(7),
    _slot(8, set="course", state=_kept("c8", "Whomp's Fortress"), translated=False),
    _slot(9)]}


def test_the_strip_in_jsdom():
    run_frontend("scenestrip.test.js")


def _rig(state: dict):
    def rig(app, lab, seen):
        _placed(app, lab, seen)
        router = APIRouter()

        @router.get("/api/scene-lab/slot-sets")
        def read():
            return state["answer"]

        @router.post("/api/scene-lab/slot-sets/{slot}/move")
        def move(slot: int, body: dict):
            state["asked"].append(("move", slot, body))
            entry = state["answer"]["slots"][slot - 1]
            entry["set"], entry["under"] = body["to"], None
            return state["answer"]

        @router.delete("/api/scene-lab/slot-sets/{slot}")
        def remove(slot: int, which: str = Query(alias="set")):
            state["asked"].append(("remove", slot, which))
            state["answer"]["slots"][slot - 1]["set"] = None
            return state["answer"]

        app.include_router(router)
        for _ in router.routes:
            app.router.routes.insert(0, app.router.routes.pop())
    return rig


def _tile(slot: int) -> str:
    return f"{ROW} .ghost-lab-slot[data-slot='{slot}']"


def _box(page, selector: str) -> dict:
    return page.evaluate("(s) => { const r = document.querySelector(s).getBoundingClientRect();"
                         " return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height}; }",
                         selector)


def _keep_folder():
    """A person inspecting the strip names a folder; the screenshots are not otherwise kept."""
    named = os.environ.get("SM64_SLOT_SETS_SHOTS")
    return Path(named) if named else None


def _marks(page) -> None:
    """The star's own saves wear a star, the course's stay plain; hover names the save under one; the line names
    the course and star."""
    stars = [page.locator(f"{_tile(slot)} .ghost-lab-slot-star").count() for slot in range(1, 10)]
    assert stars == [1, 0, 1, 0, 0, 0, 0, 0, 0]
    assert page.locator(f"{_tile(3)} .ghost-lab-slot-crossed").count() == 1
    assert page.locator(f"{ROW} .ghost-lab-slot-more").count() == 5      # one per saved tile
    assert "Under it is the course save from Whomp's Fortress." in page.locator(f"{_tile(1)} > button").get_attribute("title")
    assert page.locator(f"{ROW} .ghost-lab-strip-context").text_content() == "Whomp's Fortress \u00b7 \u2605 Shoot into the Wild Blue"


def _layout(page) -> None:
    """One line under the tiles with the course and star at its right end; every tile one size; a place's name on
    one line between the number and the flags, which sit in the corners."""
    foot, line, where, strip = (_box(page, f"{ROW} {part}") for part in
                                (".ghost-lab-strip-foot", ".ghost-lab-strip-line", ".ghost-lab-strip-context", ""))
    assert foot["height"] <= line["height"] + 1, (foot, line)
    assert where["right"] <= strip["right"] + 0.5 and where["left"] > line["left"], (where, strip)
    tiles = [_box(page, f"{_tile(slot)} > button") for slot in range(1, 10)]
    widths, heights = [one["width"] for one in tiles], {one["height"] for one in tiles}
    assert max(widths) - min(widths) <= 0.5 and len(heights) == 1, (widths, heights)   # marks change no size
    for slot in (1, 2, 4, 8):
        top, place, marks = (_box(page, f"{_tile(slot)} {part}") for part in
                             (".ghost-lab-slot-top", ".ghost-lab-slot-place", ".ghost-lab-slot-marks"))
        assert top["bottom"] <= place["top"] + 0.5 and place["bottom"] <= marks["top"] + 0.5, (slot, top, place, marks)
        assert place["height"] < 20, (slot, place)
        corner = _box(page, f"{_tile(slot)} > button")["left"]
        assert top["left"] - corner <= 6 and marks["left"] - corner <= 6, (slot, top, marks, corner)


def _menus(page, state, width, keep) -> None:
    """The menu button shows on hover; the menu opens upward from the tile's own edge, inside the window, on top; a
    choice moves the slot (the star goes) or takes it out of its set."""
    assert page.evaluate("(s) => getComputedStyle(document.querySelector(s)).opacity", f"{_tile(1)} .ghost-lab-slot-more") == "0"
    page.hover(f"{_tile(1)} > button")
    page.wait_for_function(f"getComputedStyle(document.querySelector(\"{_tile(1)} .ghost-lab-slot-more\")).opacity === '1'")
    if keep:
        page.locator(".ghost-lab-player").screenshot(path=str(keep / f"ghost-lab-starsets-{width}.png"))
    page.locator(f"{_tile(1)} .ghost-lab-slot-more").click()
    menu = page.locator(f"{_tile(1)} [role=menu]")
    menu.wait_for(timeout=5000)
    box, tile = _box(page, f"{_tile(1)} [role=menu]"), _box(page, f"{_tile(1)} > button")
    assert box["bottom"] <= tile["top"] and abs(box["left"] - tile["left"]) <= 0.5 and box["right"] <= width, (box, tile)
    assert page.evaluate("(s) => { const m = document.querySelector(s), r = m.getBoundingClientRect();"
                         " return m.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)); }",
                         f"{_tile(1)} [role=menu]")                   # on top of the player, not under it
    assert menu.get_by_role("menuitemradio", name="Only this star").get_attribute("aria-checked") == "true"
    if keep:
        page.locator(".ghost-lab-player").screenshot(path=str(keep / f"ghost-lab-starsets-menu-{width}.png"))
    menu.get_by_role("menuitemradio", name="All stars in this course").click()
    page.wait_for_function(f"!document.querySelector(\"{_tile(1)} .ghost-lab-slot-star\")", timeout=5000)
    assert state["asked"] == [("move", 1, {"to": "course"})]
    page.hover(f"{_tile(8)} > button")                         # the far end: its menu opens toward the middle
    page.locator(f"{_tile(8)} .ghost-lab-slot-more").click()
    page.locator(f"{_tile(8)} [role=menu]").wait_for(timeout=5000)
    box, tile = _box(page, f"{_tile(8)} [role=menu]"), _box(page, f"{_tile(8)}")
    assert abs(box["right"] - tile["right"]) <= 0.5 and box["left"] >= 0, (box, tile)
    page.locator(f"{_tile(8)} [role=menu]").get_by_role("menuitem", name="Remove from set").click()
    page.wait_for_function(f"!document.querySelector(\"{_tile(8)}\").classList.contains('is-set-course')", timeout=5000)
    assert state["asked"][-1] == ("remove", 8, "course")


@pytest.mark.parametrize("width", [850, 1280, 1600])
def test_star_sets_on_the_strip_at_each_width(width, tmp_path, monkeypatch):
    state = {"answer": copy.deepcopy(ANSWER), "asked": []}
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_rig(state)) as (base, _seen), \
            sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": width, "height": 1100})
            page.add_init_script(CAMERAS)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            _open(page, base)
            page.keyboard.press("ArrowRight")
            page.locator(f"{_tile(1)} .ghost-lab-slot-star").wait_for(timeout=20000)
            _marks(page)
            _layout(page)
            _menus(page, state, width, _keep_folder())
            assert errors == []
        finally:
            browser.close()
