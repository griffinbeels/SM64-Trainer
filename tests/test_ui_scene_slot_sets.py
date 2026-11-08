"""Star save-state sets on the save-state strip, on the real page and the lab's real routes (round 9, item 12). His
words: "for every single star, we should be able to have custom save states that override the default stage-level
save states (if there's a slot set) ... when the user grabs a star or selects a star ... load their star-specific
save states".

The fixture's Ghost Lab with a real star sets keeper (slotlibrary/keeper.py) over the game-memory stand-in
(test_slot_library.FakeGame, standing in Bob-omb Battlefield): a scratch Slot Library holds the course default's
slots 1, 2 and 8 and the star's own slots 1 and 3 (slot 1 over the course's). GET /api/scene-lab/slot-sets, its
pictures and its move and remove routes are the lab's own. At each supported width: a ★ marks the star's own saves
and the course's stay plain; hover names the course save under one; an empty slot is its number; the course and
star sit at the right end of the line under the tiles without making it a second line; every tile keeps one size; a
saved tile's menu button shows on hover, opens upward over the player, inside the window, on top; a choice moves the
slot and the ★ goes, or takes it out of its set.
"""
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

import test_slot_library as libfakes
import test_ui_attempt_scene as attempt_scene
from frontend_runner import run_frontend
from sm64_events.slotlibrary.coursesets import CourseSets
from sm64_events.slotlibrary.keeper import SetKeeper
from sm64_events.slotlibrary.store import SlotLibrary
from test_ui_scene_presets import _open, _placed
from test_ui_scene_viewer import CAMERAS

ROW = ".ghost-lab-strip"
BOB, STAR = 1, 3                                  # the stand-in game stands in Bob-omb Battlefield (course 1)


def test_the_strip_in_jsdom():
    run_frontend("scenestrip.test.js")


def _rig(state: dict):
    """The placed scene (test_ui_scene_presets.py) and a real keeper in the lab, as follow_spawns makes it."""
    def rig(app, lab, seen):
        _placed(app, lab, seen)
        folder = Path(lab.config_path).parent / "slot-library"
        library, game = SlotLibrary(folder), libfakes.FakeGame()
        sets = CourseSets(folder / "course-sets.json")
        kept = {name: libfakes.keep(library, game, libfakes.make_slot(fill))
                for name, fill in (("course 1", 0xA1), ("course 2", 0xA2), ("course 8", 0xA8),
                                   ("star 1", 0xB1), ("star 3", 0xB3))}
        for slot in (1, 2, 8):
            sets.put(library, BOB, None, slot, kept[f"course {slot}"].id)
        for slot in (1, 3):
            sets.put(library, BOB, STAR, slot, kept[f"star {slot}"].id)
        library.put_picture(kept["star 3"].id, _frame_picture())
        keeper = SetKeeper(library, sets, None, target=lambda: ("star", BOB, STAR))
        keeper.watch(game)
        keeper.apply(game)
        lab.refitter = SimpleNamespace(keeper=keeper, poke=lambda *args: None, start=lambda: None, stop=lambda: None)
        state.update(keeper=keeper, sets=sets, kept=kept)
    return rig


def _frame_picture() -> bytes:
    """A frame picture as a Ghost Lab save keeps one: a small JPEG, here colored stripes like the fake renderer's."""
    picture = Image.new("RGB", (160, 120))
    for x in range(160):
        shade = (x * 37) % 256
        for y in range(120):
            picture.putpixel((x, y), (shade, 255 - shade, (x * 11) % 256))
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=80)
    return out.getvalue()


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


