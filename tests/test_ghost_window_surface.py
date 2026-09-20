"""Native crop uploads without showing a window or touching Project64.

The blend check uses actual GDI over a grey memory bitmap. It distinguishes
opaque black from absent pixels, but does not claim desktop composition proof.
The integration owner must still exercise the browser-to-surface path.
"""
import ctypes
import ctypes.wintypes as wt
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from sm64_events.ghost.window import native, style
from sm64_events.ghost.window.geometry import Rect
from sm64_events.ghost.window.surface import (
    MAX_PIXELS, MAX_SIDE, LayeredSurface, _Blend, _Dib, premultiplied_bgra,
)


def test_straight_rgba_is_premultiplied_and_reordered_without_black_colour_key():
    rgba = bytes([0, 0, 0, 255, 255, 0, 255, 0, 200, 100, 50, 128,
                  20, 100, 240, 255])
    assert premultiplied_bgra(rgba) == bytes([
        0, 0, 0, 255, 0, 0, 0, 0, 25, 50, 100, 128, 240, 100, 20, 255])
    assert rgba[:4] == bytes([0, 0, 0, 255]), "conversion must not modify input"


def test_premultiplication_rejects_partial_pixels():
    with pytest.raises(ValueError, match="complete pixels"):
        premultiplied_bgra(b"abc")


