"""A Library community ghost's Ghost button opens it in the Scene viewer (round 9, item 8), on the real page.

His words (2026-11-08): "We also need ghost buttons next to all library entries that have a ghost, so that
it loads directly into the scene viewer."

The UI fixture boots the real app with test_ui_attempt_scene.py's scratch lab and fake renderer host
(headless, no GPU). The Library's community routes answer one planted target and two recordings (a JP one
whose star this Trainer knows, and one whose star it does not); the lab's /stage, /ghosts and the scene
socket's "ghosts" reply are test_ui_scene_ghosts.py's stubs, and /ghost answers the recording asked for.
The page's own logic (the stage fallback, the sentence with no stage) is pinned in
tests/frontend/sceneviewer.test.js; the lab's /ghost in tests/test_scene_lab_ghosts.py.
"""
from pathlib import Path

from fastapi import APIRouter
from playwright.sync_api import sync_playwright

import test_ui_attempt_scene as attempt_scene
import test_ui_scene_ghosts as scene_ghosts
import ui_fixture

KNOWN = "c" * 64                               # a recording of the fixture's star
UNKNOWN = "d" * 64                             # one whose star this Trainer cannot place: no button
STRAT = "Single Jump"
ENTITY = f"star:{ui_fixture.FIXTURE_COURSE}:{ui_fixture.FIXTURE_STAR}"
PORTABLE = {"kind": "star", "entity_key": f"{ui_fixture.FIXTURE_COURSE}-{ui_fixture.FIXTURE_STAR}",
            "name": "Whomp's Fortress · Fall onto the Caged Island"}
WATCH = ".community-library-recording .attempt-scene"


def _recording(identifier: str, player: str, frames: int, *, known: bool) -> dict:
    return {"id": identifier, "player_id": f"{player}-id", "player_name": player, "target": PORTABLE,
            "strategy": STRAT, "clock": "igt", "game_version": "jp", "frames": frames,
            "entity_key": ENTITY if known else None,
            "local_target": {"kind": "star", "entity_key": PORTABLE["entity_key"], "strat": STRAT} if known else None,
            "can_add": known, "unavailable_reason": None if known else "This Trainer does not know this star.",
            "saved_slot": None, "is_own": False}


def _stubs(app, lab, seen) -> None:
    """The lab's stubs (test_ui_scene_ghosts.py), the Library's community pages, and /ghost."""
    scene_ghosts._stubs(app, lab, seen)
    seen.update(library=[], ghost=[])
    rows = [_recording(KNOWN, "Kz_Speedruns_Backwards_Long", 312, known=True),
            _recording(UNKNOWN, "Elsewhere", 330, known=False)]
    router = APIRouter()

    @router.get("/api/library/community")
    def summary(entity: str | None = None, rom: str | None = None, cursor: str | None = None):
        seen["library"].append(("summary", entity, rom))
        return {"available": True, "reason": None, "next_cursor": None, "complete": True, "entries": [
            {"target": PORTABLE, "clock": "igt", "game_version": "jp", "strategy": STRAT, "recordings": 2,
             "players": 2, "best_frames": 312, "can_add": True, "unavailable_reason": None}]}

    @router.get("/api/library/community/recordings")
    def recordings(target: str, clock: str, rom: str, strategy: str, cursor: str | None = None):
        seen["library"].append(("recordings", strategy, rom))
        return {"available": True, "reason": None, "next_cursor": None, "complete": True, "recordings": rows}

    @router.get("/api/scene-lab/ghost")
    def ghost(recording: str, course: int | None = None, star: int | None = None, segment: int | None = None):
        seen["ghost"].append({"recording": recording, "course": course, "star": star})
        return {"item": {"ref": f"library-{recording[:6]}", "player": rows[0]["player_name"], "time": '10"40',
                         "frames": 312, "rank": 1, "more": 0, "yours": False, "attempt": None, "strat": STRAT,
                         "version": "jp", "recorded_utc": None, "tier": None, "division": None}}

    app.include_router(router)
    for _ in router.routes:
        app.router.routes.insert(0, app.router.routes.pop())


