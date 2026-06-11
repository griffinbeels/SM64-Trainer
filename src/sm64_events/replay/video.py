"""windows-capture (WGC) adapter -> recorder VideoSource protocol.

MONITOR capture cropped to the PJ64 window — NOT window capture. Live-audit
finding (2026-06-11): WGC window capture of PJ64 1.6 delivers frames at full
cadence but with FROZEN content (~1-6 unique frames/s even during play) —
Jabo's D3D8 presentation path bypasses the DWM per-window composition
surface, so the window's capturable surface barely updates even though the
screen shows the game live. Monitor capture sees the actual scanout, which
always has the real pixels. Evidence: 6 s probe, 188 window-capture
deliveries -> 1 unique frame, while session segments during active play held
<= 12 unique frames per 60.

Tradeoff (accepted): monitor capture records whatever covers the window —
occlusion robustness is lost. The window rect is re-queried every frame
(cheap ctypes), so moving/resizing the window tracks correctly; a minimized
window yields no frames (a gap -> honest coverage hole in the ring).

Lazy import: constructing the recorder must never require capture hardware.
frame.timespan is WGC SystemRelativeTime — QPC 100 ns ticks, the same
timebase CaptureClock anchors against (clock.py).

The crop slice is copied: the library may reuse the underlying buffer
between callbacks, and the recorder holds the last frame for CFR gap fill.

Event registration API note: windows_capture.WindowsCapture.event() checks
handler.__name__ — the decorated functions MUST be named on_frame_arrived
and on_closed exactly.

windows-capture monitor_index is 1-BASED in EnumDisplayMonitors order
(verified live; index 0 raises)."""
import ctypes
import ctypes.wintypes as wt
import logging

from sm64_events.replay.window import WindowInfo

log = logging.getLogger("sm64.replay")

_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_MONITOR_DEFAULTTONEAREST = 2
_DPI_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def _ensure_dpi_aware() -> None:
    """DWM extended-frame bounds are PHYSICAL pixels regardless of process
    DPI awareness, but GetMonitorInfo is virtualized for unaware processes —
    on a scaled display the two disagree (seen live: monitor 2560x1440
    virtualized vs a 2403x1907-physical window). Making the process
    per-monitor aware puts every coordinate in physical pixels, matching the
    WGC frame. Best-effort: fails harmlessly if awareness was already set."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            _DPI_PER_MONITOR_AWARE_V2)
    except Exception:
        pass


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Visible window bounds in virtual-screen coords (DWM extended frame
    bounds — excludes the drop shadow; falls back to GetWindowRect).
    None when the window is minimized/gone (rect would be meaningless)."""
    user32 = ctypes.windll.user32
    if not user32.IsWindow(wt.HWND(hwnd)) or user32.IsIconic(wt.HWND(hwnd)):
        return None
    rect = wt.RECT()
    res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wt.HWND(hwnd), _DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect), ctypes.sizeof(rect))
    if res != 0:
        if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect)):
            return None
    return rect.left, rect.top, rect.right, rect.bottom


def monitor_geometry(hwnd: int) -> tuple[int, tuple[int, int, int, int]]:
    """(1-based monitor index in EnumDisplayMonitors order, monitor rect).

    windows-capture enumerates monitors in the same EnumDisplayMonitors
    order, 1-based — so the position of this window's HMONITOR in that
    enumeration IS the crate's monitor_index."""
    user32 = ctypes.windll.user32
    target = user32.MonitorFromWindow(wt.HWND(hwnd), _MONITOR_DEFAULTTONEAREST)
    monitors: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT),
                        wt.LPARAM)
    def cb(hmon, _hdc, _rect, _lparam):
        monitors.append(hmon)
        return True

    user32.EnumDisplayMonitors(None, None, cb, 0)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    user32.GetMonitorInfoW(wt.HMONITOR(target), ctypes.byref(info))
    rc = info.rcMonitor
    try:
        index = monitors.index(target) + 1
    except ValueError:
        index = 1
    return index, (rc.left, rc.top, rc.right, rc.bottom)


def crop_bounds(frame_w: int, frame_h: int,
                win_rect: tuple[int, int, int, int],
                mon_rect: tuple[int, int, int, int],
                ) -> tuple[int, int, int, int] | None:
    """Window rect (virtual-screen coords) -> frame-pixel slice bounds,
    clamped to the frame; None when the visible intersection is degenerate.
    Pure — unit-tested."""
    mx, my = mon_rect[0], mon_rect[1]
    x0 = max(0, min(frame_w, win_rect[0] - mx))
    y0 = max(0, min(frame_h, win_rect[1] - my))
    x1 = max(0, min(frame_w, win_rect[2] - mx))
    y1 = max(0, min(frame_h, win_rect[3] - my))
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    return x0, y0, x1, y1


