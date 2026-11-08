"""No browser this project launches reaches the operating system's pointer lock (tools/browser_guard.py).

2026-10-26: a page test held the Scene viewer's right button, headless Chromium took the real
pointer lock, and Windows confined Griffin's mouse to the test's viewport ([0,0,850,1000], then
[0,0,1280,1000]) while he played. Two guards: every launch path installs the in-page stand-in (a
source scan over tools/, tests/ and src/), and every browser-lane test leaves the cursor as free as
it found it (tests/conftest.py, checked here against fake readings and once against the real
operating system without moving his cursor).

The same door raises Chromium's 250-entry resource-timing buffer in every page (2026-11-08), so a
probe that waits for one late request's entry never waits on an entry the browser dropped.
"""
import ctypes
import os
import re
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from source_scan import python_code

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import browser_guard  # noqa: E402

LAUNCH = re.compile(r"\b(?:chromium|firefox|webkit)\s*\.\s*(?:launch|launch_persistent_context)\s*\(|"
                    r"\bget_driver\s*\([^)]*\)\s*\.\s*launch\s*\(")
GUARDED = re.compile(r"\bbrowser_guard\s+import\s+install\b|\bfind_uilab\s*\(\s*\)")
LOCK_API = re.compile(r"requestPointerLock")
LOCK_API_OWNERS = {"tools/browser_guard.py", "tests/test_browser_guard.py"}


def unguarded(sources: dict[str, str]) -> list[str]:
    """Every way a browser could reach the real pointer lock, by file: src/ launching a browser at
    all, a tool launching one without installing the stand-in (directly, or through find_uilab),
    any file but the guard's own naming requestPointerLock (a script that could hold the real one
    before the stand-in runs), and the two doors (tests/conftest.py, find_uilab) that no longer install it."""
    found = []
    for name, source in sources.items():
        code = python_code(source) if name.endswith(".py") else source
        if name.startswith("src/") and LAUNCH.search(code):
            found.append(f"{name}: the product launches a browser")
        if name.startswith("tools/") and LAUNCH.search(code) and not GUARDED.search(code):
            found.append(f"{name}: launches a browser without browser_guard.install()")
        if name not in LOCK_API_OWNERS and LOCK_API.search(code):
            found.append(f"{name}: names requestPointerLock outside the stand-in")
    conftest = python_code(sources.get("tests/conftest.py", ""))
    configure = conftest[conftest.find("def\npytest_configure"):]
    if "install_pointer_lock_stand_in\n(" not in configure:
        found.append("tests/conftest.py: pytest_configure no longer installs the stand-in")
    door = python_code(sources.get("tools/find_uilab.py", ""))
    if "_guard_browsers\n(" not in door[door.find("def\nfind_uilab"):]:
        found.append("tools/find_uilab.py: find_uilab() no longer installs the stand-in")
    return found


def _project_sources() -> dict[str, str]:
    files = [*ROOT.glob("src/**/*.py"), *ROOT.glob("tools/**/*.py"), *ROOT.glob("tests/*.py")]
    return {path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8") for path in files
            if "node_modules" not in path.parts and ".venv" not in path.parts}


def test_every_launch_path_installs_the_pointer_lock_stand_in():
    assert unguarded(_project_sources()) == []


def test_the_scan_still_fails_on_each_way_through():
    sources = _project_sources()
    def without(name, old, new):
        assert sources[name].count(old) == 1, (name, old)
        return unguarded({**sources, name: sources[name].replace(old, new)})
    assert without("tests/conftest.py", "install_pointer_lock_stand_in()      #", "pass  #") == [
        "tests/conftest.py: pytest_configure no longer installs the stand-in"]
    assert without("tools/find_uilab.py", "    _guard_browsers()\n", "") == [
        "tools/find_uilab.py: find_uilab() no longer installs the stand-in"]
    assert without("tools/probe_clip_seek.py", "    from browser_guard import install as guard_browsers\n", "") == [
        "tools/probe_clip_seek.py: launches a browser without browser_guard.install()"]
    assert unguarded({**sources, "src/sm64_events/x.py": "def f(play):\n    play.chromium.launch(headless=True)\n"}) == [
        "src/sm64_events/x.py: the product launches a browser"]
    assert unguarded({**sources, "tests/test_x.py": 'HOOK = "HTMLCanvasElement.prototype.requestPointerLock"\n'}) == [
        "tests/test_x.py: names requestPointerLock outside the stand-in"]


