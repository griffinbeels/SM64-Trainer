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
No adaptations from the reference implementation were required."""
import json
import logging

import webview

from sm64_events.core.paths import window_state_path

log = logging.getLogger("sm64.desktop")
URL = "http://127.0.0.1:8064/"
_DEFAULT = {"w": 480, "h": 900, "x": None, "y": None}


def _load_geometry() -> dict:
    try:
        saved = json.loads(window_state_path().read_text())
        return {**_DEFAULT, **saved}
    except Exception:
        return dict(_DEFAULT)


def _save_geometry(win) -> None:
    try:
        state = {"w": int(win.width), "h": int(win.height),
                 "x": int(win.x), "y": int(win.y)}
        p = window_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))
    except Exception:
        log.debug("could not persist window geometry", exc_info=True)


def create(on_closed) -> "webview.Window":
    g = _load_geometry()
    win = webview.create_window(
        "sm64_tracker", url=URL,
        width=g["w"], height=g["h"], x=g["x"], y=g["y"],
        resizable=True, min_size=(360, 500))
    win.events.resized += lambda *a: _save_geometry(win)
    win.events.moved += lambda *a: _save_geometry(win)
    win.events.closed += lambda: on_closed()
    return win


def run() -> None:
    """Blocks on the main thread until the last window closes."""
    webview.start()
