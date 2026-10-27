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
            box = page.locator(".scene-lab canvas").bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down(button="right")
            page.wait_for_function("document.querySelector('.scene-lab-hint').textContent.includes('Move the mouse')")
            _shoot(page, tmp_path, "looking")
            page.mouse.up(button="right")

            # From the sidebar: away and back, the open scene comes back with the keyboard.
            page.locator('.nav-item[title="Practice"]:visible').click()
            page.locator('.nav-item[title="Scene viewer"]:visible').click()
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


def _static_imports(source: str) -> list[str]:
    return re.findall(r"^\s*import\s[^;]*?from\s+[\"']([^\"']+)[\"']", source, re.MULTILINE)


# Watches the pointer lock: every change with the element that holds it, and the page's own lock
# records on their way to the UI log. window.__lateGrantMs > 0 hands each request to Chromium that
# long after the page made it (still inside the click's activation), as a busy browser can.
LOCK_WATCH = r"""
(() => {
  const changes = window.__lockChanges = [], notes = window.__lockNotes = [];
  window.__lateGrantMs = 0;
  document.addEventListener("pointerlockchange", () => changes.push(document.pointerLockElement?.tagName ?? null));
  const fetched = window.fetch.bind(window);
  window.fetch = (url, options) => {
    if (String(url).endsWith("/api/uilog")) {
      try { const body = JSON.parse(options.body); if (body.kind === "pointer_lock") notes.push([body.event, body.why]); }
      catch { /* not a record */ }
    }
    return fetched(url, options);
  };
  const native = HTMLCanvasElement.prototype.requestPointerLock;
  HTMLCanvasElement.prototype.requestPointerLock = function (...args) {
    if (!window.__lateGrantMs) return native.apply(this, args);
    return new Promise((resolve, reject) => setTimeout(() => {
      try { Promise.resolve(native.apply(this, args)).then(resolve, reject); } catch (error) { reject(error); }
    }, window.__lateGrantMs));
  };
})()
"""
LOCKED = "document.pointerLockElement && document.pointerLockElement.tagName"


def test_the_pointer_lock_never_outlives_the_right_button(tmp_path, monkeypatch):
    """His report (2026-10-26): "it gets stuck in the middle of the screen, as if there's an invisible
    wall blocking its movement". Chromium on Windows keeps the cursor inside the page while a lock is
    held; headless Chromium grants the viewer's lock, so both a quick and a late grant run for real."""
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
            box = page.locator(".scene-lab canvas").bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

            page.mouse.down(button="right")                  # a quick grant: held while the button is
            page.wait_for_function(f"({LOCKED}) === 'CANVAS'", timeout=5000)
            page.mouse.up(button="right")
            page.wait_for_function(f"!({LOCKED})", timeout=5000)

            page.evaluate("window.__lateGrantMs = 300")      # a late grant: the button is up before it lands
            changes = page.evaluate("window.__lockChanges.length")
            page.mouse.down(button="right")
            page.wait_for_timeout(50)
            page.mouse.up(button="right")
            page.wait_for_function(f"window.__lockChanges.length >= {changes + 2}", timeout=5000)
            page.wait_for_timeout(200)
            # It landed and was exited at once: Chromium resolves the request before it dispatches the
            # change, so by then the lock is already gone and both changes read null.
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