def open_library_ghosts(page) -> None:
    """The Library's Has ghost list, then the planted target's recordings."""
    page.locator('.nav-item[title="Library"]:visible').click()
    page.locator(".library-page .library-ghost-filter input").check()
    page.locator(".community-library-group").first.click()
    page.locator(".community-library-recording").nth(1).wait_for()


def shoot_library_rows(page, folder: Path, name: str) -> None:
    for width in (850, 1200, 1600):
        page.set_viewport_size({"width": width, "height": 1000})
        page.wait_for_timeout(150)
        page.locator(".community-library-recordings").screenshot(path=str(folder / f"{name}-{width}.png"))
    page.set_viewport_size({"width": 1280, "height": 1000})


# Each recording row: its player, whether it has the Ghost button (its accessible name), and its ROM flag.
ROWS = """[...document.querySelectorAll('.community-library-recording')].map((row) => ({
  id: row.dataset.recordingId, watch: row.querySelector('.attempt-scene')?.getAttribute('aria-label') ?? null,
  flag: row.querySelector('.region-flag')?.alt ?? null }))"""


def test_a_library_ghost_opens_in_the_scene_viewer_with_that_ghost_added(tmp_path, monkeypatch):
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_stubs) as (base, seen), \
            sync_playwright() as play:
        scene_ghosts._answer_ghosts(monkeypatch, seen)
        browser, page, errors = scene_ghosts._browse(play, base)
        try:
            page.locator(".log-list-card").wait_for(timeout=20000)
            open_library_ghosts(page)
            assert page.evaluate(ROWS) == [
                {"id": KNOWN, "watch": "Watch Kz_Speedruns_Backwards_Long's ghost in the Scene viewer", "flag": "JP"},
                {"id": UNKNOWN, "watch": None, "flag": "JP"}]
            for width in (850, 1200, 1600):                     # the button sits in the row, left of Add ghost
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(100)
                inside = page.evaluate("""(() => { const row = document.querySelector('.community-library-recording');
                  const button = row.querySelector('.attempt-scene').getBoundingClientRect(), box = row.getBoundingClientRect();
                  const add = row.querySelector('.attempt-scene ~ button').getBoundingClientRect();
                  const middle = (rect) => rect.top + rect.height / 2;
                  return button.left >= box.left && button.right <= add.left && add.right <= box.right
                    && Math.abs(middle(button) - middle(add)) <= 1 && button.width > 0; })()""")
                assert inside, width
            shoot_library_rows(page, tmp_path, "library-ghost-button")
            stages_before = len(seen["stage"])

            page.locator(WATCH).first.click()
            page.wait_for_function(scene_ghosts.DRAWN, timeout=20000)
            assert seen["stage"][stages_before] == {"course": ui_fixture.FIXTURE_COURSE,
                                                    "star": ui_fixture.FIXTURE_STAR, "segment": None, "strat": STRAT}
            page.wait_for_function("() => document.querySelector('.scene-lab-view')")
            assert seen["ghost"] == [{"recording": KNOWN, "course": ui_fixture.FIXTURE_COURSE,
                                      "star": ui_fixture.FIXTURE_STAR}]
            assert seen["sets"][-1] == [f"library-{KNOWN[:6]}"]
            view = page.locator(".scene-lab-view").first
            assert view.locator(".region-flag").get_attribute("alt") == "JP"
            assert "Kz_Speedruns_Backwards_Long" in view.inner_text()
            assert errors == []
        finally:
            browser.close()


def test_with_the_lab_off_no_library_row_draws_a_ghost_button(tmp_path, monkeypatch):
    """The button exists only where the Scene viewer is mounted, like the Practice log's."""
    seen = {}
    original = ui_fixture.create_app

    def create_app(*args, **kwargs):
        app = original(*args, **kwargs)
        _stubs(app, None, seen)
        return app
    monkeypatch.setattr(ui_fixture, "create_app", create_app)
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=False) as (base, _seen), sync_playwright() as play:
        browser, page, errors = scene_ghosts._browse(play, base)
        try:
            page.locator(".log-list-card").wait_for(timeout=20000)
            open_library_ghosts(page)
            assert [row["watch"] for row in page.evaluate(ROWS)] == [None, None]
            assert [row["flag"] for row in page.evaluate(ROWS)] == ["JP", "JP"]
            assert errors == []
        finally:
            browser.close()
