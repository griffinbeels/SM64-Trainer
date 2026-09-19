"""The user32 calls the overlay makes, each one small enough to read.

Three things this file is careful about.

ARGUMENT TYPES. Every prototype below is declared. ctypes defaults a return
value to C int, which truncates a 64-bit window handle to 32 bits and turns a
working call into a mystery; the handles here are always c_void_p.

WHERE THESE RUN. A user32 query against Project64 serializes with PJ64's own
UI thread, which holds its window lock 110-170 ms once a second (measured in
replay/video.py). Nothing here may be called from a thread with a deadline --
`placer.Placer` runs every one of them on the follower thread, as the capture
path does, and in one explicitly set DPI awareness context.

WHOSE WINDOW. Every WRITING call below takes the OVERLAY's handle. Project64's
handle only ever reaches a reader (`read_game_window`, `window_above`,
`window_pid`). Owning the emulator's window is what this package did until
2026-09-19 and is the prime suspect for the damage he reported to it;
`tests/test_ghost_window_guard.py` reads this package's source and fails if a
writer ever takes the game's handle again.
"""
import ctypes
import ctypes.wintypes as wt
import logging

from sm64_events.ghost.window import style
from sm64_events.ghost.window.geometry import GameWindow, Rect
from sm64_events.replay.window import WindowInfo, enum_windows, find_window

log = logging.getLogger("sm64.ghost")

_api_ready = False


class _POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


def _user32():
    """user32 with every prototype this module uses declared, once."""
    global _api_ready
    user32 = ctypes.windll.user32
    if _api_ready:
        return user32
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
    user32.GetWindow.restype = wt.HWND
    user32.GetWindow.argtypes = [wt.HWND, ctypes.c_uint]
    user32.GetForegroundWindow.restype = wt.HWND
    user32.GetForegroundWindow.argtypes = []
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.SetWindowPos.restype = wt.BOOL
    user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetLayeredWindowAttributes.restype = wt.BOOL
    user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.COLORREF,
                                                  ctypes.c_ubyte, wt.DWORD]
    user32.GetLayeredWindowAttributes.restype = wt.BOOL
    user32.GetLayeredWindowAttributes.argtypes = [
        wt.HWND, ctypes.POINTER(wt.COLORREF), ctypes.POINTER(ctypes.c_ubyte),
        ctypes.POINTER(wt.DWORD)]
    for name in ("IsWindow", "IsIconic", "IsWindowVisible"):
        getattr(user32, name).restype = wt.BOOL
        getattr(user32, name).argtypes = [wt.HWND]
    user32.ShowWindow.restype = wt.BOOL
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    for name in ("GetWindowRect", "GetClientRect"):
        getattr(user32, name).restype = wt.BOOL
        getattr(user32, name).argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.ClientToScreen.restype = wt.BOOL
    user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(_POINT)]
    _api_ready = True
    return user32


def _as_long(value: int) -> int:
    """Win32 style words are unsigned; SetWindowLongW takes a signed LONG."""
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


# --- reading ---------------------------------------------------------------

def get_ex_style(hwnd: int) -> int:
    return _user32().GetWindowLongW(wt.HWND(hwnd), style.GWL_EXSTYLE) & 0xFFFFFFFF


def get_style(hwnd: int) -> int:
    return _user32().GetWindowLongW(wt.HWND(hwnd), style.GWL_STYLE) & 0xFFFFFFFF


def get_owner(hwnd: int) -> int:
    """Our own window's owner, which must always be 0. Read-only: nothing in
    this package sets one, and the guard test makes sure nothing starts."""
    return int(_user32().GetWindow(wt.HWND(hwnd), style.GW_OWNER) or 0)


def get_alpha(hwnd: int) -> int | None:
    """The whole-window alpha Windows currently holds, or None if unset."""
    key, alpha, flags = wt.COLORREF(), ctypes.c_ubyte(), wt.DWORD()
    ok = _user32().GetLayeredWindowAttributes(
        wt.HWND(hwnd), ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags))
    return int(alpha.value) if ok else None


