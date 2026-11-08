"""The Scene viewer's page (M4 E3, lab only): its request logic in jsdom, its keyboard on the
real page, and its absence.

With the lab switched off the shell must stay exactly as it was: app.js may reach the
viewer only through the dynamic import its probe answers, and nothing the app always
loads may name the viewer's module or stylesheet. The keyboard test boots the real app
through the UI fixture with test_ui_attempt_scene.py's scratch lab and fake renderer host
(headless, no GPU). Rendered feel is the lead's visual check.
"""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

import test_ui_attempt_scene as attempt_scene
from frontend_runner import run_frontend
from source_scan import strip_comments

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"


def test_the_viewer_keeps_two_pictures_in_flight_and_says_why_when_it_cannot():
    run_frontend("sceneviewer.test.js")


# Every picture request's camera on the scene socket (null: the recorded camera).
CAMERAS = r"""
(() => {
  const sent = window.__cameras = [];
  const Native = window.WebSocket;
  window.WebSocket = class extends Native {
    send(data) {
      try { const message = JSON.parse(data); if (!message.kind) sent.push(message.camera); }
      catch { /* not a request */ }
      return super.send(data);
    }
  };
})()
"""
HAS_KEYBOARD = "document.activeElement === document.querySelector('.scene-lab')"
DRAWN = "!!document.querySelector('.scene-lab-readout')"
# tools/browser_guard.py's pointer lock stand-in is the page's requestPointerLock: checked before any
# right press, so no test here can reach the browser's own lock (which confines the real cursor).
STAND_IN = "window.__pointerLockStandIn?.armed() === true"


def camera_tab(page) -> None:
    """The tray's Camera tab (round 9: every camera control lives there; Ghosts is open first)."""
    page.get_by_role("tab", name=re.compile(r"^Camera")).click()
    page.locator("#ghost-lab-panel-camera:not([hidden])").wait_for(timeout=5000)


def more_menu(page) -> None:
    """The bar's More menu (picture quality, numbers, Tune controls, keys, the lab's recordings), open."""
    if page.get_by_role("button", name="More").get_attribute("aria-expanded") != "true":
        page.get_by_role("button", name="More").click()
    page.locator(".ghost-lab-more-pop:not([hidden])").wait_for(timeout=5000)


def _hold(page, key: str, ms: int = 400) -> list:
    """Holds `key` with no button down and answers the free cameras asked for meanwhile."""
    before = page.evaluate("window.__cameras.length")
    page.keyboard.down(key)
    page.wait_for_timeout(ms)
    page.keyboard.up(key)
    page.wait_for_timeout(150)
    return [camera for camera in page.evaluate("window.__cameras")[before:] if camera]


def _shoot(page, folder: Path, state: str) -> None:
    """The player and its hint at both supported widths, back at 1280 after."""
    for width in (850, 1280):
        page.set_viewport_size({"width": width, "height": 1000})
        page.wait_for_timeout(100)
        page.locator(".scene-lab-player").screenshot(path=str(folder / f"scene-keys-{state}-{width}.png"))


def _mouse_pick_hands_the_keyboard_back(page) -> None:
    """A Pictures choice made with the mouse: E then flies up, and never picks "Exact pixels" by its letter."""
    more_menu(page)
    pictures = page.locator(".scene-lab-quality select")
    for choice in ("raw", "jpeg"):
        pictures.click()
        pictures.select_option(choice)
    rose = _hold(page, "e")
    assert pictures.input_value() == "jpeg"
    assert rose and rose[-1]["focus"][1] > rose[0]["focus"][1], rose
    assert page.evaluate(HAS_KEYBOARD)


def _fields_keep_their_keys(page) -> None:
    """The tuning panel's number field and a contenteditable keep W; a mouse click on Tune controls does not."""
    more_menu(page)
    page.get_by_role("button", name="Tune controls").click()
    assert page.evaluate(HAS_KEYBOARD)
    page.locator(".scene-lab-tune #flightSpeed").focus()
    assert not _hold(page, "w", 200)
    page.evaluate("""(() => { const note = document.createElement('div');
      note.contentEditable = 'true'; note.id = 'key-note';
      document.querySelector('.scene-lab').append(note); note.focus(); })()""")
    page.keyboard.type("wasd")
    page.wait_for_timeout(150)
    assert page.locator("#key-note").inner_text() == "wasd"
    assert not _hold(page, "w", 200)