class GdiBitBltVideoSource:
    """GDI BitBlt of the window's CLIENT DC — the working capture path for
    PJ64 1.6 (Jabo D3D8).

    Why not WGC/DDA: Jabo presents via the legacy BITBLT model (back buffer
    copied into the window's redirection surface every Present), while WGC
    and DXGI duplication read the DWM composition path, which Win11 24H2
    refreshes only on dirty-region/MPO cadence for this app class —
    live-measured at 1-6 unique frames/s while the game ran at 30 fps.
    BitBlt from the window DC reads the redirection surface itself (the
    same mechanism as OBS's "BitBlt (legacy)" window capture, the
    community-proven method for PJ64 1.6). Bonus: window DC content
    survives occlusion.

    Runs its own grab thread at cfg-driven fps; frames are stamped with
    qpc_100ns() at grab time (same timebase as WGC's SystemRelativeTime,
    so CaptureClock math is unchanged)."""

    _SRCCOPY_CAPTUREBLT = 0x00CC0020 | 0x40000000

    def __init__(self, win: WindowInfo, fps: int = 30):
        self._win = win
        self._fps = fps
        self._stop = None  # threading.Event while running
        self._thread = None

    def start(self, on_frame, on_stopped) -> None:
        if self._thread is not None:
            return  # already capturing
        import threading
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, args=(on_frame, on_stopped),
            name="gdi-capture", daemon=True)
        self._thread.start()

    def _loop(self, on_frame, on_stopped) -> None:
        import numpy as np
        from sm64_events.replay.clock import qpc_100ns

        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        # PJ64 1.6 is DPI-UNAWARE: its real backing surface is its LOGICAL
        # client size (e.g. 1600x1224 at 150% scaling), while a DPI-aware
        # thread sees the scaled physical size (2400x1836) — BitBlt then
        # copies past the surface into black right/bottom bands (live-
        # measured: content bounds 1602x1226 inside a 2400x1836 frame).
        # Thread-local UNAWARE makes GetClientRect/GetDC agree with the
        # app's actual surface; the physical-size pixels were only DWM
        # upscaling, not real detail, so this also shrinks grabs 2.25x.
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-1))  # UNAWARE
        hwnd = wt.HWND(self._win.hwnd)
        hdc = mdc = bmp = None
        w = h = 0
        buf = None
        period = 1.0 / self._fps
        import time as _time
        next_t = _time.perf_counter()
        try:
            while not self._stop.is_set():
                if not user32.IsWindow(hwnd):
                    log.info("GDI capture: window gone")
                    on_stopped()
                    return
                if user32.IsIconic(hwnd):
                    _time.sleep(period)  # minimized: deliver nothing (gap)
                    next_t = _time.perf_counter()
                    continue
                rect = wt.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                cw, ch = rect.right & ~1, rect.bottom & ~1
                if cw < 16 or ch < 16:
                    _time.sleep(period)
                    continue
                if hdc is None:
                    hdc = user32.GetDC(hwnd)
                    mdc = gdi32.CreateCompatibleDC(hdc)
                if (cw, ch) != (w, h):
                    if bmp:
                        gdi32.DeleteObject(bmp)
                    bmp = gdi32.CreateCompatibleBitmap(hdc, cw, ch)
                    gdi32.SelectObject(mdc, bmp)
                    w, h = cw, ch
                    buf = ctypes.create_string_buffer(w * h * 4)
                    bmi = _BMIH(biSize=ctypes.sizeof(_BMIH), biWidth=w,
                                biHeight=-h, biPlanes=1, biBitCount=32,
                                biCompression=0)
                    self._bmi = bmi
                ts = qpc_100ns()
                if not gdi32.BitBlt(mdc, 0, 0, w, h, hdc, 0, 0,
                                    self._SRCCOPY_CAPTUREBLT):
                    # DC went stale (display change); recreate next pass
                    gdi32.DeleteDC(mdc)
                    user32.ReleaseDC(hwnd, hdc)
                    hdc = mdc = None
                    continue
                gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(self._bmi), 0)
                arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4).copy()
                on_frame(arr, ts)
                next_t += period
                delay = next_t - _time.perf_counter()
                if delay > 0:
                    _time.sleep(delay)
                else:
                    next_t = _time.perf_counter()  # grab+encode overran; resync
        except Exception:
            log.exception("GDI capture loop died")
            on_stopped()
        finally:
            if bmp:
                gdi32.DeleteObject(bmp)
            if mdc:
                gdi32.DeleteDC(mdc)
            if hdc:
                user32.ReleaseDC(hwnd, hdc)

    def stop(self) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=5)
            self._thread = None


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class WgcVideoSource:
    """Monitor capture + per-frame crop to the target window.

    NOTE: NOT usable for PJ64 1.6 (frozen content — see GdiBitBltVideoSource
    docstring); kept for capturing normal flip-model apps."""

    def __init__(self, win: WindowInfo):
        self._win = win
        self._control = None

    def start(self, on_frame, on_stopped) -> None:
        if self._control is not None:
            return  # already capturing; a second start would orphan the first
        from windows_capture import WindowsCapture

        _ensure_dpi_aware()
        hwnd = self._win.hwnd
        mon_index, mon_rect = monitor_geometry(hwnd)
        log.info("monitor capture: index=%d rect=%s (window hwnd=%s)",
                 mon_index, mon_rect, hwnd)

        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            monitor_index=mon_index,
        )

        @capture.event
        def on_frame_arrived(frame, capture_control):
            rect = window_rect(hwnd)
            if rect is None:
                return  # minimized/destroyed: deliver nothing (coverage hole)
            bounds = crop_bounds(frame.width, frame.height, rect, mon_rect)
            if bounds is None:
                return
            x0, y0, x1, y1 = bounds
            on_frame(frame.frame_buffer[y0:y1, x0:x1].copy(), frame.timespan)

        @capture.event
        def on_closed():
            log.info("monitor capture session closed")
            on_stopped()

        self._control = capture.start_free_threaded()

    def stop(self) -> None:
        if self._control is not None:
            try:
                self._control.stop()
            except Exception:
                log.exception("WGC stop failed")
            self._control = None
