"""The save-state strip on the real page (round 9, item 5): the fixture's Ghost Lab, the lab's real routes, a
stand-in builder and a fake game (test_handoff_try.FakeGame). Nothing attaches to a process (the strip's star sets
are test_ui_scene_slot_sets.py's). A tile or the key N saves the frame on screen in slot N; the line under the tiles says what
happened. The strip at both supported widths, with the longest sentence the lab says.
"""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

import test_ui_attempt_scene as attempt_scene
from frontend_runner import run_frontend
from test_ui_scene_presets import _open, _placed
from test_ui_scene_viewer import CAMERAS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import handoff_try as tool  # noqa: E402
import test_handoff_try as fakes  # noqa: E402
from sm64_events.slotlibrary.store import SlotLibrary  # noqa: E402
import test_scene_moments as moments_test  # noqa: E402

ROW = ".ghost-lab-strip"
STATUS = f"{ROW} .ghost-lab-strip-line"
TILES = f"{ROW} .ghost-lab-slot button"


def _tile(page, slot: int):
    return page.locator(f"{ROW} .ghost-lab-slot[data-slot='{slot}'] button")


def test_the_row_in_jsdom():
    run_frontend("scenemoment.test.js")


def _rigged(game, tmp_path, answers):
    def rig(app, lab, seen):
        _placed(app, lab, seen)
        scene = lab.config_path.parent / "packages" / attempt_scene.PACKAGE

        async def source(body):
            answers["frames"].append(body.frame)
            return scene, f"Attempt {body.attempt}"

        def builder(_config, _path, frame, register=lambda child: None):
            register(None)
            if answers["refuse"]:
                return answers["refuse"], None
            return ({**moments_test.ANSWER, "frame": frame, "worked_out": answers["older"],
                     "fingerprint": tool.routine_fingerprint(game.read, fakes.slots.US)}, moments_test.slot_bytes())
        lab.moments._source = source
        lab.moments.builder = builder
        lab.moments.game = lambda: game
        lab.moments.library = SlotLibrary(tmp_path / "slot-library")
        lab.moments.backups = tmp_path / "slot-backups"
    return rig


def _says(page, words: str) -> None:
    page.wait_for_function("words => document.querySelector(%r)?.textContent.includes(words)" % STATUS, arg=words, timeout=20000)


def _ready(page) -> None:
    """Every tile pressable: the frame on screen can be saved."""
    page.wait_for_function(f"document.querySelectorAll({TILES!r}).length === 9"
                           f" && [...document.querySelectorAll({TILES!r})].every((b) => !b.disabled)", timeout=20000)


def _walk_the_row(page, base, game, answers, folder) -> None:
    _open(page, base)                         # the first frame
    _says(page, "This is the first frame.")
    assert page.locator(f"{TILES}:not([disabled])").count() == 0
    page.locator(ROW).screenshot(path=str(folder / "first-frame.png"))
    page.keyboard.press("ArrowRight")
    _ready(page)
    assert page.locator(TILES).count() == 9
    page.locator(ROW).screenshot(path=str(folder / "ready.png"))
    _tile(page, 9).click()
    _says(page, "Frame 2 is in slot 9.")
    address = fakes.slots.slot_address(8) - 0x80000000
    assert bytes(game.ram[address:address + fakes.slots.SLOT_BYTES]) == moments_test.slot_bytes()
    assert answers["frames"] == [1]
    fakes.set_mario(game)                      # a practice reset moved Mario: the write moves him in the slot too
    page.keyboard.press("ArrowRight")
    page.wait_for_function(f"!document.querySelector({STATUS!r}).textContent.includes('is in slot 9')")   # it left with its frame
    _ready(page)
    page.keyboard.press("6")                   # the key saves as the tile does
    _says(page, "is in slot 6.")
    occupied = fakes.slots.slot_address(5) + fakes.slots.TRAILER_MARIO - 0x80000000
    assert bytes(game.ram[occupied:occupied + 4]) == game.read(fakes.slots.MARIO_OBJECT, 4)
    fakes.set_area_pointer(game)               # re-entered another way: the live check refuses the same build
    _tile(page, 4).click()
    _says(page, "Leave the level, enter it again")
    assert page.locator(f"{STATUS}.is-bad").count() == 1
    page.locator(ROW).screenshot(path=str(folder / "refused.png"))
    page.keyboard.press("ArrowRight")
    _ready(page)
    fakes.set_level(game)
    _tile(page, 2).click()
    _says(page, "This save state is for Bob-omb Battlefield.")
    page.locator(ROW).screenshot(path=str(folder / "other-level.png"))
    answers["refuse"] = {"refused": "never-reached", "reason": "Usamune cannot save on this frame. The game is not "
                         "running Mario here, such as during a level start, a fade or a door."}
    page.keyboard.press("ArrowRight")
    _ready(page)
    _tile(page, 1).click()
    _says(page, "Usamune cannot save on this frame.")
    overflow = page.evaluate(f"(() => {{ const row = document.querySelector({ROW!r});"
                             "return row.scrollWidth - row.clientWidth; })()")
    assert overflow <= 0
    boxes = page.evaluate(f"[...document.querySelectorAll({TILES!r})].map((b) => {{"
                          "const r = b.getBoundingClientRect(); return [Math.round(r.top), Math.round(r.width)]; })")
    assert len(boxes) == 9 and len({top for top, _ in boxes}) == 1 and min(w for _, w in boxes) >= 40, boxes
    assert page.evaluate(f"(() => {{ const p = document.querySelector({STATUS!r}); return p.scrollWidth <= p.clientWidth"
                         " || getComputedStyle(p).textOverflow === 'ellipsis'; })()"), "the line keeps to one line"
    page.screenshot(path=str(folder / "page.png"), full_page=True)