def test_arriving_gives_the_viewer_the_keyboard_and_w_flies_with_no_button(tmp_path, monkeypatch):
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
            ready.click()                                    # the Ghost button: the only click
            page.wait_for_function(DRAWN, timeout=20000)
            assert page.evaluate(HAS_KEYBOARD)
            _shoot(page, tmp_path, "recorded")
            flown = _hold(page, "w")
            assert flown, "W asked for no free camera"
            assert page.locator(".scene-lab-tag").inner_text() == "Free camera"
            entry, last = flown[0], flown[-1]
            travelled = [last["position"][i] - entry["position"][i] for i in range(3)]
            view = [entry["focus"][i] - entry["position"][i] for i in range(3)]
            assert sum(t * v for t, v in zip(travelled, view, strict=True)) > 0, (entry, last)   # forward, where it looks
            _shoot(page, tmp_path, "free")
            assert page.evaluate(STAND_IN)
            box = page.locator(".scene-lab canvas").bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down(button="right")
            # The keys while looking, on the picture itself (round 9: the hint paragraph under the camera is gone).
            page.wait_for_function("document.querySelector('.scene-lab-cue')?.textContent.includes('W A S D move')")
            _shoot(page, tmp_path, "looking")
            page.mouse.up(button="right")

            # From the sidebar: away and back, the open scene comes back with the keyboard.
            page.locator('.nav-item[title="Practice"]:visible').click()
            page.locator('.nav-item[title="Ghost Lab"]:visible').click()
            page.wait_for_function(DRAWN, timeout=20000)
            assert page.evaluate(HAS_KEYBOARD)
            slid = _hold(page, "d")
            assert slid and slid[-1]["position"] != slid[0]["position"]

            _mouse_pick_hands_the_keyboard_back(page)
            _fields_keep_their_keys(page)
            assert errors == []
            print("scene keys", json.dumps({"w_requests": len(flown), "w_travel": travelled,
                                             "d_requests": len(slid)}))
        finally:
            browser.close()


# The play bar's buttons as drawn, left to right (each one's name: its label for a reader, else its words),
# and whether the bar scrolls sideways.
TRANSPORT_ROW = """(() => {
  const bar = document.querySelector('.scene-lab-bar[aria-label=Playback]');
  return { overflow: bar.scrollWidth - bar.clientWidth, buttons: [...bar.querySelectorAll('button')].map((b) => {
    const r = b.getBoundingClientRect();
    return { name: b.getAttribute('aria-label') || b.textContent.trim(), top: Math.round(r.top * 100) / 100,
      height: Math.round(r.height * 100) / 100 }; }) };
})()"""
PLAYING = "[...document.querySelectorAll('.scene-lab-bar button')].some((b) => b.textContent.trim() === 'Pause')"
PLAY_BAR = ["Restart", "Back to the last action change", "Back 1", "Play", "Forward 1", "On to the next action change",
            "1x", "½x", "¼x", "Loop", "Sound"]


def _spread(buttons: list, key: str) -> float:
    return max(button[key] for button in buttons) - min(button[key] for button in buttons)


def _transport_fits_and_the_title_names_the_attempt(page, folder: Path) -> None:
    for width in (850, 1280):
        page.set_viewport_size({"width": width, "height": 1000})
        page.wait_for_timeout(100)
        row = page.evaluate(TRANSPORT_ROW)
        assert [b["name"] for b in row["buttons"]] == PLAY_BAR, row
        # One row (the slider may wrap) of one weight: within half a pixel (the Play speed's segments are
        # 34.5 px against the rest's 34, measured 2026-11-08; no one sees half a pixel).
        tops = [(b["name"], b["top"], b["height"]) for b in row["buttons"]]
        assert _spread(row["buttons"], "top") <= 0.5 and _spread(row["buttons"], "height") <= 0.5, f"{width}: {tops}"
        assert row["overflow"] <= 0, row
        # The run on screen sits in the bar; the scene's whole title (course, star, strategy, time) on hover.
        title = page.locator(".ghost-lab-watching").get_attribute("title")
        assert title.count(" · ") == 3, title
        assert "sm64scene" not in page.locator(".ghost-lab-top").inner_text()
        assert page.locator(".scene-lab .workshop-hero").count() == 0   # the old title row is gone
        page.locator(".scene-lab-player").screenshot(path=str(folder / f"scene-transport-{width}.png"))


def _a_drag_keeps_playing(page) -> None:
    before = page.evaluate("window.__cameras.length")
    box = page.locator(".scene-lab canvas").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    for step in range(1, 9):
        page.mouse.move(box["x"] + box["width"] / 2 + step * 10, box["y"] + box["height"] / 2)
        page.wait_for_timeout(30)
    page.mouse.up()
    page.wait_for_timeout(200)
    assert [camera for camera in page.evaluate("window.__cameras")[before:] if camera], "the drag moved nothing"
    assert page.evaluate(PLAYING), "a drag paused Play"


