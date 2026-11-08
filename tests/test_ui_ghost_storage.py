"""Settings -> Ghost files on the real page (ui/components/ghoststorage.js), and the Practice
log's ghost buttons asking again after a purge (ui/components/attemptscene.js `revision`).

His words (2026-11-08): "Maybe we need an extra button in the settings menu for purging all
non-PB ghost files (and another for purging all local ghost files)? This wouldn't clear the
server, but would free up space for the user."

The UI fixture boots the real app offline. GET /api/ghost/storage and POST
/api/ghost/storage/purge are stubbed here with their agreed shape, ahead of the server's own
routes: the server half is not what these tests measure. The purge stub broadcasts
`ghost_files_changed` through the fixture's real Broadcaster, as the server does. For the
Practice log, GET /api/scene-lab names a lab so the shell turns its ghost buttons on (the
viewer's code loads, but nothing opens a scene, so no renderer starts), and GET
/api/scene-lab/attempt-scenes answers every row ready until a purge drops one.
"""
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import APIRouter, HTTPException
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ui_fixture  # noqa: E402
from sm64_events.core.events import Event  # noqa: E402

MB = 1024 * 1024
DRAWER = ".settings-drawer"
SECTION = f"{DRAWER} .ghost-storage"
USAGE = f"{SECTION} .ghost-storage-usage"
RESULT = f"{SECTION} .ghost-storage-result"
KEEP = f'{SECTION} button[data-purge="except_pbs"]'
ALL = f'{SECTION} button[data-purge="all"]'
DIALOG = ".modal:has(.ghost-storage-ask)"
CARD = ".log-card.log-card-active"
GHOST_BUTTONS = f"{CARD} .attempt-table .attempt-scene"
ALL_GHOST_BUTTONS = ".attempt-table .attempt-scene"
UNAVAILABLE = "Ghost file sizes are not available right now."
KEPT_NOTE = "This frees space on this computer. Ghosts you shared stay online."


def usage(total=(52, 241), except_pbs=(37, 152), every=(50, 230), in_use=(1, 4)):
    """A GET /api/ghost/storage answer, each pair (ghosts, megabytes)."""
    def pair(ghosts, megabytes):
        return {"ghosts": ghosts, "bytes": int(megabytes * MB)}
    return {"available": True, "total": pair(*total), "except_pbs": pair(*except_pbs),
            "all": pair(*every), "in_use": pair(*in_use)}


# What each purge answers, by keep_pbs: (deleted, megabytes freed, skipped, the usage after it).
AFTER = {
    True: (37, 150, 2, usage(total=(15, 89), except_pbs=(0, 0), every=(13, 78), in_use=(3, 11))),
    False: (13, 78, 0, usage(total=(3, 11), except_pbs=(0, 0), every=(0, 0), in_use=(3, 11))),
}
STATE: dict = {}


def reset(**changes) -> None:
    STATE.clear()
    STATE.update({"usage": usage(), "missing": False, "refuse": None, "after": AFTER, "lab": False,
                  "posts": [], "gets": 0, "scene_asks": [], "drop": set(), "dropped": set()})
    STATE.update(changes)


def _stub(app, broadcaster) -> None:
    router = APIRouter()

    @router.get("/api/ghost/storage")
    def storage():
        STATE["gets"] += 1
        if STATE["missing"]:                      # a server from before this endpoint
            raise HTTPException(404, "Not Found")
        return STATE["usage"]

    @router.post("/api/ghost/storage/purge")
    async def purge(body: dict):
        STATE["posts"].append(body)
        if STATE["refuse"]:
            raise HTTPException(*STATE["refuse"])
        deleted, megabytes, skipped, after = STATE["after"][bool(body["keep_pbs"])]
        STATE["usage"] = after
        STATE["dropped"] = set(STATE["drop"])
        payload = {"deleted": deleted, "bytes": int(megabytes * MB), "skipped": skipped}
        await broadcaster.publish(Event(type="ghost_files_changed", frame=0,
                                        timestamp_utc=datetime.now(timezone.utc), payload=payload))
        return {**payload, "usage": after}

    @router.get("/api/scene-lab")
    def lab_probe():
        return {"lab": {"name": "stub"}} if STATE["lab"] else {"lab": None}

    @router.get("/api/scene-lab/attempt-scenes")
    def attempt_scenes(ids: str):
        asked = [int(part) for part in ids.split(",")]
        STATE["scene_asks"].append(asked)
        return {"scenes": {str(attempt): {"state": "ready", "seconds": 1.0}
                           for attempt in asked if attempt not in STATE["dropped"]}}

    app.include_router(router)
    for _ in router.routes:                       # ahead of the server's own routes
        app.router.routes.insert(0, app.router.routes.pop())


