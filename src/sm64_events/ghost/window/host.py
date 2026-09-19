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

**`transparent=True` is never passed, and three separate things in pywebview
6.2.1 say so.** All three are in this venv's source, not from memory:

- `edgechromium.py::on_navigation_start` calls `self.form.Show()` and
  `self.form.Activate()` whenever the window is transparent. That runs on
  EVERY navigation, and the session navigates the ghost on every target
  change -- it would show an unplaced window and take the keyboard off him
  mid-attempt. On its own this rules the flag out.
- `winforms.py::create_window` reaches its transparency workaround (`Show()`
  then `Hide()`, under the comment saying that is what makes a transparent
  window work) only through an `elif` a HIDDEN window never takes, so the
  ghost would not get it anyway. **Note what the hidden branch does instead,
  because an earlier version of this docstring implied a hidden window is
  never shown and that is wrong:** it runs `Opacity = 0; Show(); Hide();
  Opacity = 1`. The form IS shown, invisibly, before any of our styles exist
  -- and WinForms implements `Form.Opacity` with `WS_EX_LAYERED`, the very bit
  whole-window alpha needs. That is why nothing in this package is one-shot;
  see `placer.Placer.prepare`.
- `winforms.py` never sets `AllowTransparency` or `TransparencyKey`, so the
  FORM stays opaque whatever the page does.

The background is reached directly instead, by
`use_transparent_page_background()` below. Be clear about what that can and
cannot buy: it stops the WebView2 CONTROL painting, which reveals the form
behind it, and that form is still opaque (`BackColor` = `PAGE_BACKGROUND`).
Making the game show through the un-footaged parts of the window as well would
need a colour key (`LWA_COLORKEY`) punching that colour out of the layered
window -- deliberately not shipped, because nothing here can be seen by an
agent and the footage he is practising on (Hazy Maze Cave) is full of
near-black pixels that a black key would punch holes through.

