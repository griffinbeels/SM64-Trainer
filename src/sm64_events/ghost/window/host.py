"""What actually holds the ghost picture: a WebView2 window, or nothing.

Two hosts behind one small surface.

`WebviewHost` is the shipped one. pywebview must own its process's MAIN
thread, which is the whole reason the overlay is a child process rather than
part of the desktop shell: browser and desktop users both get the ghost, so
the server side owns it. The window is created HIDDEN and is not shown until
the follower has placed it and READ THAT PLACEMENT BACK.

`NullHost` records what a window would have been told. It is what lets the
control plane and the follower be driven end to end in a test with nothing on
screen -- the hard limit on this feature's verification.

**A host no longer moves, shows, hides or fades anything.** Every user32 write
belongs to `placer.Placer` on the follower thread, because DPI awareness is
per thread and a rectangle read on one thread and applied on another is a
rectangle multiplied by the display scale. A host creates the window, loads
URLs into it and hands out its handle; that is all.

**Why the page background stays black, and `transparent=True` is not used.**
The follower track asked for a transparent page so the game would show
untouched wherever there is no footage. Read against pywebview 6.2.1 in this
venv, that flag cannot deliver it here, for two reasons that are both in the
source:

- `winforms.py` never sets `AllowTransparency` or `TransparencyKey` on the
  form. `transparent=True` only sets the WebView2 control's
  `DefaultBackgroundColor` to `Color.Transparent` (`edgechromium.py`), so the
  page becomes see-through to the FORM behind it -- and that form is an
  ordinary opaque window whose `BackColor` is left at the WinForms default in
  exactly that branch. A transparent page would trade a black rectangle for a
  light-grey one.
- `create_window` runs its transparency workaround (`Show()` then `Hide()`,
  which its own comment says is what makes a transparent window work) only in
  the branch for a window that is NOT hidden. The ghost must be created
  hidden, so it takes the other branch and never gets it.

Whole-window alpha (`WS_EX_LAYERED` + `SetLayeredWindowAttributes`) blends
every pixel of the window by the same amount, so "untouched outside the
footage" is not something an alpha value can express either way. The one
technique that would express it is a colour key (`LWA_COLORKEY`), punching the
background colour out completely while the video keeps its alpha. That is a
real option and it is deliberately not taken yet: nothing in this package can
be seen by an agent, and a key colour appearing inside the footage would punch
holes in the ghost. It wants his eyes before it ships.
"""
import logging
import os
import threading

from sm64_events.ghost.window import native
from sm64_events.ghost.window.geometry import Rect

log = logging.getLogger("sm64.ghost")

#: The window's title. Nothing shows it (there is no frame and no taskbar
#: button), but it is how the process finds its own window handle.
OVERLAY_TITLE = "SM64 Trainer ghost overlay"

#: What the page paints where it has nothing to show. See the module docstring
#: for why this is not transparent.
PAGE_BACKGROUND = "#000000"

_DEFAULT_RECT = Rect(x=0, y=0, width=640, height=480)


class NullHost:
    """A host with no window, for driving everything around one."""

    name = "none"

    def __init__(self):
        self.calls: list[tuple] = []

    def open(self, url: str, rect: Rect) -> None:
        self.calls.append(("open", url, rect))

    def load_url(self, url: str) -> None:
        self.calls.append(("load_url", url))

    def hwnd(self) -> int:
        return 0

    def run(self, stop: threading.Event) -> None:
        stop.wait()

    def close(self) -> None:
        self.calls.append(("close",))


class WebviewHost:
    """One frameless WebView2 window. Placing it is somebody else's job."""

    name = "webview"

    def __init__(self, title: str = OVERLAY_TITLE):
        self._title = title
        self._lock = threading.RLock()
        self._window = None
        self._hwnd = 0
        self._url = "about:blank"
        self._rect: Rect | None = None

    # --- what the child tells it -------------------------------------------

    def open(self, url: str, rect: Rect) -> None:
        with self._lock:
            self._url = url
            self._rect = rect

    def load_url(self, url: str) -> None:
        with self._lock:
            self._url = url
            window = self._window
        if window is not None:
            window.load_url(url)

    def hwnd(self) -> int:
        """The window handle, the moment WebView2 has one.

        pywebview's native object is the first place to look; the fallback is
        this process's own window list, because the overlay is hidden and the
        visible-only picker in replay/window.py cannot see it.

        Called from the follower thread only, like every other window query.
        """
        with self._lock:
            if self._hwnd or self._window is None:
                return self._hwnd
            handle = getattr(self._window, "native", None)
            for attribute in ("Handle", "handle", "hwnd"):
                raw = getattr(handle, attribute, None)
                value = raw.ToInt64() if hasattr(raw, "ToInt64") else raw
                if isinstance(value, int) and value:
                    self._hwnd = value
                    break
            else:
                self._hwnd = native.find_overlay_window(os.getpid(), self._title) or 0
            if self._hwnd:
                log.info("ghost overlay window handle %#x", self._hwnd)
            return self._hwnd

    # --- the window itself --------------------------------------------------

    def run(self, stop: threading.Event) -> None:
        """Blocks this (main) thread until `stop` is set or the window closes."""
        import webview

        rect = self._rect or _DEFAULT_RECT
        self._window = webview.create_window(
            self._title, url=self._url,
            x=rect.x, y=rect.y, width=rect.width, height=rect.height,
            # hidden: nothing appears until the follower has decided where AND
            # proved the window landed there.
            # focus=False + frameless: no chrome, no keyboard.
            hidden=True, frameless=True, focus=False, resizable=False,
            easy_drag=False, shadow=False, on_top=False, text_select=False,
            zoomable=False, draggable=False, confirm_close=False,
            background_color=PAGE_BACKGROUND)
        threading.Thread(target=self._close_when_stopped, args=(stop,),
                         name="ghost-window-stop", daemon=True).start()
        webview.start(private_mode=True)

    def close(self) -> None:
        window, self._window = self._window, None
        if window is None:
            return
        try:
            window.destroy()
        except Exception:  # noqa: BLE001 - a window already gone must not stop teardown
            log.debug("ghost overlay window was already gone", exc_info=True)

    def _close_when_stopped(self, stop: threading.Event) -> None:
        stop.wait()
        self.close()


#: Backend name -> host. `none` is how the control plane and the follower are
#: proved without putting a window on the player's screen.
BACKENDS = {"webview": WebviewHost, "none": NullHost}


def make_host(backend: str):
    return BACKENDS[backend]()
