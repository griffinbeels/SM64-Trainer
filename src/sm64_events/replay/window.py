"""Locate the PJ64 top-level window: hwnd for WGC capture, pid for proc-tap.

pick_window() is pure (tested); enum_windows()/find_window() are the ctypes
boundary (live-verified). Title matching is substring + case-insensitive —
PJ64 1.6 titles itself 'Project64 Version 1.6' (sometimes with the ROM name
appended), so the default config substring 'Project64' matches both."""
import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    visible: bool


def pick_window(windows: list[WindowInfo], title_substring: str) -> WindowInfo | None:
    needle = title_substring.lower()
    for win in windows:
        if win.visible and win.title and needle in win.title.lower():
            return win
    return None


def enum_windows() -> list[WindowInfo]:
    user32 = ctypes.windll.user32
    out: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append(WindowInfo(hwnd=int(hwnd), title=buf.value,
                              pid=pid.value,
                              visible=bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_window(title_substring: str) -> WindowInfo | None:
    return pick_window(enum_windows(), title_substring)