@contextmanager
def served():
    """The real app on the UI fixture with this file's stubs; yields its base URL."""
    reset()
    original = ui_fixture.create_app

    def create_app(*args, **kwargs):
        app = original(*args, **kwargs)
        _stub(app, args[1])
        return app

    ui_fixture.create_app = create_app
    try:
        with ui_fixture.serve_ui() as base:
            yield base
    finally:
        ui_fixture.create_app = original


@pytest.fixture(scope="module")
def base():
    with served() as url:
        yield url


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as play:
        launched = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            yield launched
        finally:
            launched.close()


def open_page(browser, base, width=1200, height=1000):
    page = browser.new_page(viewport={"width": width, "height": height})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # The Daily Star announces itself once a day over whatever is open; today (the browser's
    # local date, on this machine) is already seen.
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    page.request.post(base + "/api/daily-star/seen", data={"date": today})
    page.goto(base + "/")
    return page, errors


def open_settings(page):
    page.click('.settings-link[aria-label="Settings"]')
    page.wait_for_selector(DRAWER)


def wait_text(page, selector, text, timeout=10000):
    page.wait_for_function(
        "([selector, text]) => document.querySelector(selector)?.textContent.trim() === text",
        arg=[selector, text], timeout=timeout)


def dialog_text(page):
    """The dialog's question, then its detail line, as one string."""
    page.wait_for_selector(DIALOG)
    return page.evaluate(f"""[...document.querySelectorAll({json.dumps(DIALOG + ' .ghost-storage-ask p')})]
        .map((p) => p.textContent.trim()).join(' ')""")


def wait_server(page, check, what, turns=100):
    for _ in range(turns):
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"timed out waiting for {what}: {json.dumps(STATE, default=str)[:600]}")


@pytest.mark.parametrize("width", [850, 1200])
def test_the_section_says_the_space_and_each_dialog_counts_what_it_frees(browser, base, width):
    reset(usage=usage(in_use=(0, 0)))
    page, errors = open_page(browser, base, width)
    open_settings(page)
    wait_text(page, USAGE, "Ghost files use 241 MB on this computer.")
    # Beside Community, as asked: the section right after it in the drawer.
    assert page.evaluate(f"""document.querySelector({json.dumps(SECTION)})
        .previousElementSibling.querySelector('h3').textContent.trim()""") == "Community"
    assert page.inner_text(f"{SECTION} h3").strip() == "Ghost files"
    assert KEPT_NOTE in page.inner_text(SECTION)
    assert [page.inner_text(KEEP).strip(), page.inner_text(ALL).strip()] == ["Delete ghosts, keep PBs", "Delete all ghosts"]
    fits = page.evaluate(f"""[...document.querySelectorAll({json.dumps(SECTION + ' button')})]
        .map((b) => b.scrollWidth <= b.clientWidth && b.getBoundingClientRect().right
             <= document.querySelector({json.dumps(DRAWER)}).getBoundingClientRect().right)""")
    assert fits == [True, True]

    page.click(KEEP)
    assert dialog_text(page) == "Delete 37 ghosts and free 152 MB? Your PB, saved and imported ghosts stay."
    assert page.inner_text(f"{DIALOG} .modal-heading h2").strip() == "Delete ghosts, keep PBs"
    assert page.locator(f"{DIALOG} .modal-heading p").count() == 0          # no subtitle
    # The question reads in the title's ink, the detail line muted; a dialog mounted inside the
    # section took `.settings-section p`'s small muted text for both.
    ink = page.evaluate(f"""(() => {{ const at = (s) => getComputedStyle(document.querySelector({json.dumps(DIALOG)} + ' ' + s));
        return [at('.modal-heading h2').color, at('.ghost-storage-question').color, at('.ghost-storage-detail').color]; }})()""")
    assert ink[1] == ink[0] and ink[2] != ink[0], ink
    assert page.evaluate("document.activeElement.textContent.trim()") == "Cancel"
    page.click(f"{DIALOG} .modal-actions button:has-text('Cancel')")
    page.wait_for_selector(DIALOG, state="detached")

    page.click(ALL)
    assert dialog_text(page) == "Delete all 50 ghosts and free 230 MB? This deletes your PB ghosts too."
    page.keyboard.press("Escape")                  # closes the dialog, never the drawer under it
    page.wait_for_selector(DIALOG, state="detached")
    assert page.locator(DRAWER).count() == 1
    page.wait_for_timeout(300)
    assert STATE["posts"] == []                     # Cancel and Escape send nothing
    assert page.locator(RESULT).count() == 0
    assert errors == []
    page.close()


