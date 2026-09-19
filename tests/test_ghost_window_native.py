"""The window bits, proved against a real window that can never appear.

Every window this file creates is made WITHOUT `WS_VISIBLE` and is never
shown, so the suite cannot put anything on the player's screen (the rule that
came out of "a rainbow square in the corner of my screen", 2026-09-05, which
is why plugin/gfxwrap/host.c carries the same style set). Windows still
applies the styles, the alpha and the placement to an unshown window, so the
readbacks below are real Win32 answers rather than restatements of our own
constants.
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

import pytest

from sm64_events.ghost.window import native, style
from sm64_events.ghost.window.geometry import Rect
from sm64_events.replay.window import WindowInfo

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 window styles")


@pytest.fixture
def hidden_window():
    """A real top-level window with our overlay styles that is never shown.

    "STATIC" is a predefined window class, so this needs no RegisterClass and
    no message loop -- the window exists, answers queries, and is destroyed
    before the test returns.
    """
    made = []

    def make(ex_style=style.OVERLAY_EX_STYLE, w=320, h=240):
        user32 = ctypes.windll.user32
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
            wt.HINSTANCE, wt.LPVOID]
        hwnd = user32.CreateWindowExW(
            ex_style, "STATIC", "sm64 ghost overlay test", style.CREATE_STYLE,
            0, 0, w, h, None, None, None, None)
        assert hwnd, "CreateWindowExW refused the overlay style set"
        made.append(hwnd)
        return int(hwnd)

    yield make
    for hwnd in made:
        ctypes.windll.user32.DestroyWindow(wt.HWND(hwnd))


def test_the_style_set_says_what_the_overlay_promises():
    ex = style.overlay_ex_style()
    assert ex & style.WS_EX_LAYERED, "whole-window opacity needs a layered window"
    assert ex & style.WS_EX_TRANSPARENT, "the mouse must reach the game"
    assert ex & style.WS_EX_NOACTIVATE, "the overlay must never take focus"
    assert ex & style.WS_EX_TOOLWINDOW, "no taskbar button"
    assert not ex & style.WS_EX_APPWINDOW
    assert not ex & style.WS_EX_TOPMOST, (
        "topmost floats over every other app; the overlay sits above PJ64 only")


def test_restyling_somebody_elses_window_keeps_its_own_bits():
    """pywebview creates the window; we add our bits to whatever it chose."""
    theirs = 0x00000100  # WS_EX_WINDOWEDGE, a bit we must not drop
    ex = style.overlay_ex_style(theirs | style.WS_EX_APPWINDOW)
    assert ex & theirs
    assert not ex & style.WS_EX_APPWINDOW


def test_the_frameless_style_drops_the_chrome_and_never_shows_the_window():
    framed = style.WS_CAPTION | style.WS_THICKFRAME | style.WS_SYSMENU | style.WS_VISIBLE
    plain = style.frameless_style(framed)
    assert plain & style.WS_POPUP
    for bit in (style.WS_CAPTION, style.WS_THICKFRAME, style.WS_SYSMENU):
        assert not plain & bit
    assert plain & style.WS_VISIBLE, (
        "showing is ShowWindow's job; clearing the bit here would desync Windows")
    assert not style.CREATE_STYLE & style.WS_VISIBLE, (
        "a window we create must not be able to appear")


def test_placement_flags_can_never_activate_or_show():
    assert style.PLACE_FLAGS & style.SWP_NOACTIVATE
    assert style.PLACE_FLAGS & style.SWP_NOOWNERZORDER, (
        "moving the overlay must not raise Project64 itself")
    assert not style.PLACE_FLAGS & style.SWP_SHOWWINDOW


def test_windows_reads_back_the_extended_styles_we_asked_for(hidden_window):
    hwnd = hidden_window(ex_style=0)
    applied = native.apply_overlay_styles(hwnd)
    readback = native.get_ex_style(hwnd)
    assert readback == applied
    for bit in (style.WS_EX_LAYERED, style.WS_EX_TRANSPARENT,
                style.WS_EX_NOACTIVATE, style.WS_EX_TOOLWINDOW):
        assert readback & bit, f"Windows dropped {bit:#x}"


def test_whole_window_opacity_reaches_the_window(hidden_window):
    hwnd = hidden_window()
    assert native.set_alpha(hwnd, 128) is True
    assert native.get_alpha(hwnd) == 128
    assert native.set_alpha(hwnd, 255) is True
    assert native.get_alpha(hwnd) == 255


def test_the_overlay_never_takes_an_owner(hidden_window):
    """Ownership bought "always above its owner" and "hidden when its owner is
    minimized" for free, and it is what this package did until 2026-09-19.
    Making Project64 -- a DPI-unaware window in another process -- the owner of
    ours is the prime suspect for the emulator's window coming back with
    wallpaper showing through its status strip, so there is no longer any code
    that can set one. The overlay stays above the game by z-order alone."""
    overlay = hidden_window()
    assert native.get_owner(overlay) == 0
    assert not hasattr(native, "set_owner"), (
        "nothing may make Project64 the owner of our window again")


def test_the_z_order_target_is_the_window_above_the_game_not_the_game(hidden_window):
    """SetWindowPos inserts the moved window BELOW the handle it is given, so
    handing it Project64 puts the ghost UNDER the emulator -- which is what
    the first try did. `window_above` is the handle that lands it on top."""
    game = hidden_window()
    above = native.window_above(game)
    assert above != game
    assert isinstance(above, int)
    # A window with nothing above it answers 0, which SetWindowPos reads as
    # HWND_TOP -- still above the game, never below it.
    assert above >= 0


def test_a_window_of_ours_that_nothing_is_looking_at_is_not_the_foreground_app(
        hidden_window):
    """These windows are never shown, so they can never be the foreground --
    which is exactly the state that must hide the ghost."""
    hwnd = hidden_window()
    assert native.is_foreground_app(hwnd) is False
    assert native.is_foreground_app(0) is False
    assert native.window_pid(hwnd) == os.getpid()


def test_a_window_reads_as_away_when_its_app_is_not_in_front(hidden_window):
    hwnd = hidden_window()
    native.place(hwnd, Rect(x=10, y=10, width=640, height=480), insert_after=None)
    assert native.read_game_window(hwnd).foreground is False


def test_placing_the_overlay_moves_it_without_showing_it(hidden_window):
    hwnd = hidden_window()
    rect = Rect(x=37, y=59, width=641, height=483)
    assert native.place(hwnd, rect, insert_after=None) is True
    assert native.get_window_rect(hwnd) == rect
    assert native.is_visible(hwnd) is False, "placement must never show a window"


def test_reading_a_window_reports_its_picture_in_screen_coordinates(hidden_window):
    """The same reader the follower runs against Project64, run against a
    window of ours so the emulator is never touched."""
    hwnd = hidden_window()
    native.place(hwnd, Rect(x=200, y=150, width=640, height=480), insert_after=None)
    seen = native.read_game_window(hwnd)
    assert seen.found is True
    assert seen.minimized is False
    assert seen.client.width == 640 and seen.client.height == 480
    assert (seen.client.x, seen.client.y) == (200, 150), (
        "a popup with no frame has client origin == window origin")


def test_a_destroyed_window_reads_as_gone(hidden_window):
    hwnd = hidden_window()
    ctypes.windll.user32.DestroyWindow(wt.HWND(hwnd))
    seen = native.read_game_window(hwnd)
    assert seen.found is False
    assert seen.client is None


def test_reading_handle_zero_is_gone_not_an_exception():
    assert native.read_game_window(0).found is False


def test_a_thread_can_pin_its_own_coordinate_space_and_say_which_one():
    """The whole fix rests on this: DPI awareness is per THREAD, and a thread
    can set its own whatever the process was fixed at. The readback is the
    part that matters -- "the call succeeded" and "this thread is per-monitor
    aware" are different facts."""
    before = native.thread_dpi_awareness()
    try:
        assert native.set_thread_dpi_context(
            style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) is True
        assert native.thread_dpi_awareness() == "per_monitor"
        assert native.set_thread_dpi_context(
            style.DPI_AWARENESS_CONTEXT_UNAWARE) is True
        assert native.thread_dpi_awareness() == "unaware"
    finally:
        native.set_thread_dpi_context(
            {"unaware": style.DPI_AWARENESS_CONTEXT_UNAWARE,
             "system": style.DPI_AWARENESS_CONTEXT_SYSTEM_AWARE,
             "per_monitor": style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
             }.get(before, style.DPI_AWARENESS_CONTEXT_UNAWARE))


def test_the_process_awareness_is_reported_by_name():
    """Read rather than assumed: the overlay logs what the process ACTUALLY
    is, because `SetProcessDpiAwarenessContext` is refused whenever something
    already fixed it."""
    assert native.process_dpi_awareness() in {
        "unaware", "system", "per_monitor", "unaware_gdi_scaled", "unknown"}


def test_finding_our_own_window_ignores_whether_it_is_visible():
    """The overlay is created hidden, so the visible-only picker in
    replay/window.py cannot find it; this one matches on our pid instead."""
    windows = [
        WindowInfo(hwnd=1, title="Project64 Version 1.6", pid=42, visible=True),
        WindowInfo(hwnd=2, title="SM64 ghost overlay", pid=99, visible=False),
        WindowInfo(hwnd=3, title="SM64 ghost overlay", pid=42, visible=False),
    ]
    assert native.pick_own_window(windows, pid=42, title="ghost overlay") == 3
    assert native.pick_own_window(windows, pid=7, title="ghost overlay") is None
    assert native.pick_own_window(windows, pid=42, title="nothing") is None