@pytest.mark.parametrize("width", [850, 1280])
def test_press_refusal_and_layout_at_each_width(width, tmp_path, monkeypatch):
    game = fakes.FakeGame()
    answers = {"frames": [], "refuse": None, "older": False}
    folder = Path(tmp_path) / "shots"
    folder.mkdir()
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_rigged(game, tmp_path, answers)) as (base, _seen), \
            sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": width, "height": 1100})
            page.add_init_script(CAMERAS)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            _walk_the_row(page, base, game, answers, folder)
            assert errors == []
        finally:
            browser.close()
    target = _keep_folder()
    if target:
        for shot in folder.iterdir():
            shot.replace(target / f"{width}-{shot.name}")


def _keep_folder():
    """A person inspecting the row names a folder; the screenshots are not otherwise kept."""
    import os
    named = os.environ.get("SM64_MOMENT_SHOTS")
    return Path(named) if named else None




CHANGED = "The Trainer changed it from the US version to your JP game."
CROSSED = (f"{CHANGED} It left out a music trigger, which the JP game does not have. Your JP game has a yellow coin "
           "there instead.")


def _hint_fits(page) -> bool:
    return page.evaluate("(() => { const p = document.querySelector(%r); return p.scrollWidth <= p.clientWidth; })()"
                         % STATUS)


@pytest.mark.parametrize("width", [850, 1280])
def test_a_save_state_written_into_the_other_versions_game_shows_its_flag_and_says_so(width, tmp_path, monkeypatch):
    """Under the flag of the version the save state came from: the usual words fit the row's one line, and the
    longest (an object left out) keep all of it on hover."""
    game = fakes.FakeGame()
    answers = {"frames": [], "refuse": None, "older": False}
    rig = _rigged(game, tmp_path, answers)
    said = {"words": CHANGED}

    def crossed(app, lab, seen):
        rig(app, lab, seen)
        lab.moments._write_blocking = lambda entry, slot, *_set: {
            "written": True, "slot": slot, "frame": 7, "backup": "b.bin", "kept": None, "build": "us",
            "translated": {"from": "us", "to": "jp", "words": said["words"], "removed": 0}}
    folder = Path(tmp_path) / "shots"
    folder.mkdir()
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=crossed) as (base, _seen), \
            sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": width, "height": 1100})
            page.add_init_script(CAMERAS)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            _open(page, base)
            page.keyboard.press("ArrowRight")
            _ready(page)
            _tile(page, 3).click()
            _says(page, CHANGED)
            flag = page.locator(f"{STATUS} img.region-flag")
            assert flag.get_attribute("alt") == "US" and flag.is_visible()
            box, text = flag.bounding_box(), page.locator(STATUS).bounding_box()
            assert box["height"] >= 10 and text["x"] <= box["x"], "the flag starts the line it belongs to"
            assert _hint_fits(page), "the usual words fit the one line"
            page.locator(ROW).screenshot(path=str(folder / "translated.png"))
            said["words"] = CROSSED
            _tile(page, 4).click()
            _says(page, "Your JP game has a yellow coin there instead.")
            assert page.locator(STATUS).get_attribute("title") == f"Frame 2 is in slot 4. {CROSSED}"
            overflow = page.evaluate(f"(() => {{ const row = document.querySelector({ROW!r});"
                                     "return row.scrollWidth - row.clientWidth; })()")
            assert overflow <= 0
            page.locator(ROW).screenshot(path=str(folder / "translated-long.png"))
            assert errors == []
        finally:
            browser.close()
    target = _keep_folder()
    if target:
        for shot in folder.iterdir():
            shot.replace(target / f"{width}-{shot.name}")