def get_window_rect(hwnd: int) -> Rect | None:
    rect = wt.RECT()
    if not _user32().GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect)):
        return None
    return Rect(x=rect.left, y=rect.top, width=rect.right - rect.left,
                height=rect.bottom - rect.top)


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(_user32().IsWindow(wt.HWND(hwnd)))


def is_visible(hwnd: int) -> bool:
    return bool(hwnd) and bool(_user32().IsWindowVisible(wt.HWND(hwnd)))


def window_above(hwnd: int) -> int:
    """The window sitting immediately ABOVE this one, or 0 for the top.

    This, not the game's own handle, is what the overlay is inserted after:
    SetWindowPos puts the moved window BELOW the handle it is given.
    """
    return int(_user32().GetWindow(wt.HWND(hwnd), style.GW_HWNDPREV) or 0)


def window_pid(hwnd: int) -> int:
    pid = wt.DWORD()
    _user32().GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def foreground_window() -> int:
    return int(_user32().GetForegroundWindow() or 0)


def is_foreground_app(hwnd: int) -> bool:
    """Is the app this window belongs to the one being used right now?

    The WINDOW rather than the app would be wrong: Project64's Usamune menu
    and its file dialogs are separate windows of the same process, and the
    ghost blinking off every time one opens is not what "he is playing" means.
    """
    front = foreground_window()
    if not front or not hwnd:
        return False
    if front == hwnd:
        return True
    return window_pid(front) == window_pid(hwnd) != 0


def read_game_window(hwnd: int) -> GameWindow:
    """One observation of the emulator window, in the caller thread's space."""
    if not is_window(hwnd):
        return GameWindow(found=False, minimized=False, client=None,
                          foreground=False)
    user32 = _user32()
    handle = wt.HWND(hwnd)
    front = is_foreground_app(hwnd)
    if user32.IsIconic(handle):
        return GameWindow(found=True, minimized=True, client=None,
                          foreground=front)
    client = wt.RECT()
    origin = _POINT(0, 0)
    if not user32.GetClientRect(handle, ctypes.byref(client)):
        return GameWindow(found=True, minimized=False, client=None,
                          foreground=front)
    user32.ClientToScreen(handle, ctypes.byref(origin))
    return GameWindow(found=True, minimized=False, foreground=front,
                      client=Rect(x=origin.x, y=origin.y,
                                  width=client.right, height=client.bottom))


def window_dpi(hwnd: int) -> int:
    """The window's DPI, or 0 when Windows will not say (treated as unknown).

    Kept for the diagnostic log ONLY. It used to drive a scale factor, and
    that was the bug: PJ64 is DPI-unaware so this answers 96 for it and 144
    for us at 150 % scaling, which multiplied an already-correct rectangle by
    1.5 (`geometry.py`).
    """
    getter = getattr(ctypes.windll.user32, "GetDpiForWindow", None)
    if getter is None or not hwnd:
        return 0
    getter.restype = ctypes.c_uint
    getter.argtypes = [wt.HWND]
    return int(getter(wt.HWND(hwnd)))


# --- DPI awareness ---------------------------------------------------------

def set_process_per_monitor_aware() -> bool:
    """Ask for this process's DPI world, and say whether Windows agreed.

    It usually does NOT, and the caller must not assume otherwise: python.exe
    ships a manifest that already declares awareness, and pywebview's WinForms
    backend calls SetProcessDPIAware() of its own accord
    (.venv/.../webview/platforms/winforms.py). Either fixes the process before
    this runs, and a process whose awareness is already set refuses this call.
    The thread context below is what the overlay actually relies on, because
    that one can be set whatever the process did.
    """
    setter = getattr(ctypes.windll.user32, "SetProcessDpiAwarenessContext", None)
    if setter is None:
        return False
    setter.restype = wt.BOOL
    setter.argtypes = [ctypes.c_void_p]
    return bool(setter(ctypes.c_void_p(
        style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)))


def set_thread_dpi_context(context: int) -> bool:
    """Pin THIS thread's coordinate space. Returns whether Windows took it."""
    setter = getattr(ctypes.windll.user32, "SetThreadDpiAwarenessContext", None)
    if setter is None:
        return False
    setter.restype = ctypes.c_void_p
    setter.argtypes = [ctypes.c_void_p]
    return bool(setter(ctypes.c_void_p(context)))