def test_the_transport_fits_one_row_the_title_names_the_attempt_and_a_drag_keeps_playing(tmp_path, monkeypatch):
    """Round 4 (2026-10-27), items 1 and 4-7 on the real page: the header names the attempt (course, star,
    strategy, time), never the scene's file; Restart and Loop sit in the transport's one row at both
    widths with Back 1 / Play / Forward 1's height; "." steps; and with Loop on, a drag on the picture
    leaves Play playing."""
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
            page.wait_for_function(f"!({PLAYING})", timeout=20000)    # the five frames played through
            _transport_fits_and_the_title_names_the_attempt(page, tmp_path)
            assert page.evaluate(HAS_KEYBOARD)
            page.get_by_role("button", name="Restart").click()               # paused: to the first frame, paused
            page.wait_for_function("document.querySelector('.scene-lab-frame').textContent.includes('Frame 1 of')")
            page.keyboard.press(".")
            page.wait_for_function("document.querySelector('.scene-lab-frame').textContent.includes('Frame 2 of')")
            page.keyboard.press(",")
            page.wait_for_function("document.querySelector('.scene-lab-frame').textContent.includes('Frame 1 of')")
            assert not page.evaluate(PLAYING)

            page.get_by_role("button", name="Loop").click()
            assert page.get_by_role("button", name="Loop").get_attribute("aria-pressed") == "true"
            page.get_by_role("button", name="Play").click()
            page.wait_for_timeout(600)                                       # past the end: still playing
            assert page.evaluate(PLAYING)
            _a_drag_keeps_playing(page)
            page.get_by_role("button", name="Pause").click()
            assert errors == []
        finally:
            browser.close()


# Each picture request's id and "hud" (absent: null), and each picture header's id, "hud" and
# "hud_reason", on the scene socket. By id: the lab answers a superseded request with no picture, so
# counts of requests and pictures need not agree.
HUDS = r"""
(() => {
  const seen = window.__huds = { asked: [], drawn: [] };
  const Native = window.WebSocket;
  window.WebSocket = class extends Native {
    constructor(...args) {
      super(...args);
      this.addEventListener("message", (event) => {
        if (typeof event.data === "string") return;
        const length = new DataView(event.data).getUint32(0, true);
        const header = JSON.parse(new TextDecoder().decode(new Uint8Array(event.data, 4, length)));
        seen.drawn.push([header.id, header.hud ?? null, header.hud_reason ?? null]);
      });
    }
    send(data) {
      try { const message = JSON.parse(data); if (!message.kind) seen.asked.push([message.id, message.hud ?? null]); }
      catch { /* not a request */ }
      return super.send(data);
    }
  };
})()
"""
# The picture for the request after the first `n`, once it has arrived.
ANSWERED = """(n) => { const asked = window.__huds.asked[n];
  return !!asked && window.__huds.drawn.some((drawn) => drawn[0] === asked[0]); }"""


def test_hud_off_reaches_the_lab_on_every_picture_and_is_remembered(tmp_path, monkeypatch):
    """Round 4, item 9 on the real page and the real lab (m4-hud's flag): HUD off asks the picture on
    screen again without the HUD and every picture after it; the lab's header answers for each; and
    the choice is still off after leaving and coming back."""
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True) as (base, _seen), sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.add_init_script(HUDS)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            ready = page.locator(f"{attempt_scene.ROWS} .attempt-scene:not([disabled])").first
            ready.wait_for(timeout=20000)
            ready.click()
            page.wait_for_function(DRAWN, timeout=20000)
            page.wait_for_function(f"!({PLAYING})", timeout=20000)
            assert {hud for _id, hud in page.evaluate("window.__huds.asked")} == {None}   # on: the lab's default
            camera_tab(page)
            hud = page.get_by_role("button", name="HUD")
            assert hud.get_attribute("aria-pressed") == "true"
            asked = page.evaluate("window.__huds.asked.length")
            hud.click()
            page.wait_for_function(ANSWERED, arg=asked, timeout=20000)
            page.keyboard.press(".")
            page.wait_for_function(ANSWERED, arg=asked + 1, timeout=20000)
            huds = page.evaluate("window.__huds")
            off = [request_id for request_id, hud in huds["asked"][asked:]]
            assert [hud for _id, hud in huds["asked"][asked:]] == [False, False], huds
            answers = [drawn for drawn in huds["drawn"] if drawn[0] in off]
            assert len(answers) == 2 and all(hud is False or reason for _id, hud, reason in answers), huds   # cut, or why not
            _shoot(page, tmp_path, "hud-off")
            page.locator('.nav-item[title="Practice"]:visible').click()
            page.locator('.nav-item[title="Ghost Lab"]:visible').click()
            page.wait_for_function(DRAWN, timeout=20000)
            camera_tab(page)
            assert page.get_by_role("button", name="HUD").get_attribute("aria-pressed") == "false"
            assert page.evaluate("window.__huds.asked.at(-1)[1]") is False
            assert errors == []
        finally:
            browser.close()


