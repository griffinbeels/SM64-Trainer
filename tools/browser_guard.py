"""No browser this project launches may reach the operating system's pointer lock.

Chromium on Windows confines the REAL cursor to its window while a page holds a
pointer lock (ClipCursor), headless windows included, at the screen's origin. On
2026-10-26 a page test held the Scene viewer's right button and walled Griffin's
mouse into [0,0,850,1000], then [0,0,1280,1000], while he played: "it gets stuck
in the middle of the screen, as if there's an invisible wall blocking its
movement" (the lead's GetClipCursor watcher named the rectangles).

`install()` patches Playwright where every browser context is made (one
process-wide patch, under both its sync and async APIs and under uilab's driver),
so each page runs POINTER_LOCK_STAND_IN before any script of its own: the lock is
emulated in the page -- pointerlockchange fires, pointerLockElement reads back --
and the browser's own is never asked for. `window.__pointerLockStandIn.armed()`
says it is in place (check it before pressing anything that locks), and `.grantMs`
delays a grant, for tests of a late one. tests/conftest.py installs it for every
test and `tools/find_uilab.py` for every tool that goes through uilab; a tool that
imports Playwright itself calls it. `tests/test_browser_guard.py` holds both
guards: the source scan of every launch path, and the cursor clip read after
every browser-lane test (`cursor_clip`, `left_clipped`).

The same door gives every page RESOURCE_TIMING_BUFFER, also before any script of
its own. Chromium keeps 250 resource-timing entries per page and silently drops
the rest; the app loads about 260 at startup once a database has attempts
(measured 2026-11-08). A probe waiting for one request's entry then waits
forever although the server answered it: three special-stage tests did, for a
late `/api/marelo`. Raising the buffer after load cannot recover a dropped entry.
"""
from __future__ import annotations

import ctypes
import os

POINTER_LOCK_STAND_IN = r"""
(() => {
  if (window.__pointerLockStandIn) return;
  const state = { grantMs: 0, held: null, requests: 0, exits: 0 };
  const announce = () => document.dispatchEvent(new Event("pointerlockchange"));
  // As Chromium does: the request's promise resolves first, then pointerlockchange is dispatched.
  state.request = function requestPointerLock() {
    const target = this;
    state.requests += 1;
    return new Promise((resolve) => setTimeout(() => {
      state.held = target;
      resolve();
      setTimeout(announce, 0);
    }, state.grantMs));
  };
  state.exit = function exitPointerLock() {
    if (!state.held) return;
    state.held = null;
    state.exits += 1;
    setTimeout(announce, 0);
  };
  Element.prototype.requestPointerLock = state.request;
  Document.prototype.exitPointerLock = state.exit;
  // What a test checks before it presses anything that asks for the lock.
  state.armed = () => Element.prototype.requestPointerLock === state.request
    && Document.prototype.exitPointerLock === state.exit;
  Object.defineProperty(Document.prototype, "pointerLockElement", {
    configurable: true, get: () => (state.held && state.held.isConnected ? state.held : null) });
  Object.defineProperty(window, "__pointerLockStandIn", { value: state });
})()
"""

RESOURCE_TIMING_BUFFER = "performance.setResourceTimingBufferSize(100000)"


def install() -> None:
    """Every browser context Playwright makes in this process runs both scripts first. Idempotent
    (the mark is on Playwright's own class, so this module imported under two names patches once);
    nothing to do where Playwright is not installed."""
    try:
        from playwright._impl._browser import Browser
        from playwright._impl._browser_type import BrowserType
    except ImportError:
        return
    if getattr(Browser, "_pointer_lock_stand_in", False):
        return

    async def armed(context):
        for script in (POINTER_LOCK_STAND_IN, RESOURCE_TIMING_BUFFER):
            await context.add_init_script(script=script)
            for page in context.pages:          # the page a persistent launch opens with
                await page.evaluate(script)
        return context

    new_context = Browser.new_context

    async def new_context_armed(self, *args, **kwargs):     # Browser.new_page makes its context here too
        return await armed(await new_context(self, *args, **kwargs))
    Browser.new_context = new_context_armed

    persistent = BrowserType.launch_persistent_context

    async def persistent_armed(self, *args, **kwargs):
        return await armed(await persistent(self, *args, **kwargs))
    BrowserType.launch_persistent_context = persistent_armed
    # connect and connect_over_cdp are left alone on purpose: they attach to a browser someone
    # else started (his own window, profile_browser.py --cdp-url), whose pages are his.
    Browser._pointer_lock_stand_in = True


def retire_own_navigation_aborts() -> None:
    """uilab's page counts every failed request as a problem, and a page's own navigation (its `goto`) cancels the
    GETs still in flight with net::ERR_ABORTED: the header's GET /api/setup poll (the setup gate, 2026-11-09),
    /api/pause, /api/replay/status, each the page simply asks again after loading. Those failed three browser
    lanes of the beta.38 full run. Patched once here, on uilab's PlaywrightPage: a GET in flight when the test
    navigates and then aborted is retired; every other failed request, a POST cut by a navigation included,
    still counts. Belongs in uilab itself (its pinned commit, .github/workflows/full.yml); idempotent, and a
    no-op where uilab is not importable."""
    try:
        from uilab.drivers import playwright_driver
    except ImportError:
        return
    page_class = playwright_driver.PlaywrightPage
    if getattr(page_class, "_retires_own_navigation", False):
        return
    init, goto, failed = page_class.__init__, page_class.goto, page_class._on_request_failed

    def init_tracking(self, page, cdp):
        self._in_flight, self._retiring = set(), set()
        # Plain functions: Playwright marks each handler it wraps, which a builtin bound method refuses.
        page.on("request", lambda request: self._in_flight.add(request))
        page.on("requestfinished", lambda request: self._in_flight.discard(request))
        init(self, page, cdp)

    def goto_retiring(self, url):
        self._retiring.update(request for request in self._in_flight if request.method == "GET")
        return goto(self, url)

    def failed_unless_retired(self, request):
        self._in_flight.discard(request)
        if request in self._retiring and request.failure == "net::ERR_ABORTED":
            self._retiring.discard(request)
            return
        failed(self, request)

    page_class.__init__, page_class.goto, page_class._on_request_failed = init_tracking, goto_retiring, failed_unless_retired
    page_class._retires_own_navigation = True


# ---- the operating system's side: is the cursor confined?

_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def cursor_clip() -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None:
    """(the cursor's clip rectangle, the virtual screen) in physical pixels, both read per-monitor
    DPI aware; None off Windows. Unconfined, the two are equal."""
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    previous = user32.SetThreadDpiAwarenessContext(_PER_MONITOR_AWARE_V2)
    try:
        clip = _Rect()
        user32.GetClipCursor(ctypes.byref(clip))
        left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        screen = (left, top, left + user32.GetSystemMetrics(78), top + user32.GetSystemMetrics(79))
        return (clip.left, clip.top, clip.right, clip.bottom), screen
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


def left_clipped(before, after) -> tuple[int, int, int, int] | None:
    """The clip a test left behind: the cursor was free before it (`cursor_clip()` readings) and is
    confined after. A clip already there before the test is his own, never ours to judge."""
    if not before or not after:
        return None
    (clip_before, screen_before), (clip_after, screen_after) = before, after
    if clip_before != screen_before or clip_after == screen_after:
        return None
    return clip_after


def release_clip() -> None:
    """Frees the cursor: ClipCursor(NULL)."""
    if os.name == "nt":
        ctypes.windll.user32.ClipCursor(None)
