"""The browser owns playback; a separate crop window owns Mario's pixels.

WebviewHost creates a hidden, frameless WebView2 and hands its HWND to Placer.
Placer alone moves and fades that browser on its DPI-aware follower thread.
NullHost supplies the same control-plane surface without any windows.

A transparent WebView2 background DOES NOT make its WinForms backing window
transparent. The September 19 probe over black could not distinguish absent
pixels from black pixels; the September 20 gray-backdrop control disproved its
conclusion. Whole-window alpha darkened every unpainted pixel over the game.
A GDI color key did not remove WebView2's separately composed pixels either.

Mario mode therefore keeps the decoder window at alpha zero and sends cropped
canvas RGBA + position to CropBridge. Its own native layered window submits
pixels, geometry and SourceConstantAlpha together, leaving everything outside
the crop untouched. The whole-picture option still uses the browser window.

Do not pass pywebview transparent=True: its navigation callback shows and
activates the form. The background setter below is marshalled with BeginInvoke
because CoreWebView2 requires its UI thread; it neither blocks the follower nor
claims desktop transparency. WinForms may reset its layered style at startup,
so Placer continues to confirm styles and opacity on every follower tick.
"""
import logging
import os
import threading
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sm64_events.ghost.window import native
from sm64_events.ghost.window.geometry import Rect
from sm64_events.ghost.window.presentation import CropBridge, PageApi

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

#: What `use_transparent_page_background()` can say. Short on purpose: they
#: are written to the diag log by name, one collapsed row each.
BACKGROUND_TRANSPARENT = "transparent"
BACKGROUND_UNREACHABLE = "unreachable"
BACKGROUND_ASKED = "asked"

#: How long to leave the UI thread alone after handing it the setter, or after
#: it refused. The follower asks every tick; without this, a control that is
#: not built yet would collect one queued delegate per tick forever.
BACKGROUND_RETRY_S = 2.0


def transparent_colour():
    """`Color.Transparent`, imported where .NET actually exists.

    pywebview's WinForms backend is what loads `clr` and its assemblies, so
    nothing in this package can import `System.Drawing` before a window has
    been created -- and a unit test never can. Hence one seam, which
    `WebviewHost` takes as an argument.
    """
    from System.Drawing import Color

    return Color.Transparent