def _static_imports(source: str) -> list[str]:
    return re.findall(r"^\s*import\s[^;]*?from\s+[\"']([^\"']+)[\"']", source, re.MULTILINE)


# Watches the pointer lock -- tools/browser_guard.py's in-page stand-in, never the browser's own,
# which on Windows confines the real cursor -- every change with the element that holds it, and the
# page's own lock records on their way to the UI log.
LOCK_WATCH = r"""
(() => {
  const changes = window.__lockChanges = [], notes = window.__lockNotes = [];
  document.addEventListener("pointerlockchange", () => changes.push(document.pointerLockElement?.tagName ?? null));
  const fetched = window.fetch.bind(window);
  window.fetch = (url, options) => {
    if (String(url).endsWith("/api/uilog")) {
      try { const body = JSON.parse(options.body); if (body.kind === "pointer_lock") notes.push([body.event, body.why]); }
      catch { /* not a record */ }
    }
    return fetched(url, options);
  };
})()
"""
LOCKED = "document.pointerLockElement && document.pointerLockElement.tagName"


def test_the_pointer_lock_never_outlives_the_right_button(tmp_path, monkeypatch):
    """His report (2026-10-26): "it gets stuck in the middle of the screen, as if there's an invisible
    wall blocking its movement". Chromium on Windows keeps the cursor inside the page while a lock is
    held. The lock here is tools/browser_guard.py's stand-in (asserted before the first right press),
    granted at once and then 300 ms late, in the order Chromium was measured to use: the request's
    promise first, then pointerlockchange."""
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True) as (base, _seen), sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.add_init_script(LOCK_WATCH)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            ready = page.locator(f"{attempt_scene.ROWS} .attempt-scene:not([disabled])").first
            ready.wait_for(timeout=20000)
            ready.click()
            page.wait_for_function(DRAWN, timeout=20000)
            assert page.evaluate(STAND_IN)                   # never the browser's own lock
            box = page.locator(".scene-lab canvas").bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

            page.mouse.down(button="right")                  # a quick grant: held while the button is
            page.wait_for_function(f"({LOCKED}) === 'CANVAS'", timeout=5000)
            page.mouse.up(button="right")
            # Exited, and its pointerlockchange heard (the stand-in announces it a task later): counted
            # before it lands, it would read as a third change of the late grant below.
            page.wait_for_function(f"!({LOCKED}) && window.__lockChanges.length >= 2 "
                                   "&& window.__lockChanges.at(-1) === null", timeout=5000)

            page.evaluate("window.__pointerLockStandIn.grantMs = 300")   # a late grant: the button is up first
            changes = page.evaluate("window.__lockChanges.length")
            page.mouse.down(button="right")
            page.wait_for_timeout(50)
            page.mouse.up(button="right")
            page.wait_for_function(f"window.__lockChanges.length >= {changes + 2}", timeout=5000)
            page.wait_for_timeout(200)
            # It landed and was exited at once: the request resolves before the change is dispatched,
            # so by then the lock is already gone and both changes read null.
            late = page.evaluate("window.__lockChanges")[changes:]
            assert len(late) == 2 and late[-1] is None, late
            assert page.evaluate(LOCKED) is None
            notes = page.evaluate("window.__lockNotes")
            assert notes[:4] == [["requested", None], ["granted", None], ["ended", "button up"],
                                 ["exited", "button up"]], notes
            assert notes[4:] == [["requested", None], ["ended", "button up"], ["exited", "no look"]], notes
            assert errors == []
        finally:
            browser.close()


def test_the_shell_reaches_the_viewer_only_after_the_server_says_it_exists():
    app = strip_comments((UI / "app.js").read_text(encoding="utf-8"))
    assert not any("sceneviewer" in name for name in _static_imports(app))
    probe = app.index('fetch("/api/scene-lab"')
    lazy = app.index('import("./components/sceneviewer.js")')
    assert probe < lazy and "probe?.lab ?" in app[probe:lazy + 40]
    index = (UI / "index.html").read_text(encoding="utf-8")
    assert "scenelab.css" not in index and "sceneviewer" not in index


def test_the_guards_can_still_fail():
    assert _static_imports('import { SceneViewer } from "./components/sceneviewer.js";\n') == [
        "./components/sceneviewer.js"]
    assert _static_imports('const lazy = () => import("./components/sceneviewer.js");\n') == []