@pytest.mark.parametrize("rect,width,height,alpha,rgba", [
    (Rect(0, 0, 2, 1), 2, 1, 255, b"short"),
    (Rect(0, 0, 1, 1), 2, 1, 255, b"\x00" * 8),
    (Rect(0, 0, 0, 1), 0, 1, 255, b""),
    (Rect(0, 0, 1, 1), 1, 1, -1, b"\x00" * 4),
    (Rect(0, 0, 1, 1), 1, 1, 256, b"\x00" * 4),
    (Rect(0, 0, 1, 1), 1, 1, 0.5, b"\x00" * 4),
    (Rect(0, 0, 1, 1), 1, 1, 255, [0, 0, 0, 255]),
    (Rect(0, 0, MAX_SIDE + 1, 1), MAX_SIDE + 1, 1, 255, b""),
    (Rect(0, 0, MAX_SIDE, MAX_PIXELS // MAX_SIDE + 1),
     MAX_SIDE, MAX_PIXELS // MAX_SIDE + 1, 255, b""),
    (Rect((1 << 31) - 1, 0, 2, 1), 2, 1, 255, b"\x00" * 8),
])
def test_bad_packet_is_refused_before_a_window_is_created(rect, width, height, alpha, rgba):
    surface = LayeredSurface()
    with pytest.raises(ValueError):
        surface.present(rect, rgba, width, height, alpha)
    assert surface.hwnd == 0


@pytest.fixture
def surface():
    if sys.platform != "win32":
        pytest.skip("Native layered-window uploads require Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    context = user32.SetThreadDpiAwarenessContext
    context.argtypes, context.restype = [ctypes.c_void_p], ctypes.c_void_p
    previous = context(style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    assert previous
    value = LayeredSurface("SM64 hidden crop surface test")
    try:
        yield value
    finally:
        value.close()
        context(previous)


def test_empty_surface_cannot_be_shown(surface):
    assert surface.show() is False
    hwnd = surface.ensure()
    assert hwnd and surface.show() is False
    assert not native.is_visible(hwnd)


@pytest.mark.parametrize("alpha", [0, 128, 255])
def test_real_upload_keeps_hidden_window_unowned_unfocused_and_at_exact_rect(surface, alpha):
    before = native.foreground_window()
    rect = Rect(117, 139, 4, 3)
    rgba = bytes([31, 79, 201, 255]) * 12
    assert surface.present(rect, rgba, 4, 3, alpha, insert_after=0)
    hwnd = surface.hwnd
    assert native.get_window_rect(hwnd) == rect
    assert native.get_owner(hwnd) == 0
    assert native.get_ex_style(hwnd) & style.OVERLAY_EX_STYLE == style.OVERLAY_EX_STYLE
    assert not native.get_ex_style(hwnd) & (style.WS_EX_APPWINDOW | style.WS_EX_TOPMOST)
    assert not native.is_visible(hwnd), "present must not show a test window"
    assert native.foreground_window() == before
    surface.pump()
    assert not native.is_visible(hwnd)


def test_upload_replaces_shape_and_pixels_together_then_reuses_same_size_buffer(surface):
    assert surface.present(Rect(90, 110, 2, 2), b"\xff" * 16, 2, 2, 255)
    first_bitmap = surface._dib.bitmap
    rgba = bytes([0, 0, 0, 255, 255, 0, 255, 0, 200, 100, 50, 128])
    assert surface.present(Rect(-23, 42, 3, 1), rgba, 3, 1, 217)
    assert native.get_window_rect(surface.hwnd) == Rect(-23, 42, 3, 1)
    assert ctypes.string_at(surface._dib.bits, 12) == bytes([
        0, 0, 0, 255, 0, 0, 0, 0, 25, 50, 100, 128])
    second_bitmap = surface._dib.bitmap
    assert second_bitmap != first_bitmap
    assert surface.present(Rect(29, 42, 3, 1), rgba, 3, 1, 100)
    assert surface._dib.bitmap == second_bitmap
    assert not native.is_visible(surface.hwnd)


@pytest.mark.parametrize("alpha", [0, 128, 255])
def test_windows_blends_uploaded_pixels_over_grey_with_constant_and_pixel_alpha(surface, alpha):
    rgba = bytes([0, 0, 0, 255, 255, 0, 255, 0, 200, 100, 50, 128,
                  20, 100, 240, 255])
    assert surface.present(Rect(30, 30, 2, 2), rgba, 2, 2, alpha)
    destination = _Dib(2, 2)
    try:
        ctypes.memmove(destination.bits, bytes([128, 128, 128, 255]) * 4, 16)
        library = ctypes.WinDLL("msimg32", use_last_error=True)
        blend = library.AlphaBlend
        blend.restype = wt.BOOL
        blend.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, _Blend]
        assert blend(destination.dc, 0, 0, 2, 2, surface._dib.dc,
                     0, 0, 2, 2, _Blend(0, 0, alpha, 1))
        ctypes.windll.gdi32.GdiFlush()
        result = ctypes.string_at(destination.bits, 16)
        for index, (red, green, blue, pixel_alpha) in enumerate(
                [(0, 0, 0, 255), (255, 0, 255, 0),
                 (200, 100, 50, 128), (20, 100, 240, 255)]):
            effective = pixel_alpha / 255 * alpha / 255
            expected = [round(channel * effective + 128 * (1 - effective))
                        for channel in (blue, green, red)]
            got = list(result[index * 4:index * 4 + 3])
            assert all(abs(a - b) <= 2 for a, b in zip(got, expected, strict=True)), (got, expected)
        assert result[4:7] == b"\x80\x80\x80", "alpha-zero pixels leave grey unchanged"
        if alpha == 255:
            assert result[:3] == b"\x00\x00\x00", "opaque black must stay black"
    finally:
        destination.close()


def test_wrong_thread_cannot_operate_or_destroy_the_window(surface):
    surface.ensure()
    with ThreadPoolExecutor(max_workers=1) as executor:
        for operation in (surface.ensure, surface.hide, surface.close, surface.pump):
            with pytest.raises(RuntimeError, match="creating thread"):
                executor.submit(operation).result()
    assert native.is_window(surface.hwnd)
    assert not native.is_visible(surface.hwnd)


def test_layered_mode_conflict_fails_closed_and_reports_which_api(surface):
    surface.ensure()
    assert native.set_alpha(surface.hwnd, 123)
    assert not surface.present(Rect(30, 30, 2, 2), b"\xff" * 16, 2, 2, 255)
    assert "UpdateLayeredWindow" in surface.last_error
    assert surface.show() is False
    assert not native.is_visible(surface.hwnd)


def test_close_destroys_window_releases_bitmap_and_is_idempotent(surface):
    assert surface.present(Rect(30, 30, 2, 2), b"\xff" * 16, 2, 2, 255)
    hwnd, bitmap = surface.hwnd, surface._dib
    surface.close()
    assert not native.is_window(hwnd)
    assert surface.hwnd == 0 and surface._dib is None
    assert bitmap.dc == bitmap.bitmap == bitmap.previous == 0
    assert not bitmap.bits.value
    surface.close()


def test_hide_requires_a_fresh_upload_before_another_show(surface):
    assert surface.present(Rect(30, 30, 2, 2), b"\xff" * 16, 2, 2, 255)
    surface.hide()
    assert not surface.show()
    assert not native.is_visible(surface.hwnd)