**So the dark sheet is solved by not being on screen, not by transparency.**
Whole-window alpha blends every pixel of the window equally -- live 2026-09-19,
80 % opacity read as a translucent dark sheet over his game and his browser --
so a window with no footage on it is a window dimming his game. The child only
ever shows it while the page says a picture is presentable; `page_status()` is
how it asks.
"""
import logging
import os
import threading
from dataclasses import dataclass

from sm64_events.ghost.window import native
from sm64_events.ghost.window.geometry import Rect

log = logging.getLogger("sm64.ghost")

#: The window's title. Nothing shows it (there is no frame and no taskbar
#: button), but it is how the process finds its own window handle.
OVERLAY_TITLE = "SM64 Trainer ghost overlay"

#: The FALLBACK background, used for the form and for the WebView2 control
#: until `use_transparent_page_background()` takes. Black rather than
#: pywebview's white default on purpose: if that call is ever refused, a black
#: rectangle over the game is bad and a white one is blinding.
PAGE_BACKGROUND = "#000000"

_DEFAULT_RECT = Rect(x=0, y=0, width=640, height=480)


#: What the page is asked, once per page-watch tick. Written to survive a page
#: that has not defined the function yet (an early load, or an older build of
#: `ui/ghost/index.html`): that answers null, which reads as NOT presentable.
STATUS_SCRIPT = "window.ghostStatus ? window.ghostStatus() : null"

#: Every way asking the page can turn out. They were all `None` before, which
#: is what made his second try unreadable: one row saying `said: null` cannot
#: distinguish "there was no window yet" from "evaluate_js raised" from "the
#: page answered nothing".
NO_WINDOW, RAISED, NOTHING, STATUS = "no_window", "raised", "nothing", "status"


@dataclass(frozen=True)
class PageAnswer:
    """What came back when the page was asked, and which kind of answer it is."""

    state: str
    status: dict | None = None
    detail: str | None = None

    @property
    def presentable(self) -> bool:
        return bool(self.status and self.status.get("presentable"))


class NullHost:
    """A host with no window, for driving everything around one."""

    name = "none"
    #: There is no window to make ready, so the follower's readiness gate has
    #: nothing to wait for. Without this the gate never opens and the `none`
    #: backend -- the one that proves the control plane and the follower with
    #: nothing on screen -- would sit refusing every decision forever.
    hosts_window = False

    def __init__(self):
        self.calls: list[tuple] = []
        #: What a page would have answered. A backend with no window stands in
        #: for a healthy one, so the control plane and the follower can be
        #: driven end to end; a test that cares about the gate sets it.
        self.status: dict | None = {"presentable": True}

    def open(self, url: str, rect: Rect) -> None:
        self.calls.append(("open", url, rect))

    def load_url(self, url: str) -> None:
        self.calls.append(("load_url", url))

    def page_status(self) -> PageAnswer:
        if self.status is None:
            return PageAnswer(state=NOTHING)
        return PageAnswer(state=STATUS, status=self.status)

    def use_transparent_page_background(self) -> str | None:
        return "no_window"

    def hwnd(self) -> int:
        return 0

    def run(self, stop: threading.Event) -> None:
        stop.wait()

    def close(self) -> None:
        self.calls.append(("close",))


class WebviewHost:
    """One frameless WebView2 window. Placing it is somebody else's job."""

    name = "webview"
    hosts_window = True

    def __init__(self, title: str = OVERLAY_TITLE):
        self._title = title
        self._lock = threading.RLock()
        self._window = None
        self._hwnd = 0
        self._url = "about:blank"
        self._rect: Rect | None = None
        self._background_tried = False

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

    def page_status(self) -> PageAnswer:
        """What the page says about itself, and WHICH KIND of answer it is.

        Every failure used to come back as `None`, which is why his second try
        could not be read: "no window yet", "evaluate_js raised" and "the page
        answered nothing" were one row. They are three states now, and the
        exception text travels with the third.

        **This blocks, and it may block forever.** pywebview's `evaluate_js`
        marshals to the WebView2 UI thread and waits on a semaphore with no
        timeout (`edgechromium.py`), behind a readiness wrapper that waits 20 s
        and then raises. That is why `child.PageWatch` owns the asking on a
        thread of its own and the follower only ever reads a cached answer: a
        frozen follower is a ghost frozen on his screen.
        """
        window = self._window
        if window is None:
            return PageAnswer(state=NO_WINDOW)
        try:
            answer = window.evaluate_js(STATUS_SCRIPT)
        except Exception as error:  # noqa: BLE001 - reported, never swallowed
            log.debug("the ghost page would not answer", exc_info=True)
            return PageAnswer(state=RAISED,
                              detail=f"{type(error).__name__}: {error}"[:300])
        if isinstance(answer, dict):
            return PageAnswer(state=STATUS, status=answer)
        return PageAnswer(state=NOTHING,
                          detail=None if answer is None else str(answer)[:120])

    def use_transparent_page_background(self) -> str | None:
        """Stop the WebView2 control painting its own background.

        RETRIED until it works, and None only once it has. It used to be
        one-shot, and in his second try it fired at 08:20:06.805 -- a
        millisecond before the window handle existed -- found no control, and
        gave up for the rest of the session.

        See the module docstring for why this is done by hand instead of with
        pywebview's `transparent=True`, and for what it does and does not buy.
        """
        with self._lock:
            if self._background_tried:
                return None
            form = getattr(self._window, "native", None)
            control = getattr(form, "webview", None)
            if control is None:
                control = getattr(getattr(form, "browser", None), "webview", None)
            if control is None:
                return "unreachable"
            try:
                from System.Drawing import Color
                control.DefaultBackgroundColor = Color.Transparent
            except Exception as error:  # noqa: BLE001 - the black fallback is fine
                return f"refused: {type(error).__name__}: {error}"
            self._background_tried = True
            return "transparent"

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