def thread_dpi_awareness() -> str:
    """What this thread's coordinate space ACTUALLY is, by name.

    Asked after setting it rather than inferred from the setter's return,
    because "the call succeeded" and "the thread is per-monitor aware" are
    different facts and only the second one places a window correctly.
    """
    user32 = ctypes.windll.user32
    getter = getattr(user32, "GetThreadDpiAwarenessContext", None)
    reader = getattr(user32, "GetAwarenessFromDpiAwarenessContext", None)
    if getter is None or reader is None:
        return "unknown"
    getter.restype = ctypes.c_void_p
    getter.argtypes = []
    reader.restype = ctypes.c_int
    reader.argtypes = [ctypes.c_void_p]
    return style.AWARENESS_NAMES.get(int(reader(getter())), "unknown")


def process_dpi_awareness() -> str:
    """What the PROCESS was fixed at, by name, whoever fixed it."""
    try:
        getter = ctypes.windll.shcore.GetProcessDpiAwareness
    except (AttributeError, OSError):
        return "unknown"
    value = ctypes.c_int()
    getter.restype = ctypes.c_long
    getter.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    if getter(None, ctypes.byref(value)) != 0:
        return "unknown"
    return style.AWARENESS_NAMES.get(int(value.value), "unknown")


# --- writing (the overlay's own window, and nothing else) ------------------

def apply_overlay_styles(hwnd: int) -> int:
    """Make an existing window frameless, click-through and unfocusable.

    Returns the extended style asked for; the caller reads it back to find out
    whether Windows kept every bit.
    """
    user32 = _user32()
    handle = wt.HWND(hwnd)
    wanted_ex = style.overlay_ex_style(get_ex_style(hwnd))
    user32.SetWindowLongW(handle, style.GWL_EXSTYLE, _as_long(wanted_ex))
    user32.SetWindowLongW(handle, style.GWL_STYLE,
                          _as_long(style.frameless_style(get_style(hwnd))))
    user32.SetWindowPos(handle, wt.HWND(0), 0, 0, 0, 0, style.RESTYLE_FLAGS)
    return wanted_ex


def set_alpha(hwnd: int, alpha: int) -> bool:
    return bool(_user32().SetLayeredWindowAttributes(
        wt.HWND(hwnd), wt.COLORREF(0), ctypes.c_ubyte(alpha), wt.DWORD(style.LWA_ALPHA)))


def place(hwnd: int, rect: Rect, insert_after: int | None) -> bool:
    """Move the overlay, and put it directly above `insert_after`.

    `insert_after=None` leaves the z-order untouched; 0 means the top of the
    ordinary band. Callers pass `window_above(game)` -- never the game itself,
    which would put the ghost UNDER the emulator. This never shows or
    activates the window: PLACE_FLAGS carries neither.
    """
    flags = style.PLACE_FLAGS | (style.SWP_NOZORDER if insert_after is None else 0)
    return bool(_user32().SetWindowPos(
        wt.HWND(hwnd), wt.HWND(insert_after or 0), rect.x, rect.y,
        rect.width, rect.height, flags))


def show_no_activate(hwnd: int) -> None:
    _user32().ShowWindow(wt.HWND(hwnd), style.SW_SHOWNOACTIVATE)


def hide(hwnd: int) -> None:
    _user32().ShowWindow(wt.HWND(hwnd), style.SW_HIDE)


# --- finding ---------------------------------------------------------------

def pick_own_window(windows: list[WindowInfo], pid: int, title: str) -> int | None:
    """Our own window by pid and title, VISIBLE OR NOT. Pure.

    replay/window.py's picker requires a visible window, which is right for
    finding the emulator and wrong here: the overlay is created hidden and has
    to be found before it is ever shown.
    """
    needle = title.lower()
    for window in windows:
        if window.pid == pid and needle in (window.title or "").lower():
            return window.hwnd
    return None


def find_overlay_window(pid: int, title: str) -> int | None:
    return pick_own_window(enum_windows(), pid, title)


def find_game_window(title_substring: str) -> int | None:
    """Project64, through the repo's one window lookup."""
    found = find_window(title_substring)
    return found.hwnd if found else None
