"""The desktop app hands the Scene viewer page its pictures through WebView2 shared memory (M4 lab).

    pictures = WebView2Pictures(window)       # the pywebview window desktop/app.py made
    pictures.post(pixels, {"id": 7})          # False when it cannot: the server sends a JPEG instead

A page in that window that sees `chrome.webview` asks for encoding "shared"; the server then
skips the JPEG, puts the renderer's own pixels (ReadScreen's BGR rows, bottom-up) in a WebView2
shared buffer and posts it to the page, whose `sharedbufferreceived` event carries the request
id this module was given, and answers the request with a header and no payload. The page joins
the two by id, draws the exact pixels and releases its view.

Evidence (2026-10-26, smo-wv2-probe.py: a never-shown pywebview 6.2.1 window, WebView2 SDK
1.0.3856.49, runtime 155.0.4283.62): 80 pictures of 5,760,000 bytes arrived intact, every word
checked; creating, filling and posting one took 2.3 ms here, it reached the page 0.2 ms later,
and the viewer's drawRaw drew it in 3.4 ms. Microsoft's contract
(learn.microsoft.com, CoreWebView2.PostSharedBufferToScript and CoreWebView2SharedBuffer.Close):
each post gives the page its own ArrayBuffer view; closing the native buffer leaves the page's
view intact; the page frees its view with chrome.webview.releaseBuffer. WebView2 objects belong
to the window's GUI thread, so every call goes through form.Invoke, as pywebview's own calls do.
The copy into the buffer runs on the calling thread, not the GUI thread.
"""
from __future__ import annotations

import contextlib
import ctypes
import json


class WebView2Pictures:
    """Posts pictures to one pywebview window's page. Thread-safe enough for one caller thread
    (the server's delivery thread); never raises."""

    def __init__(self, window):
        self.window = window

    def post(self, pixels, info: dict) -> bool:
        """Puts `pixels` (bytes or a contiguous view) in a new shared buffer, posts it to the page
        with `info` as its additional data, and closes the native side. False when the window, its
        WebView2 or the post is not there; nothing reaches the page then."""
        form = getattr(self.window, "native", None)
        control = getattr(form, "webview", None)
        if control is None:
            return False
        try:
            from Microsoft.Web.WebView2.Core import CoreWebView2SharedBufferAccess
            from System import Func, Type, UInt64
        except ImportError:
            return False
        size, made = len(pixels), {}

        def create():
            core = control.CoreWebView2
            buffer = core.Environment.CreateSharedBuffer(UInt64(size))
            made.update(core=core, buffer=buffer, address=int(buffer.Buffer.ToInt64()))

        def post():
            try:
                made["core"].PostSharedBufferToScript(made["buffer"], CoreWebView2SharedBufferAccess.ReadOnly,
                                                      json.dumps(info))
            finally:
                made["buffer"].Close()

        try:
            _on_ui(form, create, Func, Type)
            _copy(made["address"], pixels, size)
            _on_ui(form, post, Func, Type)
            return True
        except Exception:  # noqa: BLE001 - any failure means the JPEG goes instead
            buffer = made.get("buffer")
            if buffer is not None:
                with contextlib.suppress(Exception):   # a window that is gone has nothing to close
                    _on_ui(form, buffer.Close, Func, Type)
            return False


def _on_ui(form, call, Func, Type):
    """Runs `call` on the form's GUI thread and returns once it has; its exception is raised here
    (an exception left on the GUI thread would end the window's message loop)."""
    failed = []

    def run():
        try:
            call()
        except Exception as error:  # noqa: BLE001 - re-raised on the caller's thread
            failed.append(error)
    form.Invoke(Func[Type](run))
    if failed:
        raise failed[0]


def _copy(address: int, pixels, size: int) -> None:
    if isinstance(pixels, memoryview) and not pixels.readonly:
        source = (ctypes.c_char * size).from_buffer(pixels)     # no copy; released below
        try:
            ctypes.memmove(address, source, size)
        finally:
            del source
    else:
        ctypes.memmove(address, bytes(pixels), size)
