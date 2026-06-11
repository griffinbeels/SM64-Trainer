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


class WgcVideoSource:
    """Monitor capture + per-frame crop to the target window."""

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
