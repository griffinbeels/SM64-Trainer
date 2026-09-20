"""A cropped picture submitted to Windows as one layered-window update.

The browser remains the decoder and clock owner. Its crop pixels and destination
arrive together here, so Windows never displays a new clip boundary around an old
browser frame. No browser surface, colour key or opaque form is involved.

Create, present, show, hide and close on one thread whose DPI context matches the
physical destination coordinates. ``present`` never shows a hidden window;
``show`` is explicit and only succeeds after an accepted frame. This also lets
native tests exercise the real upload at every opacity without appearing.

This HWND must never receive SetLayeredWindowAttributes: that API changes the
layered-window mode and prevents subsequent UpdateLayeredWindow submissions.
Opacity belongs in the update's SourceConstantAlpha, combined with pixel alpha.
"""
import ctypes
import ctypes.wintypes as wt
import threading
from functools import lru_cache

import numpy as np

from sm64_events.ghost.window import style
from sm64_events.ghost.window.geometry import Rect

MAX_SIDE = 8192
MAX_PIXELS = 4_000_000
_ULW_ALPHA = 2
_AC_SRC_ALPHA = 1
_DIB_RGB_COLORS = 0
_HGDI_ERROR = ctypes.c_void_p(-1).value


class _Point(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class _Size(ctypes.Structure):
    _fields_ = [("cx", wt.LONG), ("cy", wt.LONG)]


class _Blend(ctypes.Structure):
    _fields_ = [("operation", wt.BYTE), ("flags", wt.BYTE),
                ("alpha", wt.BYTE), ("format", wt.BYTE)]


class _BitmapHeader(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("width", wt.LONG), ("height", wt.LONG),
                ("planes", wt.WORD), ("bit_count", wt.WORD),
                ("compression", wt.DWORD), ("size_image", wt.DWORD),
                ("x_pixels_per_m", wt.LONG), ("y_pixels_per_m", wt.LONG),
                ("colours_used", wt.DWORD), ("colours_important", wt.DWORD)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("header", _BitmapHeader), ("colours", wt.DWORD * 1)]


@lru_cache(maxsize=1)
def _apis():
    """Own DLL wrappers so every pointer is 64-bit and last-error is reliable."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    definitions = [
        (user32, "CreateWindowExW", wt.HWND,
         [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
          ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
          wt.HINSTANCE, wt.LPVOID]),
        (user32, "DestroyWindow", wt.BOOL, [wt.HWND]),
        (user32, "ShowWindow", wt.BOOL, [wt.HWND, ctypes.c_int]),
        (user32, "IsWindowVisible", wt.BOOL, [wt.HWND]),
        (user32, "PeekMessageW", wt.BOOL,
         [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]),
        (user32, "TranslateMessage", wt.BOOL, [ctypes.POINTER(wt.MSG)]),
        (user32, "DispatchMessageW", ctypes.c_ssize_t, [ctypes.POINTER(wt.MSG)]),
        (user32, "SetWindowPos", wt.BOOL,
         [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
          ctypes.c_int, wt.UINT]),
        (user32, "UpdateLayeredWindow", wt.BOOL,
         [wt.HWND, wt.HDC, ctypes.POINTER(_Point), ctypes.POINTER(_Size),
          wt.HDC, ctypes.POINTER(_Point), wt.COLORREF,
          ctypes.POINTER(_Blend), wt.DWORD]),
        (gdi32, "CreateCompatibleDC", wt.HDC, [wt.HDC]),
        (gdi32, "CreateDIBSection", wt.HBITMAP,
         [wt.HDC, ctypes.POINTER(_BitmapInfo), wt.UINT,
          ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]),
        (gdi32, "SelectObject", wt.HANDLE, [wt.HDC, wt.HANDLE]),
        (gdi32, "DeleteObject", wt.BOOL, [wt.HANDLE]),
        (gdi32, "DeleteDC", wt.BOOL, [wt.HDC]),
    ]
    for library, name, result, arguments in definitions:
        function = getattr(library, name)
        function.restype, function.argtypes = result, arguments
    return user32, gdi32


def premultiplied_bgra(rgba: bytes) -> bytes:
    """Canvas straight RGBA to the top-down, premultiplied BGRA Win32 needs."""
    if len(rgba) % 4:
        raise ValueError("RGBA buffer must contain complete pixels")
    pixels = np.frombuffer(rgba, dtype=np.uint8).reshape(-1, 4)
    result = np.empty_like(pixels)
    colours = pixels[:, [2, 1, 0]].astype(np.uint16)
    result[:, :3] = (colours * pixels[:, 3, None] + 127) // 255
    result[:, 3] = pixels[:, 3]
    return result.tobytes()


def _validate(rect: Rect, rgba: bytes, width: int, height: int, alpha: int):
    if any(type(value) is not int for value in
           (rect.x, rect.y, rect.width, rect.height, width, height, alpha)):
        raise ValueError("Surface geometry and opacity must be integers")
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE
            and width * height <= MAX_PIXELS):
        raise ValueError("Crop dimensions exceed the surface limit")
    if (width, height) != (rect.width, rect.height):
        raise ValueError("Crop dimensions must match its destination rectangle")
    if not all(-(1 << 31) <= edge < (1 << 31) for edge in
               (rect.x, rect.y, rect.x + width, rect.y + height)):
        raise ValueError("Crop destination exceeds Win32 coordinate limits")
    if not 0 <= alpha <= 255:
        raise ValueError("Surface opacity must be between 0 and 255")
    if not isinstance(rgba, bytes) or len(rgba) != width * height * 4:
        raise ValueError("Crop must contain exactly width * height RGBA bytes")


class _Dib:
    """A reusable top-down bitmap, selected into its own memory DC."""

    def __init__(self, width: int, height: int):
        self.width, self.height = width, height
        self.dc = self.bitmap = self.previous = 0
        self.bits = ctypes.c_void_p()
        _, gdi32 = _apis()
        try:
            self.dc = gdi32.CreateCompatibleDC(None)
            if not self.dc:
                raise ctypes.WinError(ctypes.get_last_error())
            info = _BitmapInfo()
            info.header.size = ctypes.sizeof(_BitmapHeader)
            info.header.width, info.header.height = width, -height
            info.header.planes, info.header.bit_count = 1, 32
            self.bitmap = gdi32.CreateDIBSection(
                self.dc, ctypes.byref(info), _DIB_RGB_COLORS,
                ctypes.byref(self.bits), None, 0)
            if not self.bitmap or not self.bits.value:
                raise ctypes.WinError(ctypes.get_last_error())
            self.previous = gdi32.SelectObject(self.dc, self.bitmap)
            if not self.previous or self.previous == _HGDI_ERROR:
                self.previous = 0
                raise ctypes.WinError(ctypes.get_last_error())
        except OSError:
            self.close()
            raise

    def close(self):
        _, gdi32 = _apis()
        if self.previous:
            gdi32.SelectObject(self.dc, self.previous)
            self.previous = 0
        if self.bitmap:
            gdi32.DeleteObject(self.bitmap)
            self.bitmap = 0
        if self.dc:
            gdi32.DeleteDC(self.dc)
            self.dc = 0
        self.bits = ctypes.c_void_p()


class LayeredSurface:
    """One unowned crop window; the first native operation binds its thread."""

    def __init__(self, title: str = "SM64 Trainer ghost picture"):
        self._title = title
        self._thread: int | None = None
        self._hwnd = 0
        self._dib: _Dib | None = None
        self._presented = False
        self.last_error: str | None = None

    @property
    def hwnd(self) -> int:
        return self._hwnd

    def _on_thread(self):
        current = threading.get_ident()
        if self._thread is not None and self._thread != current:
            raise RuntimeError("The crop surface must stay on its creating thread")
        self._thread = current

    def ensure(self) -> int:
        """Create hidden; no owner, taskbar entry, focus or visible background."""
        self._on_thread()
        if not self._hwnd:
            user32, _ = _apis()
            self._hwnd = int(user32.CreateWindowExW(
                style.OVERLAY_EX_STYLE, "STATIC", self._title,
                style.CREATE_STYLE, 0, 0, 1, 1, None, None, None, None) or 0)
            if not self._hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
        return self._hwnd

    def _buffer(self, width: int, height: int) -> _Dib:
        if self._dib is None or (self._dib.width, self._dib.height) != (width, height):
            replacement = _Dib(width, height)
            if self._dib is not None:
                self._dib.close()
            self._dib = replacement
        return self._dib

    def _failed(self, operation: str) -> bool:
        self.last_error = f"{operation}: {ctypes.WinError(ctypes.get_last_error())}"
        self.hide()
        return False

    def present(self, rect: Rect, rgba: bytes, width: int, height: int,
                alpha: int, insert_after: int | None = None) -> bool:
        """Submit pixels, screen position, dimensions and opacity together.

        ``insert_after`` is already resolved by the caller: the window ABOVE
        the game (zero means the ordinary band's top), never the game itself.
        No game-window query can delay this frame's upload.
        """
        self._on_thread()
        _validate(rect, rgba, width, height, alpha)
        hwnd = self.ensure()
        buffer = self._buffer(width, height)
        pixels = premultiplied_bgra(rgba)
        ctypes.memmove(buffer.bits, pixels, len(pixels))
        user32, _ = _apis()
        if insert_after is not None:
            placed = user32.SetWindowPos(
                hwnd, insert_after, 0, 0, 0, 0,
                style.PLACE_FLAGS | style.SWP_NOMOVE | style.SWP_NOSIZE)
            if not placed:
                return self._failed("SetWindowPos")
        destination = _Point(rect.x, rect.y)
        source = _Point(0, 0)
        size = _Size(width, height)
        blend = _Blend(0, 0, alpha, _AC_SRC_ALPHA)
        if not user32.UpdateLayeredWindow(
                hwnd, None, ctypes.byref(destination), ctypes.byref(size),
                buffer.dc, ctypes.byref(source), 0, ctypes.byref(blend), _ULW_ALPHA):
            return self._failed("UpdateLayeredWindow")
        self._presented = True
        self.last_error = None
        return True

    def show(self) -> bool:
        """Show a submitted frame without activation; empty windows stay hidden."""
        self._on_thread()
        if not self._hwnd or not self._presented:
            return False
        user32, _ = _apis()
        user32.ShowWindow(self._hwnd, style.SW_SHOWNOACTIVATE)
        return bool(user32.IsWindowVisible(self._hwnd))

    def pump(self) -> None:
        """Service this window on each tick of a dedicated render thread."""
        self._on_thread()
        if self._hwnd:
            user32, _ = _apis()
            message = wt.MSG()
            for _ in range(64):
                if not user32.PeekMessageW(ctypes.byref(message), self._hwnd, 0, 0, 1):
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))

    def hide(self) -> None:
        self._on_thread()
        if self._hwnd:
            user32, _ = _apis()
            user32.ShowWindow(self._hwnd, style.SW_HIDE)
        self._presented = False

    def close(self) -> None:
        """Destroy the owned window and release its selected bitmap and DC."""
        self._on_thread()
        if self._hwnd:
            user32, _ = _apis()
            if not user32.DestroyWindow(self._hwnd):
                raise ctypes.WinError(ctypes.get_last_error())
            self._hwnd = 0
        if self._dib is not None:
            self._dib.close()
            self._dib = None
        self._presented = False