def _marks(page, state) -> None:
    """The star's own saves wear a star, the course's stay plain, an empty slot is its number; hover names the save
    under one; the line names the course and star; slot 3's picture comes from the lab's picture route."""
    stars = [page.locator(f"{_tile(slot)} .ghost-lab-slot-star").count() for slot in range(1, 10)]
    assert stars == [1, 0, 1, 0, 0, 0, 0, 0, 0]
    assert [page.locator(_tile(slot)).get_attribute("class").split()[1] for slot in range(1, 10)] == [
        "is-saved", "is-saved", "is-saved", "is-empty", "is-empty", "is-empty", "is-empty", "is-saved", "is-empty"]
    assert page.locator(f"{ROW} .ghost-lab-slot-more").count() == 4      # one per saved tile
    assert "Under it is the course save from Bob-omb Battlefield." in page.locator(f"{_tile(1)} > button").get_attribute("title")
    star = state["keeper"].view()["context"]["star"]
    assert page.locator(f"{ROW} .ghost-lab-strip-context").text_content() == f"Bob-omb Battlefield · ★ {star}"
    picture = page.locator(f"{_tile(3)} img.ghost-lab-slot-picture")
    assert picture.get_attribute("src") == f"/api/scene-lab/slot-sets/pictures/{state['kept']['star 3'].id}"
    assert page.evaluate("(s) => document.querySelector(s).naturalWidth", f"{_tile(3)} img.ghost-lab-slot-picture") > 0


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
    for slot in (1, 2, 8):
        top, place, marks = (_box(page, f"{_tile(slot)} {part}") for part in
                             (".ghost-lab-slot-top", ".ghost-lab-slot-place", ".ghost-lab-slot-marks"))
        assert top["bottom"] <= place["top"] + 0.5 and place["bottom"] <= marks["top"] + 0.5, (slot, top, place, marks)
        assert place["height"] < 20, (slot, place)
        corner = _box(page, f"{_tile(slot)} > button")["left"]
        assert top["left"] - corner <= 6 and marks["left"] - corner <= 6, (slot, top, marks, corner)


def _menus(page, state, width, keep) -> None:
    """The menu button shows on hover; the menu opens upward from the tile's own edge, inside the window, on top; a
    choice moves the slot (the star goes) or takes it out of its set, in the lab's own sets."""
    assert page.evaluate("(s) => getComputedStyle(document.querySelector(s)).opacity", f"{_tile(1)} .ghost-lab-slot-more") == "0"
    page.hover(f"{_tile(1)} > button")
    page.wait_for_function(f"getComputedStyle(document.querySelector(\"{_tile(1)} .ghost-lab-slot-more\")).opacity === '1'")
    if keep:
        page.locator(".ghost-lab-player").screenshot(path=str(keep / f"ghost-lab-starsets-real-{width}.png"))
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
        page.locator(".ghost-lab-player").screenshot(path=str(keep / f"ghost-lab-starsets-real-menu-{width}.png"))
    menu.get_by_role("menuitemradio", name="All stars in this course").click()
    page.wait_for_function(f"!document.querySelector(\"{_tile(1)} .ghost-lab-slot-star\")", timeout=5000)
    assert state["sets"].slots(BOB, STAR).get(1) is None
    assert state["sets"].slots(BOB, None)[1] == state["kept"]["star 1"].id      # the star's save is the course's now
    page.hover(f"{_tile(8)} > button")                         # the far end: its menu opens toward the middle
    page.locator(f"{_tile(8)} .ghost-lab-slot-more").click()
    page.locator(f"{_tile(8)} [role=menu]").wait_for(timeout=5000)
    box, tile = _box(page, f"{_tile(8)} [role=menu]"), _box(page, f"{_tile(8)}")
    assert abs(box["right"] - tile["right"]) <= 0.5 and box["left"] >= 0, (box, tile)
    page.locator(f"{_tile(8)} [role=menu]").get_by_role("menuitem", name="Remove from set").click()
    page.wait_for_function(f"!document.querySelector(\"{_tile(8)}\").classList.contains('is-set-course')", timeout=5000)
    assert 8 not in state["sets"].slots(BOB, None)


@pytest.mark.parametrize("width", [850, 1280, 1600])
def test_star_sets_on_the_strip_at_each_width(width, tmp_path, monkeypatch):
    state = {}
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_rig(state)) as (base, _seen), \
            sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": width, "height": 1100})
            page.add_init_script(CAMERAS)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            _open(page, base)
            page.keyboard.press("ArrowRight")
            page.locator(f"{_tile(1)} .ghost-lab-slot-star").wait_for(timeout=20000)
            _marks(page, state)
            _layout(page)
            _menus(page, state, width, _keep_folder())
            assert errors == []                                  # nothing logged: no 404 for a route the page asks
        finally:
            browser.close()