def test_delete_sends_keep_pbs_and_says_what_it_freed(browser, base):
    reset()
    page, errors = open_page(browser, base)
    open_settings(page)
    wait_text(page, USAGE, "Ghost files use 241 MB on this computer.")
    page.click(KEEP)
    assert dialog_text(page) == ("Delete 37 ghosts and free 152 MB? Your PB, saved and imported ghosts stay. "
                                 "1 ghost is in use right now, so it stays.")
    gets = STATE["gets"]
    page.click(f"{DIALOG} .ghost-storage-delete")
    wait_text(page, RESULT, "Deleted 37 ghosts and freed 150 MB. 2 ghosts are in use right now, so they stay.")
    page.wait_for_selector(DIALOG, state="detached")
    assert STATE["posts"] == [{"keep_pbs": True}]
    wait_text(page, USAGE, "Ghost files use 89 MB on this computer.")
    # The broadcast asks again too (a purge from anywhere refreshes an open drawer).
    wait_server(page, lambda: STATE["gets"] > gets, "the usage asked again after ghost_files_changed")
    assert page.is_disabled(KEEP)
    assert page.get_attribute(KEEP, "title") == ("There is nothing to delete. Only your PB, saved and imported "
                                                 "ghosts are here. 3 ghosts are in use right now, so they stay.")

    page.click(ALL)
    assert dialog_text(page) == ("Delete all 13 ghosts and free 78 MB? This deletes your PB ghosts too. "
                                 "3 ghosts are in use right now, so they stay.")
    page.click(f"{DIALOG} .ghost-storage-delete")
    wait_text(page, RESULT, "Deleted 13 ghosts and freed 78 MB.")
    assert STATE["posts"] == [{"keep_pbs": True}, {"keep_pbs": False}]
    wait_text(page, USAGE, "Ghost files use 11 MB on this computer.")
    assert page.is_disabled(KEEP) and page.is_disabled(ALL)
    assert page.get_attribute(ALL, "title") == "There is nothing to delete. 3 ghosts are in use right now, so they stay."
    assert errors == []
    page.close()


def test_a_button_with_nothing_to_delete_is_off_and_says_why(browser, base):
    reset(usage=usage(total=(4, 9.54), except_pbs=(0, 0), every=(3, 8.2), in_use=(1, 1.3)))
    page, errors = open_page(browser, base)
    open_settings(page)
    wait_text(page, USAGE, "Ghost files use 9.5 MB on this computer.")
    assert page.is_disabled(KEEP) and not page.is_disabled(ALL)
    assert page.get_attribute(KEEP, "title") == ("There is nothing to delete. Only your PB, saved and imported "
                                                 "ghosts are here. 1 ghost is in use right now, so it stays.")
    assert page.get_attribute(ALL, "title") == "Frees 8.2 MB on this computer."
    page.click(ALL)
    assert dialog_text(page) == ("Delete all 3 ghosts and free 8.2 MB? This deletes your PB ghosts too. "
                                 "1 ghost is in use right now, so it stays.")
    page.keyboard.press("Escape")
    page.wait_for_selector(DIALOG, state="detached")

    STATE["usage"] = usage(total=(0, 0), except_pbs=(0, 0), every=(0, 0), in_use=(0, 0))
    page.click('.settings-head button[aria-label="Close settings"]')
    open_settings(page)                              # the drawer asks again each time it opens
    wait_text(page, USAGE, "There are no ghost files on this computer.")
    assert page.is_disabled(KEEP) and page.is_disabled(ALL)
    assert {page.get_attribute(KEEP, "title"), page.get_attribute(ALL, "title")} == {"There are no ghost files to delete."}
    assert STATE["posts"] == []
    assert errors == []
    page.close()