def test_in_every_page_the_lock_is_emulated_and_never_the_browsers():
    """Called without a user gesture, so even a missing stand-in could not take the real lock: the
    test checks the function IS the stand-in's before calling it."""
    with sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page()
            page.set_content("<canvas width=10 height=10></canvas><iframe srcdoc='<p>x</p>'></iframe>")
            assert page.evaluate("window.__pointerLockStandIn?.armed() === true")
            frame = page.frames[1]
            assert frame.evaluate("window.__pointerLockStandIn?.armed() === true")
            seen = page.evaluate("""async () => {
              const canvas = document.querySelector('canvas'), seen = [];
              document.addEventListener('pointerlockchange', () => seen.push(document.pointerLockElement?.tagName ?? null));
              await canvas.requestPointerLock();
              const held = document.pointerLockElement?.tagName ?? null;
              await new Promise((resolve) => setTimeout(resolve, 20));
              document.exitPointerLock();
              const after = document.pointerLockElement;
              await new Promise((resolve) => setTimeout(resolve, 20));
              return { held, after, seen, requests: window.__pointerLockStandIn.requests };
            }""")
            assert seen == {"held": "CANVAS", "after": None, "seen": ["CANVAS", None], "requests": 1}
            with_context = browser.new_context().new_page()          # a context made directly is armed too
            assert with_context.evaluate("window.__pointerLockStandIn?.armed() === true")
        finally:
            browser.close()


def test_every_page_keeps_more_than_chromiums_250_resource_timings():
    """Chromium's default buffer drops entry 251 onward. A page that loads 300 resources at startup
    must still list the last one, or a probe waiting for a late request's entry waits forever."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    count = 300

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (f"<script>for (let i = 0; i < {count}; i++) fetch('/r' + i);</script>"
                    if self.path == "/" else "ok").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if self.path == "/" else "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
                paths = page.evaluate(f"""async () => {{
                  const read = () => performance.getEntriesByType('resource').map(e => new URL(e.name).pathname);
                  const deadline = performance.now() + 10000;
                  while (read().length < {count} && performance.now() < deadline)
                    await new Promise(resolve => setTimeout(resolve, 50));
                  return read();
                }}""")
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    assert len(paths) == count and f"/r{count - 1}" in paths, (len(paths), paths[-3:])


def test_a_clip_the_test_left_is_named_and_one_already_there_is_not():
    screen = (-2560, -147, 5280, 2413)
    free, walled = (screen, screen), ((0, 0, 850, 1000), screen)
    assert browser_guard.left_clipped(free, walled) == (0, 0, 850, 1000)
    assert browser_guard.left_clipped(free, free) is None
    assert browser_guard.left_clipped(walled, walled) is None            # his own clip, there before the test
    assert browser_guard.left_clipped(None, walled) is None              # off Windows


@pytest.mark.skipif(os.name != "nt", reason="the cursor clip is a Windows rectangle")
def test_the_reader_sees_a_real_clip_without_moving_his_cursor():
    """A clip one pixel short of the screen, on the side away from the cursor, for a few ms:
    the cursor cannot move, and the reader must call it confined. Skipped when something of his
    already confines the cursor; freed in a finally whatever happens."""
    import ctypes.wintypes as wintypes
    before = browser_guard.cursor_clip()
    if browser_guard.left_clipped((before[1], before[1]), before) is not None:
        pytest.skip(f"the cursor is already confined to {before[0]}: his, not ours to touch")
    user32, screen = ctypes.windll.user32, before[1]
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        left, top, right, bottom = screen
        shrunk = (left, top, right - 1, bottom) if point.x < right - 1 else (left + 1, top, right, bottom)
        rect = wintypes.RECT(*shrunk)
        started = time.perf_counter()
        try:
            user32.ClipCursor(ctypes.byref(rect))
            during = browser_guard.cursor_clip()
        finally:
            browser_guard.release_clip()
            held_ms = (time.perf_counter() - started) * 1000
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))
    after = browser_guard.cursor_clip()
    assert browser_guard.left_clipped(before, during) == shrunk
    assert after[0] == after[1] and held_ms < 100, (after, held_ms)
