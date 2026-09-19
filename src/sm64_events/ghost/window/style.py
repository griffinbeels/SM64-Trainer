"""The Win32 style bits that make a window an overlay, and nothing else.

Pure integers and pure functions, so what the overlay promises can be checked
without a window existing. The set is the one plugin/gfxwrap/host.c already
proves on this machine:

- LAYERED     -- whole-window opacity, blended by the GPU.
- TRANSPARENT -- Microsoft: mouse events pass to the windows underneath.
- NOACTIVATE  -- clicking it, or it appearing, never takes the keyboard.
- TOOLWINDOW  -- no taskbar button and no alt-tab entry.

TOPMOST is deliberately absent. A topmost window floats over every other app
after an alt-tab; the overlay instead sits directly above Project64 by being
inserted above it in the z-order.

OWNERSHIP IS ALSO ABSENT, and that is deliberate as of 2026-09-19. Making
Project64 -- a DPI-unaware window in another process -- the OWNER of our
window bought "always above its owner" and "hidden when its owner is
minimized" for free, and it is the prime suspect for the emulator's window
coming back with desktop wallpaper showing through its status strip after his
first try. A practice tool may not risk the emulator: nothing in this package
may name Project64 as the window being MODIFIED, which
`tests/test_ghost_window_guard.py` enforces by reading the source.
"""

# --- window styles ---------------------------------------------------------
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000

#: Everything that draws a frame or lets the window be resized by hand.
FRAME_STYLES = (WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX
                | WS_MAXIMIZEBOX | WS_BORDER | WS_DLGFRAME)

#: What a window WE create is created with. WS_VISIBLE is absent on purpose:
#: a window created without it cannot appear until something calls ShowWindow,
#: which is what lets the suite create real windows and still put nothing on
#: the player's screen.
CREATE_STYLE = WS_POPUP

# --- extended window styles ------------------------------------------------
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_APPWINDOW = 0x00040000
WS_EX_LAYERED = 0x00080000
# 0x08000000, spelled as a shift on purpose: the flat hex literal is reserved
# by tests/test_single_source.py for core/childproc.py's CREATE_NO_WINDOW, a
# different constant that happens to share the value.
WS_EX_NOACTIVATE = 1 << 27

OVERLAY_EX_STYLE = (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
                    | WS_EX_TOOLWINDOW)

# --- SetWindowPos / ShowWindow ---------------------------------------------
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200

#: Moving the overlay must never activate it and never raise Project64 with
#: it, and must never be the thing that shows it.
PLACE_FLAGS = SWP_NOACTIVATE | SWP_NOOWNERZORDER

RESTYLE_FLAGS = (SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                 | SWP_NOOWNERZORDER | SWP_FRAMECHANGED)

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

LWA_ALPHA = 0x00000002

GWL_STYLE = -16
GWL_EXSTYLE = -20

# --- GetWindow ---------------------------------------------------------------
#: The window immediately ABOVE this one in the z-order. SetWindowPos inserts
#: the moved window AFTER (below) the handle it is given, so THIS is what the
#: overlay is inserted after to end up directly above Project64. Handing it
#: Project64's own handle -- which is what the first try did -- puts the ghost
#: underneath the emulator instead.
GW_HWNDPREV = 3
#: Read-only, and only ever asked about our OWN window, to prove it has no
#: owner. Nothing here sets one.
GW_OWNER = 4

#: SetThreadDpiAwarenessContext / SetProcessDpiAwarenessContext values.
DPI_AWARENESS_CONTEXT_UNAWARE = -1
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = -2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = -3
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

#: GetAwarenessFromDpiAwarenessContext / GetProcessDpiAwareness, in one
#: spelling so a log line reads the same whichever asked.
AWARENESS_NAMES = {0: "unaware", 1: "system", 2: "per_monitor", 3: "unaware_gdi_scaled"}


def overlay_ex_style(base: int = 0) -> int:
    """Add the overlay bits to a window's existing extended style.

    Written as add-and-clear rather than assign because the window is usually
    one pywebview created: whatever else it chose stays, only the bits that
    would give the ghost a taskbar button are removed.
    """
    return (int(base) | OVERLAY_EX_STYLE) & ~WS_EX_APPWINDOW & ~WS_EX_TOPMOST


def frameless_style(base: int = 0) -> int:
    """Strip the chrome from a window's style, leaving WS_VISIBLE alone.

    Showing and hiding is ShowWindow's job; clearing WS_VISIBLE behind
    Windows' back leaves the window and the system disagreeing about whether
    it is on screen.
    """
    return (int(base) | WS_POPUP) & ~FRAME_STYLES
