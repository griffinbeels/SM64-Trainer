# src/sm64_events/desktop/window.py
"""pywebview window over the running server, with geometry persistence so a
full-portrait or maximized layout reopens where you left it.

The window is freely resizable with no max bound (the user fills a full
vertical monitor); the content is the same responsive UI the browser serves.

pywebview 6.2.1 API notes (verified via inspect.signature + source read):
- create_window() accepts x=None/y=None natively (omits positioning when None)
- events.resized, events.moved, events.closed all exist in 6.2.1
- win.width, win.height, win.x, win.y are @property accessors in Window class
- += operator is supported on Event objects via __iadd__
- webview.start() accepts icon= (path str) to set the window/taskbar icon
- create_window() has minimized=False default; no runtime .minimized property
  to query — detect via the -32000 sentinel Windows uses for minimized windows.
No adaptations from the reference implementation were required."""
import json
import logging

import webview

from sm64_events.core.paths import APP_DISPLAY_NAME, server_port, window_state_path
from sm64_events.desktop.tray import _asset_path

log = logging.getLogger("sm64.desktop")

# The narrowest window this app is SUPPORTED at (user's call, 2026-07-29:
# "the minimum officially supported width we should support is 850px. Height
# can be any height, that's fine").
#
# Enforced in three places, and it has to be all three or it is not enforced:
#   1. `min_size` below, so the frame cannot be dragged narrower;
#   2. `_DEFAULT`, which was 480 -- a first run opened BELOW the minimum;
#   3. the clamp in `_load_geometry`, because a window saved at 480px before
#      this existed would otherwise reopen at 480px forever.
# The layout gate's matrix floor is the same number
# (`tools/uilab_project.py`), so what ships and what is measured agree.
MIN_WINDOW_WIDTH = 850

_DEFAULT = {"w": 900, "h": 900, "x": None, "y": None}

# Windows uses -32000,-32000 as sentinel coordinates when a window is minimized.
# Any position <= this threshold is off-screen/minimized; skip persisting it.
_WIN_MINIMIZED_SENTINEL = -30000


def _load_geometry() -> dict:
    try:
        saved = json.loads(window_state_path().read_text())
        g = {**_DEFAULT, **saved}
        # Reject off-screen or minimized positions: restore size only and let
        # the OS place the window on-screen. Size (w/h) is always kept.
        x, y = g.get("x"), g.get("y")
        if x is not None and (x <= _WIN_MINIMIZED_SENTINEL or y <= _WIN_MINIMIZED_SENTINEL):
            log.debug("discarding off-screen/minimized saved position (%s,%s)", x, y)
            g["x"] = None
            g["y"] = None
        # Clamp UP to the supported minimum. A window saved narrower than this
        # -- every window saved before 2026-07-29, since the default was 480 --
        # would otherwise reopen at its old width and land in a layout nothing
        # tests. `min_size` alone does not do this: it constrains DRAGGING, not
        # the size a window is created at.
        if g.get("w") is not None and g["w"] < MIN_WINDOW_WIDTH:
            log.debug("raising saved width %s to the supported minimum %s",
                      g["w"], MIN_WINDOW_WIDTH)
            g["w"] = MIN_WINDOW_WIDTH
        return g
    except Exception:
        return dict(_DEFAULT)


def _save_geometry(win) -> None:
    """Persist window size + position. SKIPS the write when the window is
    minimized: Windows reports -32000,-32000 for minimized windows, which
    restores off-screen on the next launch. Size is still valuable to keep
    (vertical-monitor workflow), so we only reject clearly-bad samples."""
    try:
        x, y = int(win.x), int(win.y)
        w, h = int(win.width), int(win.height)
        # Guard: skip if coordinates are the Windows minimized sentinel or
        # dimensions are implausibly small (window not yet laid out).
        if x <= _WIN_MINIMIZED_SENTINEL or y <= _WIN_MINIMIZED_SENTINEL:
            log.debug("skip geometry save: minimized sentinel (%s,%s)", x, y)
            return
        if w < 100 or h < 100:
            log.debug("skip geometry save: implausible size (%s,%s)", w, h)
            return
        state = {"w": w, "h": h, "x": x, "y": y}
        p = window_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))
    except Exception:
        log.debug("could not persist window geometry", exc_info=True)


def create(on_closed) -> "webview.Window":
    g = _load_geometry()
    win = webview.create_window(
        APP_DISPLAY_NAME, url=f"http://127.0.0.1:{server_port()}/",
        width=g["w"], height=g["h"], x=g["x"], y=g["y"],
        resizable=True, min_size=(MIN_WINDOW_WIDTH, 500))
    win.events.resized += lambda *a: _save_geometry(win)
    win.events.moved += lambda *a: _save_geometry(win)
    win.events.closed += lambda: on_closed()
    return win


def run() -> None:
    """Blocks on the main thread until the last window closes."""
    # icon= sets the window/taskbar icon (pywebview 6.2.1 webview.start param).
    icon_path = str(_asset_path("ukiki.ico"))
    webview.start(icon=icon_path)