def test_an_old_server_or_an_unavailable_store_leaves_the_buttons_off_with_a_sentence(browser, base):
    reset(missing=True)
    page, errors = open_page(browser, base, width=850)
    open_settings(page)
    wait_text(page, USAGE, UNAVAILABLE)
    assert page.is_disabled(KEEP) and page.is_disabled(ALL)
    assert page.get_attribute(KEEP, "title") == UNAVAILABLE
    reason = "Ghost files are not ready while the trainer starts."
    STATE.update(missing=False, usage={"available": False, "reason": reason})
    page.click('.settings-head button[aria-label="Close settings"]')
    open_settings(page)
    wait_text(page, USAGE, reason)
    assert page.is_disabled(KEEP) and page.is_disabled(ALL)
    assert page.get_attribute(ALL, "title") == reason
    assert page.locator(DIALOG).count() == 0
    assert errors == []
    page.close()


@pytest.mark.parametrize("status, detail", [(409, "Ghost files are already being deleted."),
                                            (503, "Ghosts are not running right now.")])
def test_a_refused_purge_says_why_in_the_dialog(browser, base, status, detail):
    reset(refuse=(status, detail))
    page, errors = open_page(browser, base)
    open_settings(page)
    wait_text(page, USAGE, "Ghost files use 241 MB on this computer.")
    page.click(KEEP)
    page.wait_for_selector(DIALOG)
    page.click(f"{DIALOG} .ghost-storage-delete")
    wait_text(page, f"{DIALOG} [role=alert]", detail)
    assert page.is_enabled(f"{DIALOG} .ghost-storage-delete")       # he can try again or cancel
    assert STATE["posts"] == [{"keep_pbs": True}]
    page.click(f"{DIALOG} .modal-actions button:has-text('Cancel')")
    page.wait_for_selector(DIALOG, state="detached")
    assert page.inner_text(USAGE).strip() == "Ghost files use 241 MB on this computer."
    assert page.locator(RESULT).count() == 0
    assert errors == []
    page.close()


def test_sizes_read_plainly_and_the_newest_change_is_found(browser, base):
    reset()
    page, errors = open_page(browser, base)
    page.wait_for_selector('.settings-link[aria-label="Settings"]')
    shown = page.evaluate("""import('/ui/components/ghoststorage.js').then((m) => ({
      sizes: [0, 0.4, 1, 4.54, 9.96, 152.4, 2400].map((mb) => m.ghostMegabytes(mb * 1048576)),
      newest: m.newestGhostFilesChange([{ type: 'ghost_changed' }, { type: 'ghost_files_changed', seq: 9 },
                                        { type: 'ghost_files_changed', seq: 4 }]).seq,
      none: m.newestGhostFilesChange(undefined),
    }))""")
    assert shown["sizes"] == ["less than 1 MB", "less than 1 MB", "1.0 MB", "4.5 MB", "10 MB", "152 MB", "2,400 MB"]
    assert shown["newest"] == 9 and shown["none"] is None
    assert errors == []
    page.close()


def test_a_purge_takes_the_ghost_button_off_a_deleted_scene(browser, base):
    reset(lab=True)
    page, errors = open_page(browser, base)
    page.locator(GHOST_BUTTONS).first.wait_for(timeout=20000)
    page.wait_for_timeout(1500)
    settled = [list(ask) for ask in STATE["scene_asks"]]   # one ask per table the log shows
    page.wait_for_timeout(1000)
    assert STATE["scene_asks"] == settled                 # nothing cutting: no asking on renders
    before = page.locator(ALL_GHOST_BUTTONS).count()
    assert before == sum(len(ask) for ask in settled) and page.locator(GHOST_BUTTONS).count() >= 2
    STATE["drop"] = {settled[0][0]}                       # the purge deletes this attempt's scene

    open_settings(page)
    page.locator(KEEP + ":enabled").wait_for()
    page.click(KEEP)
    page.click(f"{DIALOG} .ghost-storage-delete")
    page.wait_for_selector(RESULT)
    page.click('.settings-head button[aria-label="Close settings"]')
    page.wait_for_function(f"document.querySelectorAll({json.dumps(ALL_GHOST_BUTTONS)}).length === {before - 1}",
                           timeout=10000)
    again = STATE["scene_asks"][len(settled):]
    assert sorted(again) == sorted(settled)               # the same rows, each table asked once more
    page.wait_for_timeout(1500)
    assert len(STATE["scene_asks"]) == 2 * len(settled)
    assert errors == []
    page.close()
