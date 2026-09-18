"""What actually holds the ghost picture: a WebView2 window, or nothing.

Two hosts behind one small surface.

`WebviewHost` is the shipped one. pywebview must own its process's MAIN
thread, which is the whole reason the overlay is a child process rather than
part of the desktop shell: browser and desktop users both get the ghost, so
the server side owns it. The window is created HIDDEN and restyled the moment
its handle is known, so it never appears with a frame and never appears
before it has been placed on the game picture.

`NullHost` records what a window would have been told. It is what lets the
control plane and the follower be driven end to end in a test with nothing on
screen -- the hard limit on this feature's verification.

Anything a host is told before its window exists is remembered and applied on
the first flush, because the trainer can set an opacity a whole second before
WebView2 has finished starting.
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

    def set_alpha(self, alpha: int) -> None:
        self.calls.append(("set_alpha", alpha))

    def place(self, rect: Rect, owner_hwnd: int) -> None:
        self.calls.append(("place", rect, owner_hwnd))

    def show(self) -> None:
        self.calls.append(("show",))

    def hide(self) -> None:
        self.calls.append(("hide",))

    def hwnd(self) -> int:
        return 0

    def run(self, stop: threading.Event) -> None:
        stop.wait()

    def close(self) -> None:
        self.calls.append(("close",))


class WebviewHost:
    """One frameless, click-through WebView2 window, glued to Project64."""

    name = "webview"

    def __init__(self, title: str = OVERLAY_TITLE):
        self._title = title
        self._lock = threading.RLock()
        self._window = None
        self._hwnd = 0
        self._styled = False
        self._url = "about:blank"
        self._rect: Rect | None = None
        self._owner = 0
        self._owned = 0
        self._alpha: int | None = None
        self._visible = False

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

    def set_alpha(self, alpha: int) -> None:
        with self._lock:
            self._alpha = alpha
        self._flush()

    def place(self, rect: Rect, owner_hwnd: int) -> None:
        with self._lock:
            self._rect = rect
            self._owner = int(owner_hwnd or 0)
        self._flush()

    def show(self) -> None:
        with self._lock:
            self._visible = True
        self._flush()

    def hide(self) -> None:
        with self._lock:
            self._visible = False
        self._flush()

    def hwnd(self) -> int:
        return self._hwnd

    # --- the window itself --------------------------------------------------

    def run(self, stop: threading.Event) -> None:
        """Blocks this (main) thread until `stop` is set or the window closes."""
        import webview

        rect = self._rect or _DEFAULT_RECT
        self._window = webview.create_window(
            self._title, url=self._url,
            x=rect.x, y=rect.y, width=rect.width, height=rect.height,
            # hidden: nothing appears until the follower has decided where.
            # focus=False + frameless: no chrome, no keyboard.
            hidden=True, frameless=True, focus=False, resizable=False,
            easy_drag=False, shadow=False, on_top=False, text_select=False,
            zoomable=False, draggable=False, confirm_close=False,
            background_color="#000000")
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

    def _resolve_hwnd(self) -> int:
        """The window handle, the moment WebView2 has one.

        pywebview's native object is the first place to look; the fallback is
        this process's own window list, because the overlay is hidden and the
        visible-only picker in replay/window.py cannot see it.
        """
        if self._hwnd:
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

    def _flush(self) -> None:
        """Apply everything the host has been told, in the one right order."""
        with self._lock:
            hwnd = self._resolve_hwnd()
            if not hwnd:
                return
            if not self._styled:
                applied = native.apply_overlay_styles(hwnd)
                readback = native.get_ex_style(hwnd)
                if readback != applied:
                    log.warning("ghost overlay styles: asked %#x, window kept %#x",
                                applied, readback)
                self._styled = True
            if self._alpha is not None:
                native.set_alpha(hwnd, self._alpha)
            if self._rect is not None:
                if self._owner and self._owner != self._owned:
                    self._owned = self._owner if native.set_owner(hwnd, self._owner) else 0
                native.place(hwnd, self._rect, insert_after=self._owner or None)
            if self._visible:
                native.show_no_activate(hwnd)
            else:
                native.hide(hwnd)


#: Backend name -> host. `none` is how the control plane and the follower are
#: proved without putting a window on the player's screen.
BACKENDS = {"webview": WebviewHost, "none": NullHost}


def make_host(backend: str):
    return BACKENDS[backend]()