def as_delegate(action):
    """A Python callable in the form `Control.BeginInvoke` wants.

    Falls back to the bare callable where there is no .NET, which is what lets
    the marshal itself be driven by a test against a fake control.
    """
    try:
        from System import Action
    except Exception:  # noqa: BLE001 - no .NET here; the callable stands in
        return action
    return Action(action)


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

    def __init__(self, title: str = OVERLAY_TITLE, *,
                 colour=transparent_colour, clock=time.monotonic):
        self._title = title
        self._lock = threading.RLock()
        self._window = None
        self._hwnd = 0
        self._url = "about:blank"
        self._rect: Rect | None = None
        #: The two seams the marshal is driven through in a test: what colour
        #: to set (it needs .NET) and what time it is.
        self._colour = colour
        self._clock = clock
        #: Set by the UI THREAD, read by the follower. None means it has not
        #: answered yet.
        self._background_result: str | None = None
        self._background_quiet_until = 0.0
        self._background_done = False
        #: pythonnet hands the delegate to Windows; it must outlive this call.
        self._background_delegate = None
        self.crop = CropBridge()
        self._crop_stop = threading.Event()
        self._crop_thread = None

    # --- what the child tells it -------------------------------------------

    def open(self, url: str, rect: Rect) -> None:
        with self._lock:
            self._url = url
            self._rect = rect

    def load_url(self, url: str) -> None:
        self.crop.reset()
        with self._lock:
            self._url = url
            window = self._window
        if window is not None:
            window.load_url(self._page_url(url))

    def _page_url(self, url):
        parts = urlsplit(url)
        # .NET Uri escapes a file URL's query into its path. The native rig
        # therefore carries settings in its fragment; keep that transport.
        field = "fragment" if parts.scheme == "file" else "query"
        query = dict(parse_qsl(getattr(parts, field)))
        query["native_session"] = self.crop.session
        return urlunsplit(parts._replace(**{field: urlencode(query)}))

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
            return PageAnswer(state=STATUS, status={**answer, "native_surface": self.crop.diagnostics()})
        return PageAnswer(state=NOTHING,
                          detail=None if answer is None else str(answer)[:120])

    def _control(self):
        """pywebview's WebView2 control, by either of the two shapes it takes."""
        form = getattr(self._window, "native", None)
        found = getattr(form, "webview", None)
        if found is None:
            found = getattr(getattr(form, "browser", None), "webview", None)
        return found

    def use_transparent_page_background(self) -> str | None:
        """Stop the WebView2 control painting its own background.

        Called every tick by the follower, so it must be cheap and must NEVER
        wait on the UI thread. Returns one short word for the diag log, or
        None when there is nothing new to say; the caller collapses repeats.

        RETRIED until it works, and silent only once it has. It used to be
        one-shot, and in his second try it fired at 08:20:06.805 -- a
        millisecond before the window handle existed -- found no control, and
        gave up for the rest of the session. It was ALSO made on this thread,
        which is the older and larger fault: see the module docstring.
        """
        with self._lock:
            if self._background_done:
                return None
            result, self._background_result = self._background_result, None
            if result == BACKGROUND_TRANSPARENT:
                self._background_done = True
                return BACKGROUND_TRANSPARENT
            if result is not None:
                # A refusal is never final: the CoreWebView2 behind the control
                # may simply not have been built yet. Wait, then ask again.
                self._background_quiet_until = self._clock() + BACKGROUND_RETRY_S
                return result
            if self._clock() < self._background_quiet_until:
                return None
        control = self._control()
        # No control yet costs nothing to discover, so it is not rate-limited;
        # the log collapses the repeats into one row with a count.
        return BACKGROUND_UNREACHABLE if control is None else self._ask(control)

    def _ask(self, control) -> str:
        """Hand the setter to the control's OWN thread, and come straight back.

        `BeginInvoke` rather than `Invoke`: the follower may not be parked on a
        UI thread that has wedged. The outcome arrives later, in
        `_background_result`, and the next tick reports it.
        """
        def apply() -> None:
            try:
                control.DefaultBackgroundColor = self._colour()
                outcome = BACKGROUND_TRANSPARENT
            except Exception as error:  # noqa: BLE001 - reported, never raised
                outcome = f"refused: {type(error).__name__}"
            with self._lock:
                self._background_result = outcome

        delegate = as_delegate(apply)
        with self._lock:
            self._background_delegate = delegate
            self._background_quiet_until = self._clock() + BACKGROUND_RETRY_S
        try:
            control.BeginInvoke(delegate)
        except Exception as error:  # noqa: BLE001 - a control with no handle yet
            return f"refused: {type(error).__name__}"
        return BACKGROUND_ASKED

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
            self._title, url=self._page_url(self._url), js_api=PageApi(self.crop),
            x=rect.x, y=rect.y, width=rect.width, height=rect.height,
            # hidden: nothing appears until the follower has decided where AND
            # proved the window landed there.
            # focus=False + frameless: no chrome, no keyboard.
            hidden=True, frameless=True, focus=False, resizable=False,
            easy_drag=False, shadow=False, on_top=False, text_select=False,
            zoomable=False, draggable=False, confirm_close=False,
            background_color=PAGE_BACKGROUND)
        self._crop_thread = threading.Thread(target=self.crop.run, args=(self._crop_stop,),
                                            name="ghost-crop", daemon=True)
        self._crop_thread.start()
        threading.Thread(target=self._close_when_stopped, args=(stop,),
                         name="ghost-window-stop", daemon=True).start()
        webview.start(private_mode=True)

    def close(self) -> None:
        self._crop_stop.set()
        if self._crop_thread is not None:
            self._crop_thread.join(timeout=2)
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
