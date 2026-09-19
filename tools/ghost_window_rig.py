"""The overlay's verification rig: every visible property, as a number.

**Why this exists.** The ghost overlay has failed in front of the player twice,
and both times every visible property of the window -- its size, its opacity,
whether it was even above the emulator, whether the page inside it was running
at all -- could only be read off screenshots he had to take himself. This runs
the same machinery against a STAND-IN and prints what it measured.

**What it stands the emulator in for.** A Python-owned window titled like
Project64, created on a thread pinned DPI-UNAWARE, because Project64 1.6 is
DPI-unaware and this machine runs at 150 %: that pairing is what turned a
correct rectangle into a window half again too big on his first try. The
stand-in is never shown. Its client rectangle is read in BOTH coordinate
spaces, so the 1.5 sitting between them is a printed number rather than a
theory.

**What it will not do.** Nothing here may be seen by the player or take his
keyboard. Every window is either never shown, or shown only with
SW_SHOWNOACTIVATE while layered at a PROVEN alpha of 0, click-through,
non-activating, with no taskbar button, over the stand-in's rectangle and
never over Project64. The foreground window is recorded before and after every
step that creates or shows one: Project64 in front means wait, and the
foreground landing on one of ours means stop and say so.

    uv run python tools/ghost_window_rig.py --experiment e1,e4

Experiments:

e1  A pywebview window on the counting page, hidden, then shown at alpha 0.
    Are `requestAnimationFrame` and `requestVideoFrameCallback` ticking in
    each state? A frozen counter that starts climbing when the window is shown
    means the overlay's "hidden until the page reports a picture" gate is a
    deadlock by construction.
e4  The real overlay child (`python -m sm64_events.ghost.window`) over the
    stand-in with `--fixed-rect`: the rectangle asked for, the rectangle read
    back, the stand-in's own, the extended-style bits Windows kept, the alpha,
    the z-order, and the window's own pixels.
picture
    Item 3. The stand-in carries a status-bar child like Project64's, so the
    client rectangle, every child with its class, and the picture that comes
    out of them are printed in both coordinate spaces. Also reads the PLAYER's
    emulator if it is open -- read-only, nothing is touched -- because the
    numbers that matter are the ones inside his own window.
zorder
    Item 4. The child is given the stand-in as its z-order anchor, the
    relation "directly above, nothing between" is asserted, and then the
    stand-in is raised over it repeatedly and the repair is timed -- once with
    the reorder hook and once with `--no-reorder-watch`, so the hook's value
    is a difference rather than a claim. It measures the z-order and the
    repair; it cannot reproduce an activation or say what the player sees.

The counters come back through the window TITLE, read with GetWindowText --
`evaluate_js` is one of the things under suspicion, so the instrument may not
depend on it.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:  # runnable as a plain script
    sys.path.insert(0, str(REPO_ROOT / "src"))

from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402
from sm64_events.ghost.window import diag, native, style  # noqa: E402
from sm64_events.ghost.window.geometry import (  # noqa: E402
    Rect,
    bottom_strip,
    rects_agree,
)
from sm64_events.ghost.window.protocol import alpha_byte  # noqa: E402

PAGE = Path(__file__).resolve().parent / "ghost_rig_page.html"
COLOURKEY_PAGE = Path(__file__).resolve().parent / "ghost_rig_colourkey_page.html"

#: The stand-in's title. Titled like the emulator on purpose -- it is what the
#: overlay's own window lookup would match -- and never shown, so it can never
#: be the foreground window and can never be mistaken for the real thing.
STANDIN_TITLE = "Project64 1.6 - SM64 Trainer rig stand-in"
STANDIN_CLASS = "SM64TrainerGhostRigStandIn"

#: How tall the stand-in's status-bar child is, in its own unaware space.
#: Project64 1.6's is 23 logical pixels on his machine.
STANDIN_STRIP_H = 23

#: A child window, which is what a status bar is.
WS_CHILD = 0x40000000

#: `SetWindowPos`'s "put this at the front of the ordinary band". Used by the
#: rig on ITS OWN stand-in, to provoke the reorder the overlay has to survive.
HWND_TOP = 0
SWP_NOMOVE_NOSIZE_NOACTIVATE = 0x0002 | 0x0001 | 0x0010

#: What the overlay child is told to look for. Deliberately a title nothing on
#: this machine has: with `--fixed-rect` the child never looks a window up, and
#: if that ever changed the rig must still not find the player's emulator.
CHILD_GAME_TITLE = "SM64TrainerRigNoSuchWindow"

OVERLAY_TITLE = "SM64 Trainer ghost overlay"
RIG_HOST_TITLE = "SM64 Trainer rig page host"

#: A foreground window whose title contains this is the player using his
#: emulator, and nothing of ours may happen while it is there.
PROTECTED_TITLE = "project64"

FOREGROUND_WAIT_S = 600.0
FOREGROUND_POLL_S = 5.0

PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
SRCCOPY = 0x00CC0020
#: Included in a screen capture only when the older layered-window path needs
#: it. Not used by default: it forces a desktop redraw, which on some machines
#: is a visible flash, and this rig may not be seen.
CAPTUREBLT = 0x40000000
LWA_COLORKEY = 0x00000001

#: The key colour punched out of the window. Bright and unlike anything in the
#: footage on purpose: a BLACK key would punch holes through the dark parts of
#: Hazy Maze Cave, which is why `host.py` never shipped one. An only-Mario
#: picture owns every background pixel, so the key can be any colour at all.
COLOUR_KEY_RGB = (255, 0, 255)

#: The most the rig will ever let a window be on his screen: 3/255 is about
#: 1.2 %, which moves an unpunched bright region by one to three levels per
#: channel -- readable by a capture, not by a person.
MAX_SHOW_ALPHA = 3
GW_HWNDFIRST = 0
GW_HWNDNEXT = 2
PM_REMOVE = 0x0001

EX_STYLE_BITS = {
    "LAYERED": style.WS_EX_LAYERED,
    "TRANSPARENT": style.WS_EX_TRANSPARENT,
    "NOACTIVATE": style.WS_EX_NOACTIVATE,
    "TOOLWINDOW": style.WS_EX_TOOLWINDOW,
    "TOPMOST": style.WS_EX_TOPMOST,
    "APPWINDOW": style.WS_EX_APPWINDOW,
}

#: The bits the overlay promises. Named here so a report says which ONE went
#: missing rather than printing two hex numbers that differ.
REQUIRED_EX_BITS = ("LAYERED", "TRANSPARENT", "NOACTIVATE", "TOOLWINDOW")


# --------------------------------------------------------------------------
# pure helpers -- everything that can be checked without a window
# --------------------------------------------------------------------------

def style_report(asked: int, kept: int) -> dict:
    """Which extended-style bits Windows actually kept, by name.

    Two hex numbers that differ say a style was refused; they do not say which
    promise was broken. `missing` is the answer that matters: TRANSPARENT gone
    means the mouse stops passing through, NOACTIVATE gone means the window can
    take his keyboard.
    """
    asked, kept = int(asked) & 0xFFFFFFFF, int(kept) & 0xFFFFFFFF
    present = sorted(name for name, bit in EX_STYLE_BITS.items() if kept & bit)
    dropped = sorted(name for name, bit in EX_STYLE_BITS.items()
                     if asked & bit and not kept & bit)
    added = sorted(name for name, bit in EX_STYLE_BITS.items()
                   if kept & bit and not asked & bit)
    missing = [name for name in REQUIRED_EX_BITS if not kept & EX_STYLE_BITS[name]]
    return {"asked": f"{asked:#010x}", "kept": f"{kept:#010x}",
            "kept_bits": present, "dropped_bits": dropped, "added_bits": added,
            "missing_required": missing, "equal": asked == kept}


def parse_tick_title(title: str, prefix: str = "RIG ") -> dict | None:
    """One rig window title -> the numbers in it. None if it is not one.

    Every rig page writes `key=value` pairs behind a short prefix and nothing
    else, so an unparsed title is a page that has not reported yet rather than
    a format to guess at. The prefix says which page is being read: `RIG` for
    the frame counter, `RK` for the colour-key regions.
    """
    text = (title or "").strip()
    if not text.startswith(prefix):
        return None
    out: dict = {}
    for token in text[len(prefix):].split():
        name, _, value = token.partition("=")
        if not name or not _:
            continue
        try:
            out[name] = int(value)
        except ValueError:
            try:
                out[name] = float(value)
            except ValueError:
                out[name] = value
    return out or None


def tick_rates(first: dict | None, last: dict | None) -> dict:
    """Two title readings -> callbacks per second between them.

    Rates rather than totals, because the two states being compared are not
    watched for the same length of time and "60 vs 0" has to survive that.
    """
    if not first or not last:
        return {"measured": False, "reason": "no_samples"}
    span = float(last.get("at", 0.0)) - float(first.get("at", 0.0))
    if span <= 0:
        return {"measured": False, "reason": "no_span"}
    out = {"measured": True, "span_s": round(span, 3)}
    for name in ("raf", "rvfc"):
        before, after = first.get(name), last.get(name)
        if not isinstance(before, int) or not isinstance(after, int):
            out[f"{name}_per_s"] = None
            continue
        out[f"{name}_delta"] = after - before
        out[f"{name}_per_s"] = round((after - before) / span, 2)
    return out


def summarize_pixels(data: bytes, width: int, height: int,
                     *, black_threshold: int = 16) -> dict:
    """A window's pixels reduced to the two numbers that settle the argument.

    `non_black_fraction` is the one that separates his round-1 report -- an
    opaque black rectangle -- from a window with footage in it, without anyone
    having to look at a picture.
    """
    wanted = int(width) * int(height) * 4
    if width <= 0 or height <= 0 or len(data) < wanted:
        return {"ok": False, "reason": "short_buffer",
                "have": len(data), "want": wanted}
    pixels = np.frombuffer(data[:wanted], dtype=np.uint8).reshape(height, width, 4)
    colour = pixels[:, :, :3]
    brightest = colour.max(axis=2)
    return {"ok": True,
            # BGRA off the wire, reported as RGB so it reads like a colour.
            "mean_rgb": [round(float(pixels[:, :, 2].mean()), 2),
                         round(float(pixels[:, :, 1].mean()), 2),
                         round(float(pixels[:, :, 0].mean()), 2)],
            "non_black_fraction": round(float((brightest > black_threshold).mean()), 4),
            "brightest_channel": int(colour.max()),
            "pixel_count": int(width) * int(height)}


def still_block(moved, brightness, width: int, height: int,
                step: int = 40) -> tuple[int, int] | None:
    """The darkest `width` x `height` block in which nothing moved. Pure.

    Given a map of which pixels changed between two screen captures and a map
    of how bright each one is, both as 2-D arrays. Summed-area tables rather
    than a loop over candidates: this searches a whole multi-monitor desktop,
    and his is mostly live video, so the answer is usually a small corner that
    a coarse hunt would walk straight past.

    Returns the block's top-left inside the arrays, or None if every block has
    something moving in it.
    """
    rows, columns = moved.shape
    if height > rows or width > columns:
        return None
    changed = np.pad(np.asarray(moved, dtype=np.int64).cumsum(0).cumsum(1),
                     ((1, 0), (1, 0)))
    light = np.pad(np.asarray(brightness, dtype=np.int64).cumsum(0).cumsum(1),
                   ((1, 0), (1, 0)))

    def blocks(table, tops, lefts):
        return (table[np.ix_(tops + height, lefts + width)]
                - table[np.ix_(tops, lefts + width)]
                - table[np.ix_(tops + height, lefts)]
                + table[np.ix_(tops, lefts)])

    tops = np.arange(0, rows - height + 1, step)
    lefts = np.arange(0, columns - width + 1, step)
    if not tops.size or not lefts.size:
        return None
    motion = blocks(changed, tops, lefts)
    still = motion == 0
    if not still.any():
        return None
    totals = blocks(light, tops, lefts).astype(np.float64)
    totals[~still] = np.inf
    index = int(np.argmin(totals))
    return (int(lefts[index % lefts.size]), int(tops[index // lefts.size]))


def rects_intersect(one: Rect, other: Rect) -> bool:
    return (one.x < other.x + other.width and other.x < one.x + one.width
            and one.y < other.y + other.height and other.y < one.y + one.height)


def scale_between(smaller: Rect | None, larger: Rect | None) -> float | None:
    """The single factor between two rectangles' sizes, or None if they differ.

    Only a UNIFORM factor is a coordinate-space disagreement; anything else is
    two different rectangles and must not be reported as a scale.
    """
    if smaller is None or larger is None:
        return None
    if smaller.width <= 0 or smaller.height <= 0:
        return None
    by_width = larger.width / smaller.width
    by_height = larger.height / smaller.height
    if abs(by_width - by_height) > 0.02:
        return None
    return round(by_width, 4)


def checked_child_opacity(value: float) -> float:
    """The only opacity the rig may start the overlay child with.

    A structural guard rather than a careful caller. The child decides for
    itself when to show its window, so the instrument must never be able to
    hand it an opacity that would put a solid rectangle on the player's
    screen: anything that does not round to an alpha of 0 is refused here,
    before the process exists.
    """
    alpha = alpha_byte(value)
    if alpha != 0:
        raise ValueError(
            f"the rig may only start the overlay child fully transparent; "
            f"opacity {value!r} is alpha {alpha}")
    return float(value)


def colour_ref(rgb: tuple[int, int, int]) -> int:
    """An RGB triple as Windows wants it: COLORREF is 0x00BBGGRR.

    Written out rather than assumed because a colour key that is byte-swapped
    keys the wrong colour and punches nothing, which looks exactly like a
    colour key that does not work.
    """
    red, green, blue = (int(part) & 0xFF for part in rgb)
    return (blue << 16) | (green << 8) | red


def grid_cells(rect: Rect, columns: int = 3, rows: int = 2) -> dict[str, Rect]:
    """The page's regions as rectangles, named r1..rN, left to right.

    The page lays its regions out as a grid filling the viewport exactly, so
    the rig can address each one by arithmetic on the window rectangle instead
    of asking the page for geometry over a title that has no room for it.
    """
    out: dict[str, Rect] = {}
    for row in range(rows):
        for column in range(columns):
            left = rect.x + round(column * rect.width / columns)
            right = rect.x + round((column + 1) * rect.width / columns)
            top = rect.y + round(row * rect.height / rows)
            bottom = rect.y + round((row + 1) * rect.height / rows)
            out[f"r{row * columns + column + 1}"] = Rect(
                x=left, y=top, width=right - left, height=bottom - top)
    return out


def split_halves(cell: Rect) -> dict[str, Rect]:
    """A region's left and right halves.

    The video and the canvas each carry an opaque half and a transparent half,
    so one region answers both "does it reach the screen" and "is it punched
    out" -- and a region that shows nothing at all is told apart from one that
    is see-through everywhere.
    """
    middle = cell.x + cell.width // 2
    return {"left": Rect(x=cell.x, y=cell.y, width=middle - cell.x,
                         height=cell.height),
            "right": Rect(x=middle, y=cell.y,
                          width=cell.x + cell.width - middle,
                          height=cell.height)}


def inset(rect: Rect, fraction: float = 0.4) -> Rect:
    """A smaller box at the centre of a region.

    Sampled instead of the whole region because a region's edge is where a
    rounded grid line, a scrollbar or the neighbour's antialiasing lands, and
    one stray column would move a mean that has to resolve single levels.
    """
    width = max(1, int(rect.width * fraction))
    height = max(1, int(rect.height * fraction))
    return Rect(x=rect.x + (rect.width - width) // 2,
                y=rect.y + (rect.height - height) // 2,
                width=width, height=height)


def region_delta(before: bytes, after: bytes, width: int, height: int,
                 box: Rect, origin: Rect) -> dict:
    """How much one region of the screen moved, per channel.

    `box` is in screen coordinates and `origin` is the captured rectangle, so
    a region is addressed the same way whether it came from the grid or from
    one half of it. The mean is what answers the question -- a punched region
    does not move at all -- and the maximum and the changed fraction are kept
    beside it, because at an alpha of 3 a mean can round to zero while some
    pixels did move.
    """
    wanted = int(width) * int(height) * 4
    if len(before) < wanted or len(after) < wanted or width <= 0 or height <= 0:
        return {"ok": False, "reason": "short_buffer"}
    left, top = box.x - origin.x, box.y - origin.y
    right, bottom = left + box.width, top + box.height
    if left < 0 or top < 0 or right > width or bottom > height:
        return {"ok": False, "reason": "box_outside_capture"}
    shape = (height, width, 4)
    first = np.frombuffer(before[:wanted], dtype=np.uint8).reshape(shape)
    second = np.frombuffer(after[:wanted], dtype=np.uint8).reshape(shape)
    one = first[top:bottom, left:right, :3].astype(np.int16)
    two = second[top:bottom, left:right, :3].astype(np.int16)
    difference = two - one
    moved = np.abs(difference).max(axis=2)
    return {"ok": True,
            # BGRA off the wire, reported as RGB so it reads like a colour.
            "mean_delta_rgb": [round(float(difference[:, :, 2].mean()), 3),
                               round(float(difference[:, :, 1].mean()), 3),
                               round(float(difference[:, :, 0].mean()), 3)],
            "max_abs_delta": int(moved.max()),
            "changed_fraction": round(float((moved > 0).mean()), 4),
            "pixels": int(box.width) * int(box.height)}


def rect_inside(outer: Rect, inner: Rect) -> bool:
    """Is `inner` wholly within `outer`?

    A window clipped by the edge of the desktop comes back a different size
    from the one it was given, which would read as the overlay mis-placing
    itself. The stand-in has to sit somewhere it fits.
    """
    return (inner.x >= outer.x and inner.y >= outer.y
            and inner.x + inner.width <= outer.x + outer.width
            and inner.y + inner.height <= outer.y + outer.height)


def standin_candidates(width: int, height: int, *, count: int = 24,
                       screen: Rect | None = None,
                       blocked: tuple[Rect, ...] = ()) -> list[Rect]:
    """Places to try putting the stand-in, in ITS OWN (unaware) space.

    A list of guesses rather than a computed answer, because the coordinate a
    DPI-unaware window is given is not the coordinate it ends up at -- that is
    the whole subject of this rig. Each one is created, measured in the aware
    space, and kept or moved on; this only decides the ORDER of the tries.

    Walks the whole desktop rather than a short diagonal from the top-left.
    With Project64 maximized -- 2409x1910 of a 2560-wide monitor, which is how
    he actually plays -- every spot on that diagonal was over the emulator and
    the rig had nowhere to stand (measured 2026-09-19, eight refusals and no
    run). Spots overlapping a `blocked` rectangle are dropped up front so the
    walk spends its tries somewhere it might land.
    """
    if screen is None:
        screen = Rect(x=0, y=0, width=1920, height=1080)
    step_x, step_y = width + 40, height + 40
    spots: list[Rect] = []
    y = screen.y + 20
    while y + height <= screen.y + screen.height and len(spots) < count * 4:
        x = screen.x + 20
        while x + width <= screen.x + screen.width and len(spots) < count * 4:
            spot = Rect(x=x, y=y, width=width, height=height)
            if not any(rects_intersect(spot, taken) for taken in blocked):
                spots.append(spot)
            x += step_x
        y += step_y
    return spots[:count]


def zorder_relation(order: list[int], overlay: int, other: int) -> str:
    """Where the overlay sits relative to another window, in words.

    The list is top-first, which is how `GetWindow(GW_HWNDNEXT)` walks it, so a
    SMALLER index is nearer the front.
    """
    if overlay not in order:
        return "overlay_missing"
    if other not in order:
        return "other_missing"
    ours, theirs = order.index(overlay), order.index(other)
    if ours == theirs:
        return "same"
    if ours > theirs:
        return "below"
    return "directly_above" if theirs - ours == 1 else "above"


def foreground_verdict(hwnd: int, title: str, own_hwnds: set[int],
                       own_pids: set[int], pid: int = 0) -> str:
    """Whose window is in front: ours, the emulator's, or somebody else's.

    Ours is checked FIRST. A stand-in titled like the emulator would otherwise
    read as "the player is in his game" at the exact moment it should read as
    "we just stole his focus".
    """
    if not hwnd:
        return "none"
    if hwnd in own_hwnds or (pid and pid in own_pids):
        return "ours"
    if PROTECTED_TITLE in (title or "").lower():
        return "project64"
    return "other"


# --------------------------------------------------------------------------
# the win32 surface
# --------------------------------------------------------------------------

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_void_p, wt.HWND, ctypes.c_uint,
                              wt.WPARAM, wt.LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", _WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


_declared = False


def _win32():
    """user32 and gdi32 with every prototype declared, once.

    Same care as `ghost/window/native.py`: ctypes defaults a return value to C
    int, which truncates a 64-bit handle and turns a working call into a
    mystery.
    """
    global _declared
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    if _declared:
        return user32, gdi32
    user32.GetDC.restype, user32.GetDC.argtypes = wt.HDC, [wt.HWND]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    user32.PrintWindow.restype = wt.BOOL
    user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
    user32.GetWindow.restype, user32.GetWindow.argtypes = wt.HWND, [wt.HWND, wt.UINT]
    user32.GetTopWindow.restype, user32.GetTopWindow.argtypes = wt.HWND, [wt.HWND]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetDesktopWindow.restype = wt.HWND
    user32.SetForegroundWindow.restype = wt.BOOL
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wt.HWND, wt.HMENU,
                                       wt.HINSTANCE, wt.LPVOID]
    user32.DestroyWindow.restype = wt.BOOL
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.DefWindowProcW.restype = ctypes.c_void_p
    user32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.EnumDisplayMonitors.restype = wt.BOOL
    user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p,
                                           _MONITORENUMPROC, wt.LPARAM]
    user32.SetLayeredWindowAttributes.restype = wt.BOOL
    user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.COLORREF,
                                                  ctypes.c_ubyte, wt.DWORD]
    user32.GetLayeredWindowAttributes.restype = wt.BOOL
    user32.GetLayeredWindowAttributes.argtypes = [
        wt.HWND, ctypes.POINTER(wt.COLORREF), ctypes.POINTER(ctypes.c_ubyte),
        ctypes.POINTER(wt.DWORD)]
    gdi32.BitBlt.restype = wt.BOOL
    gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int,
                             wt.DWORD]
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.DeleteObject.restype = wt.BOOL
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.DeleteDC.restype, gdi32.DeleteDC.argtypes = wt.BOOL, [wt.HDC]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                                ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO),
                                wt.UINT]
    _declared = True
    return user32, gdi32


@contextlib.contextmanager
def dpi_context(value: int):
    """Run a block in one DPI coordinate space, then put the old one back.

    Every rectangle in this file says which context it was read in, because a
    rectangle without one is the bug that put a window half again too big over
    his other apps.
    """
    user32 = ctypes.windll.user32
    setter = getattr(user32, "SetThreadDpiAwarenessContext", None)
    if setter is None:
        yield False
        return
    setter.restype = ctypes.c_void_p
    setter.argtypes = [ctypes.c_void_p]
    previous = setter(ctypes.c_void_p(value))
    try:
        yield bool(previous)
    finally:
        if previous:
            setter(ctypes.c_void_p(previous))


def read_title(hwnd: int) -> str:
    """A window's title, including one belonging to another process.

    GetWindowText reads the text Windows has cached for the window rather than
    asking the owning thread for it, so a page host whose UI thread has wedged
    still answers -- with a title that has stopped changing, which is the
    reading the experiment wants.
    """
    user32, _ = _win32()
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(wt.HWND(hwnd), buffer, 512)
    return buffer.value


def zorder_list() -> list[int]:
    """Every top-level window, front to back, hidden ones included.

    Walked rather than enumerated because the ORDER is the measurement: this is
    the only way to say whether the ghost is directly above the window it is
    supposed to be directly above.
    """
    user32, _ = _win32()
    out: list[int] = []
    seen: set[int] = set()
    handle = user32.GetTopWindow(None)
    while handle:
        value = int(handle)
        if value in seen:
            break
        seen.add(value)
        out.append(value)
        handle = user32.GetWindow(wt.HWND(value), GW_HWNDNEXT)
    return out


_MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC,
                                      ctypes.POINTER(wt.RECT), wt.LPARAM)


def monitor_rects() -> list[Rect]:
    """Every physical monitor's rectangle, in the caller's DPI context.

    The virtual screen is the BOUNDING BOX of the monitors, so on a desktop
    whose screens are not aligned it contains rectangles no monitor covers.
    Such a hole captures as pure black and never changes -- perfectly still,
    perfectly dark, and therefore exactly what a hunt for a quiet corner
    settles on (measured 2026-09-19: the probe window was placed at
    (-1880,-147), composited nowhere, and every region read zero).
    """
    user32, _ = _win32()
    found: list[Rect] = []

    def visit(_monitor, _dc, area, _lparam):
        rect = area.contents
        found.append(Rect(x=rect.left, y=rect.top,
                          width=rect.right - rect.left,
                          height=rect.bottom - rect.top))
        return True

    user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(visit), 0)
    return found


def coverage_mask(shape: tuple[int, int], origin: Rect,
                  rects: list[Rect]):
    """A boolean map of which pixels of a capture a monitor actually covers."""
    covered = np.zeros(shape, dtype=bool)
    for rect in rects:
        top = max(0, rect.y - origin.y)
        left = max(0, rect.x - origin.x)
        bottom = min(shape[0], rect.y - origin.y + rect.height)
        right = min(shape[1], rect.x - origin.x + rect.width)
        if bottom > top and right > left:
            covered[top:bottom, left:right] = True
    return covered


def virtual_screen() -> Rect:
    user32, _ = _win32()
    return Rect(x=user32.GetSystemMetrics(76), y=user32.GetSystemMetrics(77),
                width=user32.GetSystemMetrics(78), height=user32.GetSystemMetrics(79))


def capture_window(hwnd: int) -> dict:
    """The window's OWN pixels, reduced to numbers.

    `PW_RENDERFULLCONTENT` is what asks Windows for a window whose content is
    composited rather than painted into its DC. Whether WebView2 honours it is
    itself one of the answers this rig is for, so the raw return value is kept
    beside the numbers: `printed: false` and "every pixel is black" are
    different findings.
    """
    user32, gdi32 = _win32()
    rect = native.get_window_rect(hwnd)
    if rect is None or rect.width <= 0 or rect.height <= 0:
        return {"ok": False, "reason": "no_rect"}
    screen_dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, rect.width, rect.height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        printed = bool(user32.PrintWindow(wt.HWND(hwnd), memory_dc,
                                          PW_RENDERFULLCONTENT))
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = rect.width
        info.bmiHeader.biHeight = -rect.height  # top-down
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        buffer = (ctypes.c_char * (rect.width * rect.height * 4))()
        lines = gdi32.GetDIBits(memory_dc, bitmap, 0, rect.height, buffer,
                                ctypes.byref(info), DIB_RGB_COLORS)
        summary = summarize_pixels(bytes(buffer), rect.width, rect.height)
        summary.update(printed=printed, scanlines=int(lines), rect=rect.as_list())
        return summary
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


def _dib_header(width: int, height: int) -> "_BITMAPINFO":
    info = _BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height  # top-down
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    return info


def capture_screen(rect: Rect, *, capture_layered: bool = False) -> bytes | None:
    """What the SCREEN shows over one rectangle, composed, as raw BGRA.

    Not `PrintWindow`: that asks a window to draw ITSELF, which cannot answer
    whether a pixel of it reached the desktop. Whether the window's pixels are
    absent from the composed result is the entire question here, so the
    reading has to come from the composed result.

    `capture_layered` adds CAPTUREBLT, which is the old way of pulling layered
    windows into a screen blit. It is off by default because it forces a
    desktop redraw that can flash, and nothing this rig does may be seen.
    """
    user32, gdi32 = _win32()
    if rect.width <= 0 or rect.height <= 0:
        return None
    screen_dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, rect.width, rect.height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        flags = SRCCOPY | (CAPTUREBLT if capture_layered else 0)
        if not gdi32.BitBlt(memory_dc, 0, 0, rect.width, rect.height,
                            screen_dc, rect.x, rect.y, flags):
            return None
        info = _dib_header(rect.width, rect.height)
        buffer = (ctypes.c_char * (rect.width * rect.height * 4))()
        if not gdi32.GetDIBits(memory_dc, bitmap, 0, rect.height, buffer,
                               ctypes.byref(info), DIB_RGB_COLORS):
            return None
        return bytes(buffer)
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


def backdrop_report(rect: Rect, *, gap_s: float = 1.0,
                    capture_layered: bool = False) -> dict:
    """Is what is already on screen here holding still?

    Everything downstream is a BEFORE/AFTER difference, so a backdrop that
    repaints by itself -- a clock, a blinking caret, a video -- turns into a
    delta that reads exactly like the window showing through. Two captures a
    second apart have to be byte-identical before anything is shown.
    """
    first = capture_screen(rect, capture_layered=capture_layered)
    if first is None:
        return {"static": False, "reason": "capture_failed"}
    time.sleep(gap_s)
    second = capture_screen(rect, capture_layered=capture_layered)
    if second is None:
        return {"static": False, "reason": "capture_failed"}
    if first == second:
        return {"static": True, "bytes": len(first)}
    moved = region_delta(first, second, rect.width, rect.height, rect, rect)
    return {"static": False, "reason": "backdrop moved between two captures",
            "drift": moved}


def set_colour_key(hwnd: int, rgb: tuple[int, int, int], alpha: int) -> bool:
    """Punch one colour out of the window AND give the rest an alpha.

    `LWA_COLORKEY | LWA_ALPHA` together: every pixel that is exactly the key
    colour is absent from the screen, and everything else is blended at the
    alpha. This is the call `ghost/window/host.py` names and never made -- the
    product's own `native.set_alpha` passes LWA_ALPHA alone -- so the rig makes
    it here rather than changing product code to ask the question.
    """
    user32, _ = _win32()
    return bool(user32.SetLayeredWindowAttributes(
        wt.HWND(hwnd), wt.COLORREF(colour_ref(rgb)), ctypes.c_ubyte(alpha),
        wt.DWORD(LWA_COLORKEY | style.LWA_ALPHA)))


def get_colour_key(hwnd: int) -> dict:
    """The key, the alpha and the flags the window actually kept."""
    user32, _ = _win32()
    key, alpha, flags = wt.COLORREF(), ctypes.c_ubyte(), wt.DWORD()
    ok = user32.GetLayeredWindowAttributes(
        wt.HWND(hwnd), ctypes.byref(key), ctypes.byref(alpha),
        ctypes.byref(flags))
    if not ok:
        return {"ok": False}
    value = int(key.value)
    return {"ok": True, "alpha": int(alpha.value), "flags": int(flags.value),
            "key_colorref": f"{value:#08x}",
            "key_rgb": [value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF],
            "keyed": bool(flags.value & LWA_COLORKEY),
            "alpha_applied": bool(flags.value & style.LWA_ALPHA)}


def read_client_rect(hwnd: int, context: int) -> Rect | None:
    """The window's client area in screen coordinates, in ONE named context."""
    with dpi_context(context):
        return native.read_game_window(hwnd).client


def read_picture_rect(hwnd: int, context: int) -> Rect | None:
    """The part of the window a renderer would draw into, in ONE context."""
    with dpi_context(context):
        return native.read_game_window(hwnd).picture


def read_window_survey(hwnd: int, context: int) -> dict:
    """Client, every child with its class and rectangle, and the picture.

    The exact numbers item 3 turns on, all read in ONE named context by the
    same reader the overlay runs. Read-only: enumerate, measure, never touch.
    """
    with dpi_context(context):
        seen = native.read_game_window(hwnd)
    strip = bottom_strip(seen.client, seen.children)
    return {"found": seen.found, "minimized": seen.minimized,
            "client": seen.client.as_list() if seen.client else None,
            "children": [{"class": child.cls, "rect": child.rect.as_list(),
                          "visible": child.visible, "hwnd": child.hwnd}
                         for child in seen.children],
            "picture": seen.picture.as_list() if seen.picture else None,
            "subtracted": strip.cls if strip else None,
            "picture_equals_client": seen.picture == seen.client}


class StandIn:
    """A window standing in for Project64: unaware, fixed, never shown.

    Created on a thread pinned DPI-UNAWARE because Project64 1.6 is. That is
    not decoration: at 150 % scaling its client rectangle reads one size from
    an unaware thread and half again as much from an aware one, and mixing the
    two is exactly the defect this rig has to be able to catch.
    """

    def __init__(self, rect: Rect, title: str = STANDIN_TITLE,
                 strip_height: int = STANDIN_STRIP_H):
        self.asked = rect
        self.title = title
        self.strip_height = strip_height
        self.hwnd = 0
        self.strip_hwnd = 0
        self.error: str | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._proc = None
        self._thread = threading.Thread(target=self._run, name="rig-standin",
                                        daemon=True)

    def start(self, timeout_s: float = 5.0) -> bool:
        self._thread.start()
        self._ready.wait(timeout_s)
        return bool(self.hwnd)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        user32, _ = _win32()
        with dpi_context(style.DPI_AWARENESS_CONTEXT_UNAWARE):
            try:
                self._create(user32)
            except Exception as error:  # noqa: BLE001 - reported, never raised
                self.error = f"{type(error).__name__}: {error}"
            self._ready.set()
            message = wt.MSG()
            while not self._stop.is_set():
                while user32.PeekMessageW(ctypes.byref(message), None, 0, 0,
                                          PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
                self._stop.wait(0.02)
            if self.hwnd:
                user32.DestroyWindow(wt.HWND(self.hwnd))
                self.hwnd = 0

    def _create(self, user32) -> None:
        module = ctypes.windll.kernel32.GetModuleHandleW(None)
        # The proc is kept on the instance: a WNDPROC that gets collected while
        # Windows still holds the pointer takes the process with it.
        self._proc = _WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
        klass = _WNDCLASSW()
        klass.lpfnWndProc = self._proc
        klass.hInstance = module
        klass.lpszClassName = STANDIN_CLASS
        user32.RegisterClassW(ctypes.byref(klass))  # already registered is fine
        self.hwnd = int(user32.CreateWindowExW(
            style.WS_EX_TOOLWINDOW | style.WS_EX_NOACTIVATE, STANDIN_CLASS,
            self.title,
            # WS_VISIBLE is absent, so nothing can put this on his screen.
            style.CREATE_STYLE,
            self.asked.x, self.asked.y, self.asked.width, self.asked.height,
            None, None, module, None) or 0)
        if not self.hwnd:
            self.error = f"CreateWindowExW failed, GetLastError={ctypes.GetLastError()}"
            return
        if self.strip_height > 0:
            # Project64 1.6 keeps a `msctls_statusbar32` docked along the
            # bottom of its own client area and the renderer draws into what is
            # left. A child of ours in the same place is what lets the picture
            # rule be measured without reading the player's emulator.
            self.strip_hwnd = int(user32.CreateWindowExW(
                0, "STATIC", "", WS_CHILD | style.WS_VISIBLE,
                0, self.asked.height - self.strip_height,
                self.asked.width, self.strip_height,
                wt.HWND(self.hwnd), None, module, None) or 0)


# --------------------------------------------------------------------------
# the safety guard
# --------------------------------------------------------------------------

class FocusStolen(Exception):
    """One of our windows took the foreground. Every later step is off."""


class Blocked(Exception):
    """Project64 is in front and stayed there. The experiment is skipped."""


class FocusGuard:
    """Who has the keyboard, recorded around everything that opens a window."""

    def __init__(self):
        self.own_hwnds: set[int] = set()
        self.own_pids: set[int] = {os.getpid()}
        self.stolen: dict | None = None

    def look(self) -> dict:
        hwnd = native.foreground_window()
        title = read_title(hwnd) if hwnd else ""
        pid = native.window_pid(hwnd) if hwnd else 0
        return {"hwnd": hwnd, "title": title, "pid": pid,
                "verdict": foreground_verdict(hwnd, title, self.own_hwnds,
                                              self.own_pids, pid)}

    def wait_until_clear(self, *, timeout_s: float = FOREGROUND_WAIT_S,
                         poll_s: float = FOREGROUND_POLL_S) -> dict:
        """Hold until the player is not in his emulator. Raises if he stays."""
        deadline = time.monotonic() + timeout_s
        seen = self.look()
        waited = 0.0
        while seen["verdict"] == "project64":
            if time.monotonic() >= deadline:
                raise Blocked(f"Project64 held the foreground for {waited:.0f}s")
            time.sleep(poll_s)
            waited += poll_s
            seen = self.look()
        seen["waited_s"] = round(waited, 1)
        return seen

    def check(self, before: dict) -> dict:
        """After a window step: did the foreground land on one of ours?"""
        after = self.look()
        after["changed"] = after["hwnd"] != before["hwnd"]
        if after["verdict"] != "ours":
            return after
        restored = False
        if before["hwnd"]:
            user32, _ = _win32()
            # Only ever here: the foreground is already wrong and putting his
            # window back is the repair, not a preference.
            restored = bool(user32.SetForegroundWindow(wt.HWND(before["hwnd"])))
        after["restored_previous"] = restored
        self.stolen = after
        raise FocusStolen(json.dumps({"before": before, "after": after}))


# --------------------------------------------------------------------------
# the clip
# --------------------------------------------------------------------------

def ensure_clip(path: Path, *, seconds: int = 4, fps: int = 30,
                width: int = 256, height: int = 192) -> dict:
    """A few seconds of obviously-not-black video, made once and reused.

    Bright and moving on purpose: the pixel summary has to be able to tell
    footage from the black page background behind it, which is the difference
    between his round-1 report and a working overlay.
    """
    if path.exists() and path.stat().st_size > 0:
        return {"path": str(path), "made": False, "bytes": path.stat().st_size}
    import av  # a project dependency; imported here so the pure half runs without it

    path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("libx264", rate=fps)
        stream.width, stream.height = width, height
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "28", "preset": "veryfast"}
        total = seconds * fps
        for index in range(total):
            picture = np.zeros((height, width, 3), dtype=np.uint8)
            picture[:, :] = (30, 90, 200)
            left = int(index / total * (width - 48))
            picture[:, left:left + 48] = (255, 220, 40)
            picture[height // 3:2 * height // 3, :] = np.maximum(
                picture[height // 3:2 * height // 3, :], 120)
            frame = av.VideoFrame.from_ndarray(picture, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return {"path": str(path), "made": True, "bytes": path.stat().st_size}


def ensure_alpha_clip(path: Path) -> dict:
    """Two seconds of VP9-with-alpha WebM: white on the left, nothing on the right.

    Made rather than found, because no clip in this repo has an alpha channel.
    The two halves are what let one region answer both questions at once: the
    opaque half proves the video reached the screen, and the transparent half
    is the thing being asked about.

    The alpha does not ride in the picture: WebM carries it as a second VP9
    stream in each block's BlockAdditional, which is what `alpha_mode=1` in the
    container means. ffmpeg writes it and will not read it back, so the
    encoder's own decode is NOT a check -- only a browser is.
    """
    from sm64_events.core.paths import bundled_ffmpeg

    if path.exists() and path.stat().st_size > 0:
        return {"path": str(path), "made": False, "bytes": path.stat().st_size}
    ffmpeg = bundled_ffmpeg() or shutil.which("ffmpeg")
    if not ffmpeg:
        return {"path": str(path), "made": False, "error": "no ffmpeg found"}
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=white@1:s=64x96:d=2:r=30,format=yuva420p",
        "-f", "lavfi", "-i", "color=c=white@0:s=64x96:d=2:r=30,format=yuva420p",
        "-filter_complex", "[0:v][1:v]hstack=inputs=2,format=yuva420p[v]",
        "-map", "[v]", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-auto-alt-ref", "0", "-b:v", "300k", str(path)]
    finished = subprocess.run(command, capture_output=True, text=True,
                              check=False, **quiet_spawn_kwargs())
    if finished.returncode != 0 or not path.exists():
        return {"path": str(path), "made": False,
                "error": (finished.stderr or "ffmpeg failed")[:400]}
    return {"path": str(path), "made": True, "bytes": path.stat().st_size,
            "ffmpeg": str(ffmpeg)}


def page_url(clip: Path, *, present: str, post: bool) -> str:
    """The counting page with its settings, carried in the FRAGMENT.

    Not the query string: .NET's `Uri` -- which is what pywebview hands the
    WebView2 control -- has no query component for the `file` scheme, so a
    `?` is escaped into the path and Chromium answers with an error page.
    Measured here on 2026-09-19, and the reason the rig's first run reported a
    page that never ticked.
    """
    settings = urlencode({"video": clip.as_uri(), "present": present,
                          "post": "1" if post else "0"})
    return f"{PAGE.as_uri()}#{settings}"


# --------------------------------------------------------------------------
# the page host (this file, re-entered as a child)
# --------------------------------------------------------------------------

HOST_PREFIX = "@RIG "


def _host_say(payload: dict) -> None:
    sys.stdout.write(HOST_PREFIX + json.dumps(payload) + "\n")
    sys.stdout.flush()


class PageHost:
    """A bare pywebview window on a page, driven over stdin. No follower.

    Experiment 1 needs pywebview's own behaviour, not the overlay's: a window
    created exactly as `ghost/window/host.py` creates it, and then nothing
    happening to it except what the experiment asks for. Every command answers
    with what it MEASURED afterwards rather than with whether the call
    returned, because "the call succeeded" has already lied about this window
    once (the alpha of his second try).
    """

    def __init__(self, title: str, url: str, rect: Rect,
                 background: str = "#000000"):
        self.title = title
        self.url = url
        self.rect = rect
        #: What the FORM behind the WebView2 control is painted. Black for the
        #: counting page, the key colour for the colour-key probe: the form is
        #: what shows wherever the page and the control paint nothing, so it
        #: is the surface the key has to punch.
        self.background = background
        self.window = None
        self._hwnd = 0

    # --- what pywebview gave us --------------------------------------------

    def _form(self):
        return getattr(self.window, "native", None)

    def _control(self):
        form = self._form()
        found = getattr(form, "webview", None)
        if found is None:
            found = getattr(getattr(form, "browser", None), "webview", None)
        return found

    def hwnd(self) -> int:
        if not self._hwnd:
            self._hwnd = native.find_overlay_window(os.getpid(), self.title) or 0
        return self._hwnd

    def _on_web_message(self, _sender, event) -> None:
        """The page's counters -> the HWND title, without `evaluate_js`.

        pywebview 6.2.1 never copies `document.title` onto the form (nothing in
        `edgechromium.py` mentions a title at all), so the page posts the same
        text over WebView2's own channel and this puts it where GetWindowText
        can read it from outside the process.
        """
        try:
            payload = json.loads(event.WebMessageAsJson)
            if not (isinstance(payload, list) and len(payload) == 3):
                return
            text = payload[1]
            if isinstance(text, str):
                with contextlib.suppress(ValueError):
                    text = json.loads(text)
            form = self._form()
            if form is not None and isinstance(text, str):
                form.Text = text[:250]
        except Exception as error:  # noqa: BLE001 - a rig channel may not end the host
            _host_say({"event": "message_failed",
                       "detail": f"{type(error).__name__}: {error}"})

    # --- the commands -------------------------------------------------------

    def do_hwnd(self, _message: dict) -> dict:
        return {"event": "hwnd", "hwnd": self.hwnd()}

    def do_bridge(self, _message: dict) -> dict:
        control = self._control()
        attached, detail = False, None
        if control is not None:
            try:
                control.WebMessageReceived += self._on_web_message
                attached = True
            except Exception as error:  # noqa: BLE001
                detail = f"{type(error).__name__}: {error}"
        return {"event": "bridge", "attached": attached,
                "control": control is not None, "detail": detail}

    def do_style(self, _message: dict) -> dict:
        hwnd = self.hwnd()
        asked = native.apply_overlay_styles(hwnd) if hwnd else 0
        return {"event": "style", "asked": asked,
                "kept": native.get_ex_style(hwnd) if hwnd else 0}

    def do_alpha(self, message: dict) -> dict:
        hwnd = self.hwnd()
        value = int(message.get("value", 0))
        taken = native.set_alpha(hwnd, value) if hwnd else False
        return {"event": "alpha", "asked": value, "taken": bool(taken),
                "kept": native.get_alpha(hwnd) if hwnd else None}

    def do_place(self, message: dict) -> dict:
        hwnd = self.hwnd()
        rect = Rect(*message["rect"])
        placed = native.place(hwnd, rect, insert_after=None) if hwnd else False
        got = native.get_window_rect(hwnd) if hwnd else None
        return {"event": "place", "placed": bool(placed),
                "got": got.as_list() if got else None}

    def do_show(self, _message: dict) -> dict:
        """Shown only at an alpha that has been READ BACK and is invisible.

        The rig proves the readback before it ever sends this; the host refuses
        anyway, because an instrument that CAN put a visible window on his
        screen is not a safe instrument whatever its caller intended. The
        ceiling is `MAX_SHOW_ALPHA` -- 0 for every experiment but the colour
        key, which needs a few levels of signal to measure at all.
        """
        hwnd = self.hwnd()
        kept = native.get_alpha(hwnd) if hwnd else None
        allowed = kept is not None and 0 <= kept <= MAX_SHOW_ALPHA
        if allowed:
            native.show_no_activate(hwnd)
        return {"event": "show", "done": allowed, "alpha": kept,
                "ceiling": MAX_SHOW_ALPHA,
                "visible": native.is_visible(hwnd) if hwnd else False}

    def do_background(self, message: dict) -> dict:
        """Stop the WebView2 control painting its own background.

        The same call `WebviewHost.use_transparent_page_background()` makes,
        with one difference that turns out to be the whole difference:
        `DefaultBackgroundColor` reaches `ICoreWebView2Controller2`, which may
        only be touched from the control's OWN UI thread. Set from any other
        thread it throws `InvalidOperationException` wrapping an
        `E_NOINTERFACE` -- measured here 2026-09-19 from the rig's control
        thread. The product makes this call from its FOLLOW thread, so it
        cannot be succeeding there either.

        `Invoke` marshals it, which is what `edgechromium.py` does for every
        other controller call it makes.
        """
        control = self._control()
        if control is None:
            return {"event": "background", "result": "unreachable"}
        direct = bool(message.get("direct"))
        try:
            from System import Func, Object
            from System.Drawing import Color

            def apply():
                control.DefaultBackgroundColor = Color.Transparent
                return None

            if direct:
                apply()
            else:
                control.Invoke(Func[Object](apply))
        except Exception as error:  # noqa: BLE001 - "it refused" is a reading
            return {"event": "background", "direct": direct,
                    "result": f"refused: {type(error).__name__}: {error}"}
        return {"event": "background", "direct": direct,
                "result": "transparent"}

    def do_colourkey(self, message: dict) -> dict:
        """Punch the key colour out of the window and set the rest's alpha."""
        hwnd = self.hwnd()
        rgb = tuple(message.get("rgb") or COLOUR_KEY_RGB)
        alpha = int(message.get("alpha", MAX_SHOW_ALPHA))
        if alpha > MAX_SHOW_ALPHA:
            return {"event": "colourkey", "refused": "above the show ceiling",
                    "asked": alpha, "ceiling": MAX_SHOW_ALPHA}
        taken = set_colour_key(hwnd, rgb, alpha) if hwnd else False
        return {"event": "colourkey", "taken": bool(taken),
                "asked_rgb": list(rgb), "asked_alpha": alpha,
                "kept": get_colour_key(hwnd) if hwnd else None}

    def do_hide(self, _message: dict) -> dict:
        hwnd = self.hwnd()
        if hwnd:
            native.hide(hwnd)
        return {"event": "hide",
                "visible": native.is_visible(hwnd) if hwnd else False}

    def do_probe(self, message: dict) -> dict:
        """Ask the page a question with `evaluate_js`, on a deadline.

        `evaluate_js` is one of the suspects, not the instrument: it waits on a
        semaphore with no timeout, so it is asked on a thread of its own and
        "it never answered" is one of the readings. What it reports -- whether
        the page loaded at all -- is how a silent title gets explained.
        """
        script = message.get("script") or (
            "JSON.stringify({rs: document.readyState, "
            "has: typeof window.ghostStatus, url: location.href, "
            "title: document.title, "
            "bridge: typeof (window.chrome && window.chrome.webview "
            "&& window.chrome.webview.postMessage), "
            "status: window.ghostStatus ? window.ghostStatus() : null})")
        answered: dict = {}

        def ask() -> None:
            try:
                answered["answer"] = self.window.evaluate_js(script)
            except Exception as error:  # noqa: BLE001 - "it raised" is a reading
                answered["error"] = f"{type(error).__name__}: {error}"

        worker = threading.Thread(target=ask, name="rig-probe", daemon=True)
        started = time.monotonic()
        worker.start()
        worker.join(timeout=float(message.get("timeout", 8.0)))
        return {"event": "probe", "answered": not worker.is_alive(),
                "took_s": round(time.monotonic() - started, 3),
                "answer": answered.get("answer"), "error": answered.get("error")}

    def do_source(self, _message: dict) -> dict:
        control = self._control()
        source, core = None, False
        with contextlib.suppress(Exception):
            source = str(control.Source) if control is not None else None
        with contextlib.suppress(Exception):
            core = control is not None and control.CoreWebView2 is not None
        return {"event": "source", "source": source, "core": core,
                "asked_url": self.url}

    @property
    def commands(self) -> dict:
        return {"hwnd": self.do_hwnd, "bridge": self.do_bridge,
                "style": self.do_style, "alpha": self.do_alpha,
                "place": self.do_place, "show": self.do_show,
                "hide": self.do_hide, "probe": self.do_probe,
                "source": self.do_source, "background": self.do_background,
                "colourkey": self.do_colourkey}

    # --- the pipe -----------------------------------------------------------

    def handle(self, message: dict) -> bool:
        """One command. False means the rig asked the host to end."""
        name = message.get("cmd")
        if name == "quit":
            return False
        action = self.commands.get(name)
        if action is None:
            _host_say({"event": "unknown", "cmd": name})
            return True
        try:
            _host_say(action(message))
        except Exception as error:  # noqa: BLE001 - one bad command is not the end
            _host_say({"event": "failed", "cmd": name,
                       "detail": f"{type(error).__name__}: {error}"})
        return True

    def control_loop(self) -> None:
        with dpi_context(style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            for line in sys.stdin:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict) or not self.handle(message):
                    break
        if self.window is not None:
            with contextlib.suppress(Exception):
                self.window.destroy()

    def run(self) -> int:
        import webview

        self.window = webview.create_window(
            self.title, url=self.url, x=self.rect.x, y=self.rect.y,
            width=self.rect.width, height=self.rect.height,
            hidden=True, frameless=True, focus=False, resizable=False,
            easy_drag=False, shadow=False, on_top=False, text_select=False,
            zoomable=False, draggable=False, confirm_close=False,
            background_color=self.background)
        threading.Thread(target=self.control_loop, name="rig-host-control",
                         daemon=True).start()
        _host_say({"event": "creating", "pid": os.getpid()})
        webview.start(private_mode=True)
        return 0


def run_page_host(args) -> int:
    rect = Rect(*args.rect) if args.rect else Rect(x=0, y=0, width=640, height=480)
    return PageHost(args.title, args.url, rect,
                    background=getattr(args, "background", None) or "#000000").run()


class HostProcess:
    """The page host as the rig sees it: one pipe in, named lines out."""

    def __init__(self, url: str, rect: Rect, title: str = RIG_HOST_TITLE,
                 background: str = "#000000"):
        self.title = title
        self.lines: list[str] = []
        self.events: list[dict] = []
        # `--flag=value` throughout, for the reason `child_command` spells out:
        # a rectangle on the monitor left of the primary starts with a minus
        # sign, and argparse reads `--rect -1880,-147,240,160` as a flag with
        # no value. The host then exits 2 before it can say anything, which
        # reads exactly like "the window never came up" (measured 2026-09-19,
        # when the only still corner of his desktop was on the left screen).
        command = [sys.executable, str(Path(__file__).resolve()),
                   "--role=page-host", f"--url={url}", f"--title={title}",
                   f"--background={background}",
                   f"--rect={','.join(str(value) for value in rect.as_list())}"]
        self.command = command
        # stderr joins stdout: a host that dies before it can say anything --
        # a bad argument, a WebView2 that will not start -- otherwise leaves
        # the rig with a broken pipe and no reason for it.
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            cwd=str(REPO_ROOT), **quiet_spawn_kwargs())
        threading.Thread(target=self._read, name="rig-host-reader",
                         daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            text = line.rstrip("\n")
            self.lines.append(text)
            if text.startswith(HOST_PREFIX):
                with contextlib.suppress(ValueError):
                    self.events.append(json.loads(text[len(HOST_PREFIX):]))

    def send(self, **message) -> bool:
        """One command down the pipe. False means the host is not there.

        A dead host is reported rather than raised: it is one of the readings,
        and an experiment that crashes on it writes no report at all.
        """
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            self.events.append({"event": "pipe_closed",
                                "cmd": message.get("cmd"),
                                "exit_code": self.process.poll()})
            return False
        return True

    def ask(self, timeout_s: float = 10.0, **message) -> dict | None:
        """One command, and the next event it produces."""
        seen = len(self.events)
        if not self.send(**message):
            return self.events[-1]
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if len(self.events) > seen:
                return self.events[seen]
            time.sleep(0.05)
        return None

    def wait_for_window(self, timeout_s: float = 45.0) -> int:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            answer = self.ask(timeout_s=3.0, cmd="hwnd")
            if answer and answer.get("hwnd"):
                return int(answer["hwnd"])
            time.sleep(0.4)
        return 0

    def console_lines(self) -> list[str]:
        """What pywebview's own console bridge printed: a second readout of
        the same counters, independent of the window title."""
        return [line for line in self.lines if line.startswith("RIG ")]

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.send(cmd="quit")
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=5)
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)


# --------------------------------------------------------------------------
# watching a window's title
# --------------------------------------------------------------------------

def watch_title(hwnd: int, seconds: float, *, poll_s: float = 0.5) -> dict:
    """Read one window's title for a while and turn it into rates."""
    samples: list[dict] = []
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        title = read_title(hwnd)
        parsed = parse_tick_title(title)
        if parsed is not None:
            parsed["at"] = time.monotonic()
            samples.append(parsed)
        time.sleep(poll_s)
    first = samples[0] if samples else None
    last = samples[-1] if samples else None
    return {"samples": len(samples), "first": first, "last": last,
            "rates": tick_rates(first, last),
            "title_now": read_title(hwnd)}


# --------------------------------------------------------------------------
# experiment 1
# --------------------------------------------------------------------------

def experiment_1(bench: "Bench") -> dict:
    """Hidden, then shown at alpha 0: does the page's compositor ever run?"""
    out: dict = {"name": "e1", "question":
                 "do requestAnimationFrame / requestVideoFrameCallback tick in "
                 "a hidden pywebview window, and do they start when it is shown "
                 "at alpha 0?"}
    out["foreground_before_create"] = bench.guard.wait_until_clear()
    host = HostProcess(page_url(bench.clip, present="onframe", post=True),
                       bench.overlay_rect)
    bench.hosts.append(host)
    # Claimed the instant the process exists, not once its window is found: a
    # foreground steal during CREATION is the thing being measured, and a
    # window we have not identified yet would be judged as somebody else's.
    bench.guard.own_pids.add(host.process.pid)
    try:
        hwnd = host.wait_for_window()
        out["hwnd"] = hwnd
        out["foreground_after_create"] = bench.guard.check(
            out["foreground_before_create"])
        if not hwnd:
            out["skipped"] = "the page host never reported a window handle"
            return out
        bench.guard.own_hwnds.add(hwnd)
        bench.guard.own_pids.add(host.process.pid)
        out["bridge"] = host.ask(cmd="bridge")
        out["source"] = host.ask(cmd="source")
        out["probe_while_hidden"] = host.ask(timeout_s=20.0, cmd="probe")
        out["visible_at_create"] = native.is_visible(hwnd)
        out["ex_style_at_create"] = style_report(style.OVERLAY_EX_STYLE,
                                                 native.get_ex_style(hwnd))
        out["hidden"] = watch_title(hwnd, 10.0)
        out["hidden"]["pixels"] = capture_window(hwnd)
        out["hidden"]["console_lines"] = len(host.console_lines())

        out["style"] = host.ask(cmd="style")
        out["alpha"] = host.ask(cmd="alpha", value=0)
        out["alpha_readback_before_show"] = native.get_alpha(hwnd)
        out["ex_style_before_show"] = style_report(style.OVERLAY_EX_STYLE,
                                                  native.get_ex_style(hwnd))
        if out["alpha_readback_before_show"] != 0:
            out["skipped"] = ("alpha did not read back as 0, so the window was "
                              "never shown")
            return out
        out["place"] = host.ask(cmd="place", rect=bench.overlay_rect.as_list())
        before_show = bench.guard.look()
        out["foreground_before_show"] = before_show
        out["show"] = host.ask(cmd="show")
        out["foreground_after_show"] = bench.guard.check(before_show)
        out["visible_after_show"] = native.is_visible(hwnd)
        shown_from = len(host.console_lines())
        out["shown"] = watch_title(hwnd, 10.0)
        out["shown"]["pixels"] = capture_window(hwnd)
        out["shown"]["console_lines"] = len(host.console_lines()) - shown_from
        out["probe_while_shown"] = host.ask(timeout_s=20.0, cmd="probe")
        host.ask(cmd="hide")
        out["visible_after_hide"] = native.is_visible(hwnd)
        out["verdict"] = _e1_verdict(out)
        return out
    finally:
        host.close()
        out["host_exit_code"] = host.process.poll()
        # Everything the host printed, including whatever pywebview's own
        # console bridge put there: a silent title has to be explainable.
        out["host_lines"] = host.lines[-40:]


def _e1_verdict(out: dict) -> str:
    hidden = out.get("hidden", {}).get("rates", {})
    shown = out.get("shown", {}).get("rates", {})
    if not hidden.get("measured") or not shown.get("measured"):
        return "not_measured"
    frozen = (hidden.get("raf_per_s") or 0) < 1 and (hidden.get("rvfc_per_s") or 0) < 1
    climbing = (shown.get("raf_per_s") or 0) >= 1 or (shown.get("rvfc_per_s") or 0) >= 1
    if frozen and climbing:
        return "deadlock_confirmed: frozen while hidden, ticking once shown"
    if not frozen:
        return "deadlock_refuted: the page ticks while the window is hidden"
    return "both_frozen: the page never ticked in either state"


# --------------------------------------------------------------------------
# experiment 4
# --------------------------------------------------------------------------

def child_command(url: str, rect: Rect, opacity: float, *,
                  anchor_hwnd: int = 0, reorder_watch: bool = True,
                  python: str | None = None) -> list[str]:
    """The exact command line the rig starts the overlay child with.

    **Every value goes in as `--flag=value`, and that is not a style choice.**
    A rectangle on a monitor left of the primary starts with a minus sign, and
    argparse reads `--fixed-rect -2540,-127,640,457` as a flag with no value:
    the child exits 2 before it can write a single line of its own log, which
    reads exactly like "the overlay never made a window" (measured 2026-09-19,
    once Project64 maximized pushed the stand-in onto his left screen).
    """
    return [python or sys.executable, "-m", "sm64_events.ghost.window",
            f"--url={url}", f"--opacity={checked_child_opacity(opacity):g}",
            "--backend=webview", f"--title={CHILD_GAME_TITLE}",
            f"--fixed-rect={','.join(str(value) for value in rect.as_list())}",
            # The rectangle stands in for the PICTURE; the anchor stands in for
            # the window the ghost has to sit directly above. Read, never
            # modified, and absent unless a caller asked for one.
            *([f"--anchor-hwnd={int(anchor_hwnd)}"] if anchor_hwnd else []),
            *([] if reorder_watch else ["--no-reorder-watch"])]


class ChildProcess:
    """The REAL overlay child, started the way the trainer starts it."""

    def __init__(self, url: str, rect: Rect, log_path: Path, opacity: float,
                 *, anchor_hwnd: int = 0, reorder_watch: bool = True):
        self.events: list[dict] = []
        self.lines: list[str] = []
        self.errors: list[str] = []
        command = child_command(url, rect, opacity, anchor_hwnd=anchor_hwnd,
                                reorder_watch=reorder_watch)
        environment = dict(os.environ)
        environment[diag.PATH_ENV] = str(log_path)
        self.command = command
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            cwd=str(REPO_ROOT), env=environment, **quiet_spawn_kwargs())
        threading.Thread(target=self._read, name="rig-child-reader",
                         daemon=True).start()
        # Kept, not discarded. A child that dies inside argparse never reaches
        # its own log, and "the overlay never made a findable window" is what
        # that looked like from here until 2026-09-19.
        threading.Thread(target=self._read_errors, name="rig-child-stderr",
                         daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            text = line.rstrip("\n")
            self.lines.append(text)
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            if isinstance(payload, dict) and "event" in payload:
                self.events.append(payload)

    def _read_errors(self) -> None:
        for line in self.process.stderr:
            self.errors.append(line.rstrip("\n"))

    def send(self, **message) -> None:
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def wait_for_event(self, name: str, timeout_s: float = 30.0,
                       *, after: int = 0) -> dict | None:
        """The next event of this name, counting only ones after `after`.

        `after` is what makes "it placed itself" readable: the child emits one
        `placed` the moment it decides to stay hidden, and reporting THAT as
        the answer to "where did it put itself" is how an instrument agrees
        with a bug.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for payload in list(self.events[after:]):
                if payload.get("event") == name:
                    return payload
            time.sleep(0.1)
        return None

    def seen(self) -> int:
        return len(self.events)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.send(quit=True)
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=5)
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)


def descendants(pid: int) -> set[int]:
    """Every process under one we started.

    `sys.executable` in this project's uv virtual environment is a LAUNCHER:
    the pid `Popen` reports is not the pid that runs the interpreter (measured
    2026-09-19 -- Popen said 65884, the interpreter that answered said 49100).
    So a window matched against the spawned pid is never found, and the rig
    has to look through the whole family.
    """
    family = {int(pid)}
    try:
        import psutil
    except ImportError:
        return family
    with contextlib.suppress(Exception):
        family.update(child.pid
                      for child in psutil.Process(pid).children(recursive=True))
    return family


def overlay_windows() -> list:
    """Every window on this machine titled like an overlay, ours or not."""
    from sm64_events.replay.window import enum_windows

    needle = OVERLAY_TITLE.lower()
    return [window for window in enum_windows()
            if needle in (window.title or "").lower()]


def _wait_for_overlay_window(pid: int, known: set[int],
                             timeout_s: float = 45.0) -> dict:
    """The child's own window: new since we started, and from its family.

    Both halves matter. A sibling session's overlay may already be running on
    this machine, and a window that was there before ours started is not ours
    to measure or to move.
    """
    deadline = time.monotonic() + timeout_s
    seen: list = []
    while time.monotonic() < deadline:
        family = descendants(pid)
        seen = overlay_windows()
        fresh = [window for window in seen
                 if window.hwnd not in known and window.pid in family]
        if fresh:
            return {"hwnd": int(fresh[0].hwnd), "pid": int(fresh[0].pid),
                    "spawned_pid": int(pid), "family": sorted(family),
                    "candidates": len(seen), "ambiguous": len(fresh) > 1}
        time.sleep(0.4)
    return {"hwnd": 0, "spawned_pid": int(pid),
            "family": sorted(descendants(pid)),
            "candidates": len(seen),
            "titled_elsewhere": [{"hwnd": int(window.hwnd), "pid": int(window.pid)}
                                 for window in seen]}


def _wait_for_alpha(hwnd: int, wanted: int, timeout_s: float = 10.0) -> int | None:
    deadline = time.monotonic() + timeout_s
    kept = native.get_alpha(hwnd)
    while time.monotonic() < deadline and kept != wanted:
        time.sleep(0.2)
        kept = native.get_alpha(hwnd)
    return kept


def _e4_bring_up(bench: "Bench", child: "ChildProcess", out: dict,
                 known: set[int]) -> int:
    """Start the child and find its window, recording the foreground around it."""
    out["ready"] = child.wait_for_event("ready")
    out["dpi"] = child.wait_for_event("dpi")
    found = _wait_for_overlay_window(child.process.pid, known)
    out["window_search"] = found
    hwnd = found["hwnd"]
    out["hwnd"] = hwnd
    out["foreground_after_create"] = bench.guard.check(
        out["foreground_before_create"])
    if not hwnd:
        out["skipped"] = "the overlay child never created a findable window"
        return 0
    bench.guard.own_hwnds.add(hwnd)
    bench.guard.own_pids.update(found["family"])
    bench.guard.own_pids.add(found["pid"])
    # One tick is enough for the follower to style the window; the alpha it
    # asked for on that same tick, before the styles, is the thing being
    # measured here.
    time.sleep(1.5)
    out["ex_style_after_first_tick"] = style_report(
        style.OVERLAY_EX_STYLE, native.get_ex_style(hwnd))
    out["alpha_after_first_tick"] = native.get_alpha(hwnd)
    out["visible_before_show"] = native.is_visible(hwnd)
    return hwnd


def _e4_show(bench: "Bench", child: "ChildProcess", hwnd: int, out: dict) -> bool:
    """Get the alpha to a proved 0, then ask for the window. False if refused.

    The child cannot get there by itself, and that is a finding rather than a
    workaround. `FollowLoop.tick` runs `settle()` -- and therefore
    `ensure_alpha` -- before `apply()`, which is where the window is made
    WS_EX_LAYERED; an alpha set on a window that is not layered fails, and
    `Placer.ensure_alpha` stores the value as applied whether or not the call
    succeeded, so it never asks again. The rig therefore lays the window over
    itself and lets the child apply the alpha to a window that can take one.
    """
    out["alpha_before_rig_styles"] = native.get_alpha(hwnd)
    out["rig_applied_styles"] = style_report(native.apply_overlay_styles(hwnd),
                                             native.get_ex_style(hwnd))
    # The rig sets the alpha itself, on a window it has just made layered. The
    # child cannot: it was started fully transparent, so `ensure_alpha` has
    # already stored 0 as applied -- whether or not that first call succeeded
    # -- and every later request for 0 is skipped as a repeat.
    out["rig_set_alpha"] = native.set_alpha(hwnd, 0)
    out["alpha_after_rig_set"] = native.get_alpha(hwnd)
    # Asked anyway, so the log says what the child's own path does with it.
    child.send(opacity=0.0)
    out["alpha_readback_after_request"] = _wait_for_alpha(hwnd, 0)
    if out["alpha_readback_after_request"] != 0:
        out["skipped"] = ("alpha never read back as 0, so the child was never "
                          "asked to show its window")
        return False
    before_show = bench.guard.look()
    out["foreground_before_show"] = before_show
    marker = child.seen()
    child.send(visible=True)
    out["placed_event"] = child.wait_for_event("placed", timeout_s=15.0,
                                               after=marker)
    time.sleep(1.0)
    out["foreground_after_show"] = bench.guard.check(before_show)
    out["visible_after_show"] = native.is_visible(hwnd)
    return True


def _e4_measure(bench: "Bench", hwnd: int, log_path: Path, out: dict) -> None:
    """Rectangle, styles, alpha, owner, z-order and pixels, all read back."""
    with dpi_context(style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
        read_back = native.get_window_rect(hwnd)
    standin_aware = bench.standin_picture_aware
    out["rect_read_back_aware"] = read_back.as_list() if read_back else None
    out["standin_client_aware"] = (bench.standin_client_aware.as_list()
                                   if bench.standin_client_aware else None)
    out["standin_picture_aware"] = (standin_aware.as_list()
                                    if standin_aware else None)
    out["rect_equals_asked"] = rects_agree(bench.overlay_rect, read_back)
    out["rect_equals_standin_picture"] = (
        standin_aware is not None and rects_agree(standin_aware, read_back))
    out["covers_the_strip"] = (
        bench.standin_client_aware is not None and read_back is not None
        and rects_agree(bench.standin_client_aware, read_back))
    out["scale_vs_asked"] = scale_between(bench.overlay_rect, read_back)
    out["ex_style_when_shown"] = style_report(style.OVERLAY_EX_STYLE,
                                              native.get_ex_style(hwnd))
    out["alpha_when_shown"] = native.get_alpha(hwnd)
    out["owner"] = native.get_owner(hwnd)
    order = zorder_list()
    out["zorder"] = {
        "vs_standin": zorder_relation(order, hwnd, bench.standin.hwnd),
        "overlay_index": order.index(hwnd) if hwnd in order else None,
        "standin_index": (order.index(bench.standin.hwnd)
                          if bench.standin.hwnd in order else None),
        "windows": len(order),
        "window_above_overlay": native.window_above(hwnd)}
    out["pixels"] = capture_window(hwnd)
    out["page_status_rows"] = [row for row in diag.read(log_path)
                               if row.get("event") == "page_status"]


def _reassert_gaps(rounds: list[dict], rows: list[dict]) -> dict:
    """Milliseconds from the rig raising the stand-in to the child's own row.

    Both stamps are UTC from the same machine clock, so this is the number the
    player would feel: how long the ghost was behind the game.
    """
    reasserted = sorted(datetime.fromisoformat(row["utc"]) for row in rows
                        if row.get("event") == "z_reasserted")
    gaps: list[float] = []
    unmatched = 0
    for entry in rounds:
        raised = datetime.fromisoformat(entry["raised_at"])
        after = [stamp for stamp in reasserted if stamp >= raised]
        if not after:
            unmatched += 1
            continue
        gaps.append(round((after[0] - raised).total_seconds() * 1000.0, 1))
    if not gaps:
        return {"rows": len(reasserted), "unmatched": unmatched,
                "note": "the child logged no z_reasserted after any raise"}
    ordered = sorted(gaps)
    return {"rows": len(reasserted), "unmatched": unmatched, "each_ms": gaps,
            "min_ms": ordered[0], "median_ms": ordered[len(ordered) // 2],
            "max_ms": ordered[-1]}


def _e4_finish(child: "ChildProcess", log_path: Path, out: dict) -> None:
    child.close()
    out["child_exit_code"] = child.process.poll()
    out["child_command"] = child.command
    if child.process.poll():
        out["child_stderr"] = child.errors[-12:]
    rows = diag.read(log_path)
    out["diag_events"] = [row.get("event") for row in rows]
    out["diag_first_alpha"] = next(
        (row for row in rows if row.get("event") == "alpha"), None)
    out["diag_first_styles"] = next(
        (row for row in rows if row.get("event") == "styles"), None)
    out["diag_placed"] = [row for row in rows
                          if row.get("event") in ("placed", "place_mismatch")]
    out["ordering"] = _ordering_finding(rows)
    if out.get("rounds"):
        out["reassert_gap"] = _reassert_gaps(out["rounds"], rows)


def experiment_4(bench: "Bench") -> dict:
    """The real child over the stand-in: rectangle, styles, alpha, z-order."""
    out: dict = {"name": "e4", "question":
                 "does the overlay child place itself exactly where it was "
                 "told, at 150 % scaling, and keep every style it asked for?"}
    out["standin"] = bench.standin_report
    out["rect_asked"] = bench.overlay_rect.as_list()
    out["foreground_before_create"] = bench.guard.wait_until_clear()
    log_path = bench.scratch / "e4_ghost_window.jsonl"
    with contextlib.suppress(OSError):
        log_path.unlink()
    # Recorded BEFORE the spawn: a sibling session's overlay may already be on
    # this machine, and its window must not be mistaken for the one under test.
    known = {int(window.hwnd) for window in overlay_windows()}
    out["overlay_windows_before"] = sorted(known)
    # present=always opens the child's own PAGE gate on purpose: this
    # experiment is about geometry, and the gate is experiment 1's subject.
    # It does NOT make the window appear -- `--visible` is absent, so the
    # follower answers `hidden_by_request` whatever the page says -- and the
    # opacity is 0, so the child could not show anything solid even if it did.
    child = ChildProcess(page_url(bench.clip, present="always", post=False),
                         bench.overlay_rect, log_path, opacity=0.0)
    bench.children.append(child)
    bench.guard.own_pids.add(child.process.pid)
    try:
        hwnd = _e4_bring_up(bench, child, out, known)
        if hwnd and _e4_show(bench, child, hwnd, out):
            _e4_measure(bench, hwnd, log_path, out)
            child.send(visible=False)
            time.sleep(0.5)
            out["visible_after_hide"] = native.is_visible(hwnd)
        return out
    finally:
        _e4_finish(child, log_path, out)


def _ordering_finding(rows: list[dict]) -> dict:
    """Did the child set an alpha before the window was layered? From the log."""
    events = [row.get("event") for row in rows]
    if "alpha" not in events or "styles" not in events:
        return {"decided": False, "reason": "the child logged no alpha/styles pair"}
    first_alpha, first_styles = events.index("alpha"), events.index("styles")
    alpha_row = rows[first_alpha]
    return {"decided": True,
            "alpha_before_styles": first_alpha < first_styles,
            "first_alpha_asked": alpha_row.get("asked"),
            "first_alpha_kept": alpha_row.get("got"),
            "first_alpha_accepted": alpha_row.get("accepted"),
            "gap_rows": abs(first_styles - first_alpha)}



# --------------------------------------------------------------------------
# item 3: the picture is the client MINUS the emulator's own chrome
# --------------------------------------------------------------------------

def experiment_picture(bench: "Bench") -> dict:
    """Does the reader find the status bar and take it off the picture?

    Creates nothing and shows nothing: the stand-in already exists and carries
    a status-bar child, and Project64 -- if the player has it open -- is read
    with the same read-only survey and never touched.
    """
    out: dict = {"name": "picture", "question":
                 "is the rectangle the overlay is given the part of the "
                 "client area the renderer draws into?"}
    standin = bench.standin
    out["standin_strip_height_unaware"] = standin.strip_height
    out["standin_strip_hwnd"] = standin.strip_hwnd
    for name, context in (("unaware", style.DPI_AWARENESS_CONTEXT_UNAWARE),
                          ("per_monitor_aware",
                           style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
        out["standin_" + name] = read_window_survey(standin.hwnd, context)
    aware = out["standin_per_monitor_aware"]
    client, picture = aware["client"], aware["picture"]
    out["verdict"] = _picture_verdict(aware, client, picture)
    out["shortfall_px"] = (None if not client or not picture
                           else client[3] - picture[3])
    # Read-only, and the whole point of item 3: what the PLAYER's emulator
    # actually contains. Never modified, messaged or reparented.
    out["project64"] = _project64_survey()
    return out


def _picture_verdict(aware: dict, client, picture) -> str:
    if not client or not picture:
        return "the stand-in reported no rectangle"
    if aware["subtracted"] is None:
        return "no strip was found, so the picture is the whole client"
    if picture[3] >= client[3]:
        return "a strip was found but nothing came off the picture"
    return "picture is client minus the " + aware["subtracted"] + " strip"


def _project64_survey() -> dict:
    """The player's emulator, read only, in the context the placer uses."""
    from sm64_events.replay.window import enum_windows

    out: dict = {"windows": []}
    for window in enum_windows():
        if PROTECTED_TITLE not in (window.title or "").lower():
            continue
        if not window.visible:
            continue
        survey = read_window_survey(
            window.hwnd, style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        survey["title"] = window.title
        survey["unaware"] = read_window_survey(
            window.hwnd, style.DPI_AWARENESS_CONTEXT_UNAWARE)
        out["windows"].append(survey)
    if not out["windows"]:
        out["note"] = "Project64 was not running; nothing was read"
    return out


# --------------------------------------------------------------------------
# item 4: directly above the game, and back there promptly
# --------------------------------------------------------------------------

def _raise_standin(hwnd: int) -> bool:
    """Put the rig's OWN stand-in at the front of the ordinary band.

    This is the reorder the overlay has to survive, generated without touching
    focus: `SetForegroundWindow` on a never-shown window does nothing, and
    taking the player's foreground is forbidden anyway.
    """
    user32, _ = _win32()
    return bool(user32.SetWindowPos(wt.HWND(hwnd), wt.HWND(HWND_TOP), 0, 0, 0,
                                    0, SWP_NOMOVE_NOSIZE_NOACTIVATE))


def directly_above(overlay: int, other: int) -> bool:
    """Is the overlay the window immediately above `other`? ONE call.

    `zorder_relation` over `zorder_list` answers the same question and says
    more when it is wrong, but it walks every top-level window on the machine
    -- 631 of them here, ~90 ms a pass -- which is LONGER THAN THE REPAIR being
    timed. The first run of this measurement reported ~92 ms for both the
    hooked and the unhooked overlay, and the only thing separating them was a
    flag on the first read. An instrument slower than its subject measures
    itself, so the poll below asks the cheap question and the expensive one is
    kept for the verdict at each end.
    """
    return int(native.window_above(other) or 0) == overlay


def _repair_latency(overlay: int, standin: int, *,
                    timeout_s: float = 2.0) -> dict:
    """Raise the stand-in, then time the ghost getting back above it."""
    before = zorder_relation(zorder_list(), overlay, standin)
    started = time.perf_counter()
    # Stamped in the same clock the child writes its log in, so the repair can
    # be timed from the child's own `z_reasserted` row. The rig's own poll
    # cannot do it: `SetWindowPos` on a window of ours delivers messages to
    # every thread that owns one, and the overlay's belongs to a process busy
    # inside WebView2, so the call alone takes 70-100 ms here.
    raised_at = datetime.now(timezone.utc)
    raised = _raise_standin(standin)
    buried = directly_above(overlay, standin)
    deadline = started + timeout_s
    repaired = buried
    while not repaired and time.perf_counter() < deadline:
        repaired = directly_above(overlay, standin)
    return {"before": before, "raise_ok": raised,
            "raised_at": raised_at.isoformat(),
            "buried_at_first_read": not buried,
            "after": zorder_relation(zorder_list(), overlay, standin),
            "ms": round((time.perf_counter() - started) * 1000.0, 1),
            "repaired": repaired}


def _probe_cost(overlay: int, standin: int, *, samples: int = 200) -> dict:
    """What one poll costs, so a latency can be read against its own floor."""
    started = time.perf_counter()
    for _ in range(samples):
        directly_above(overlay, standin)
    cheap = (time.perf_counter() - started) * 1000.0 / samples
    started = time.perf_counter()
    zorder_relation(zorder_list(), overlay, standin)
    return {"one_poll_ms": round(cheap, 3),
            "full_walk_ms": round((time.perf_counter() - started) * 1000.0, 1)}


def _zorder_run(bench: "Bench", *, watch: bool, rounds: int) -> dict:
    """One child, asserted above the stand-in, then knocked down `rounds` times."""
    out: dict = {"reorder_watch": watch}
    name = "hook" if watch else "tick"
    log_path = bench.scratch / ("z_" + name + "_ghost_window.jsonl")
    with contextlib.suppress(OSError):
        log_path.unlink()
    known = {int(window.hwnd) for window in overlay_windows()}
    out["foreground_before_create"] = bench.guard.wait_until_clear()
    child = ChildProcess(page_url(bench.clip, present="always", post=False),
                         bench.overlay_rect, log_path, opacity=0.0,
                         anchor_hwnd=bench.standin.hwnd,
                         reorder_watch=watch)
    bench.children.append(child)
    bench.guard.own_pids.add(child.process.pid)
    try:
        hwnd = _e4_bring_up(bench, child, out, known)
        if not hwnd or not _e4_show(bench, child, hwnd, out):
            return out
        out["ex_style_topmost"] = bool(native.get_ex_style(hwnd)
                                       & style.WS_EX_TOPMOST)
        out["owner"] = native.get_owner(hwnd)
        out["relation_when_placed"] = zorder_relation(
            zorder_list(), hwnd, bench.standin.hwnd)
        out["window_above_overlay"] = native.window_above(hwnd)
        out["probe_cost_ms"] = _probe_cost(hwnd, bench.standin.hwnd)
        rounds_out = []
        for _ in range(rounds):
            rounds_out.append(_repair_latency(hwnd, bench.standin.hwnd))
            # Let the follower settle before knocking it down again, or the
            # next round measures a loop still finishing the last one.
            time.sleep(0.3)
        out["rounds"] = rounds_out
        repaired = sorted(row["ms"] for row in out["rounds"] if row["repaired"])
        out["repaired_of"] = [len(repaired), rounds]
        if repaired:
            out["ms"] = {"min": repaired[0],
                         "median": repaired[len(repaired) // 2],
                         "max": repaired[-1]}
        out["foreground_at_end"] = bench.guard.check(
            out["foreground_before_create"])
        return out
    finally:
        _e4_finish(child, log_path, out)


def experiment_zorder(bench: "Bench", rounds: int = 8) -> dict:
    """Is the ghost directly above the game, and how fast does it get back?

    **What a never-shown stand-in CAN answer.** Windows keeps one z-order for
    every top-level window, shown or not, so `SetWindowPos` moves a hidden
    window in it and `GetWindow(GW_HWNDPREV)` reads the result. The relation
    "directly above, with nothing between" and the time the overlay takes to
    restore it after something is raised in front are therefore real
    measurements rather than stand-ins for one.

    **What it CANNOT.** An activation. `SetForegroundWindow` on a window that
    was never shown does nothing, and taking the player's foreground is
    forbidden, so his actual gesture -- clicking Project64, which raises the
    emulator AND fires `EVENT_SYSTEM_FOREGROUND` -- is reproduced here by its
    z-order half alone, through `EVENT_OBJECT_REORDER`. It also cannot say
    whether the ghost LOOKS right over a DirectX surface. Those stay his.
    """
    out: dict = {"name": "zorder", "question":
                 "does the overlay sit directly above the game window, never "
                 "topmost, and how long is it buried when something is "
                 "raised over it?",
                 "standin": bench.standin_report["hwnd"],
                 "measures": "z-order relation and repair latency; not an "
                             "activation, and not what the player sees"}
    out["with_hook"] = _zorder_run(bench, watch=True, rounds=rounds)
    out["tick_only"] = _zorder_run(bench, watch=False, rounds=rounds)
    hook = out["with_hook"].get("reassert_gap") or {}
    tick = out["tick_only"].get("reassert_gap") or {}
    if "median_ms" in hook and "median_ms" in tick:
        out["median_saved_ms"] = round(tick["median_ms"] - hook["median_ms"], 1)
    out["buried_at_first_read"] = {
        key: sum(1 for row in out[key].get("rounds") or []
                 if row["buried_at_first_read"])
        for key in ("with_hook", "tick_only")}
    return out


# --------------------------------------------------------------------------
# the bench
# --------------------------------------------------------------------------

@dataclass
class Bench:
    scratch: Path
    clip: Path
    guard: FocusGuard
    standin: StandIn
    standin_report: dict
    standin_client_aware: Rect | None
    #: What the overlay is asked to cover: the stand-in's client MINUS its
    #: status-bar child, exactly as the real follower covers Project64's
    #: picture rather than its client area.
    standin_picture_aware: Rect | None
    overlay_rect: Rect
    hosts: list
    children: list
    #: A few seconds of VP9-with-alpha WebM: half opaque white, half fully
    #: transparent. Only the colour-key probe uses it, and only to ask whether
    #: a transparent video pixel can be absent from the screen.
    alpha_clip: Path | None = None


def place_standin(width: int, height: int) -> tuple[StandIn, dict]:
    """Create the stand-in somewhere it fits and nothing of his is.

    Created, then MEASURED, then kept or moved on. The coordinate a DPI-unaware
    window is handed is not the coordinate it lands at, so asking where it
    actually went is the only honest way to promise it is clear of Project64
    and not clipped by the edge of the desktop.
    """
    with dpi_context(style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
        screen = virtual_screen()
    blocked = tuple(_emulator_rects())
    tried: list[dict] = []
    for spot in standin_candidates(width, height, screen=screen, blocked=blocked):
        standin = StandIn(spot)
        standin.start()
        aware = read_client_rect(standin.hwnd,
                                 style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        unaware = read_client_rect(standin.hwnd,
                                   style.DPI_AWARENESS_CONTEXT_UNAWARE)
        refusal = None
        if aware is None:
            refusal = "the stand-in reported no client rectangle"
        elif not rect_inside(screen, aware):
            refusal = "clipped by the edge of the desktop"
        elif any(rects_intersect(aware, taken) for taken in blocked):
            refusal = "over a Project64 window"
        if refusal is None:
            picture = read_picture_rect(
                standin.hwnd, style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
            return standin, {
                "hwnd": standin.hwnd, "error": standin.error,
                "title": standin.title,
                "shown": native.is_visible(standin.hwnd),
                "asked_unaware": spot.as_list(),
                "client_unaware": unaware.as_list() if unaware else None,
                "client_aware": aware.as_list() if aware else None,
                "picture_aware": picture.as_list() if picture else None,
                "strip_hwnd": standin.strip_hwnd,
                "aware_over_unaware": scale_between(unaware, aware),
                "screen_aware": screen.as_list(),
                "emulator_rects": [rect.as_list() for rect in blocked],
                "rejected": tried}
        tried.append({"asked_unaware": spot.as_list(), "reason": refusal})
        standin.close()
    raise Blocked(f"no room for the stand-in clear of Project64 on "
                  f"{screen.as_list()}: {tried}")


def build_bench(scratch: Path, width: int, height: int) -> Bench:
    guard = FocusGuard()
    standin, report = place_standin(width, height)
    if standin.hwnd:
        guard.own_hwnds.add(standin.hwnd)
    aware = Rect(*report["client_aware"]) if report["client_aware"] else None
    picture = Rect(*report["picture_aware"]) if report["picture_aware"] else None
    clip = scratch / "rig_clip.mp4"
    report["clip"] = ensure_clip(clip)
    alpha_clip = scratch / "rig_alpha_clip.webm"
    report["alpha_clip"] = ensure_alpha_clip(alpha_clip)
    return Bench(scratch=scratch, clip=clip, guard=guard, standin=standin,
                 standin_report=report, standin_client_aware=aware,
                 standin_picture_aware=picture,
                 # The PICTURE, not the client. Handing the child the client
                 # makes its own second opinion disagree with it -- measured
                 # 2026-09-19: every placement came back `game_moved`, so the
                 # placer forgot the rectangle and could never re-assert the
                 # z-order. The rig has to ask for what the overlay asks for.
                 overlay_rect=picture or aware or Rect(*report["asked_unaware"]),
                 hosts=[], children=[], alpha_clip=alpha_clip)


def _emulator_rects() -> list[Rect]:
    """Where Project64's windows are, read-only, so nothing lands on them."""
    from sm64_events.replay.window import enum_windows

    out: list[Rect] = []
    with dpi_context(style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
        for window in enum_windows():
            if PROTECTED_TITLE not in (window.title or "").lower():
                continue
            rect = native.get_window_rect(window.hwnd)
            if rect is not None:
                out.append(rect)
    return out


def close_bench(bench: Bench) -> dict:
    survivors = []
    for host in bench.hosts:
        host.close()
        if host.process.poll() is None:
            survivors.append(host.process.pid)
    for child in bench.children:
        child.close()
        if child.process.poll() is None:
            survivors.append(child.process.pid)
    bench.standin.close()
    return {"standin_gone": not native.is_window(bench.standin.hwnd),
            "survivors": survivors}


# --------------------------------------------------------------------------
# the colour-key probe
# --------------------------------------------------------------------------

#: How big the probe window is. Small on purpose: everything it does is a
#: difference of two screen captures, and a small rectangle is easier to find
#: a still backdrop under.
COLOURKEY_RECT = (240, 160)

#: What each sample box is expected to do if per-pixel see-through works.
COLOURKEY_EXPECTATION = {
    "r1_nothing": "no change (the form's key shows through an unpainted page)",
    "r2_key": "no change (the page painted the key colour itself)",
    "r3_white": "a small blend toward white -- the control that proves the "
                "capture can see the window at all",
    "r4_white_half_alpha": "the edge-fringe question: toward white, or toward "
                           "the key",
    "r5_video_opaque": "a small blend toward white",
    "r5_video_transparent": "no change, if Chromium decodes the WebM alpha",
    "r6_canvas_opaque": "a small blend toward white",
    "r6_canvas_cleared": "no change",
}


def colourkey_sample_boxes(rect: Rect) -> dict[str, Rect]:
    """Where on screen each of the page's regions lands, named by what it is.

    The video and the canvas are split, because "it did not show" and "it was
    punched out" are the same reading unless the same element has an opaque
    half right beside the transparent one.
    """
    cells = grid_cells(rect, columns=3, rows=2)
    video, sheet = split_halves(cells["r5"]), split_halves(cells["r6"])
    return {"r1_nothing": inset(cells["r1"]),
            "r2_key": inset(cells["r2"]),
            "r3_white": inset(cells["r3"]),
            "r4_white_half_alpha": inset(cells["r4"]),
            "r5_video_opaque": inset(video["left"]),
            "r5_video_transparent": inset(video["right"]),
            "r6_canvas_opaque": inset(sheet["left"]),
            "r6_canvas_cleared": inset(sheet["right"])}


def backdrop_brightness(rect: Rect) -> float:
    """How bright what is already on screen here is, 0..255.

    Headroom, not decoration. A white region blended at 3/255 over a WHITE
    backdrop moves by nothing at all, so a bright rectangle would report
    "punched out" for every region including the control -- the measurement
    would be a flat table of zeroes that means nothing.
    """
    data = capture_screen(rect, capture_layered=True)
    if data is None:
        return 255.0
    summary = summarize_pixels(data, rect.width, rect.height)
    return float(sum(summary.get("mean_rgb", [255, 255, 255])) / 3.0)


def _still_backdrop(bench: "Bench", width: int, height: int,
                    out: dict, *, gap_s: float = 1.0) -> Rect | None:
    """The darkest still rectangle anywhere on the desktop, clear of PJ64.

    Two captures of the WHOLE desktop rather than a poll of a handful of
    guessed spots. His screen is mostly live video -- OBS's preview alone
    moved every one of the first eight candidates -- so the still corner has
    to be searched for rather than assumed, and searching it a rectangle at a
    time would take a minute of captures to find nothing.
    """
    with dpi_context(style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
        screen = virtual_screen()
        monitors = monitor_rects()
    first = capture_screen(screen, capture_layered=True)
    time.sleep(gap_s)
    second = capture_screen(screen, capture_layered=True)
    if first is None or second is None:
        out["blocked"] = "the desktop could not be captured"
        return None
    shape = (screen.height, screen.width, 4)
    before = np.frombuffer(first, dtype=np.uint8).reshape(shape)[:, :, :3]
    after = np.frombuffer(second, dtype=np.uint8).reshape(shape)[:, :, :3]
    moved = (np.abs(after.astype(np.int16) - before.astype(np.int16)).max(axis=2) > 0)
    brightness = after.max(axis=2)
    # Anything no monitor covers is ruled out first. It is the stillest,
    # darkest thing on the desktop and it is not on screen at all.
    moved |= ~coverage_mask(moved.shape, screen, monitors)
    # Project64 is blacked out of the search rather than checked afterwards:
    # a rectangle over his emulator must never be chosen even if it is the
    # stillest, darkest place that IS on a monitor.
    for taken in _emulator_rects():
        top = max(0, taken.y - screen.y)
        left = max(0, taken.x - screen.x)
        moved[top:top + taken.height, left:left + taken.width] = True
    found = still_block(moved, brightness, width, height)
    out["backdrop"] = {"desktop": screen.as_list(),
                       "monitors": [rect.as_list() for rect in monitors],
                       "moving_or_offscreen_fraction": round(float(moved.mean()), 4),
                       "gap_s": gap_s}
    if found is None:
        out["blocked"] = "backdrop not static"
        return None
    spot = Rect(x=screen.x + found[0], y=screen.y + found[1],
                width=width, height=height)
    # Confirmed on its own, after the search: the desktop pair says the block
    # held still for one second, and this says it is still holding still.
    confirmed = backdrop_report(spot, capture_layered=True)
    level = backdrop_brightness(spot)
    out["backdrop"].update(rect=spot.as_list(), brightness=round(level, 2),
                           headroom_to_white=round(255.0 - level, 2),
                           **confirmed)
    if not confirmed.get("static"):
        out["blocked"] = "backdrop not static"
        return None
    return spot


#: Both ways of reading the screen, measured in the same shown state.
#: A plain screen blit does NOT include a layered window on Windows 11 --
#: measured 2026-09-19, every region flat zero including the control -- so
#: CAPTUREBLT is the one that can answer, and the plain one is kept beside it
#: as the proof of why.
CAPTURE_WAYS = {"plain": False, "captureblt": True}


def _capture_probe(rect: Rect) -> dict:
    """What each way of reading the screen actually returns, side by side.

    Kept in the report because the first run of this experiment was answered
    by a capture that was not reading the desktop at all: the plain blit
    called a rectangle of live video "static" and the window's own white
    region "unchanged" in the same run.
    """
    out: dict = {}
    for name, layered in CAPTURE_WAYS.items():
        data = capture_screen(rect, capture_layered=layered)
        out[name] = ({"ok": False, "reason": "capture_failed"} if data is None
                     else summarize_pixels(data, rect.width, rect.height))
    return out


def _colourkey_pass(host: "HostProcess", bench: "Bench", rect: Rect,
                    alpha: int, *, keyed: bool = True,
                    stage: str = "control_transparent") -> dict:
    """One alpha: capture, key, show, capture, difference, hide.

    `keyed=False` is the control the whole experiment turns on. It runs the
    product's own call -- whole-window alpha and no key at all -- so "that
    region was absent" can be told apart from "that region was never painted".
    Without it, a page that simply drew nothing would read as a colour key
    working perfectly.
    """
    result: dict = {"alpha": alpha, "keyed": keyed, "stage": stage}
    host.ask(cmd="hide")
    time.sleep(0.4)
    before = {name: capture_screen(rect, capture_layered=layered)
              for name, layered in CAPTURE_WAYS.items()}
    if any(shot is None for shot in before.values()):
        return {**result, "skipped": "the screen could not be captured"}
    result["key"] = (host.ask(cmd="colourkey", rgb=list(COLOUR_KEY_RGB),
                              alpha=alpha) if keyed
                     else host.ask(cmd="alpha", value=alpha))
    kept = get_colour_key(host_hwnd(host))
    result["key_readback"] = kept
    if kept.get("alpha") != alpha or bool(kept.get("keyed")) != keyed:
        return {**result, "skipped": "the layered attributes did not read back"}
    watched = bench.guard.look()
    result["foreground_before_show"] = watched
    result["show"] = host.ask(cmd="show")
    # Shown before captured, and given a moment: a hidden page draws no
    # frames at all (experiment 1), so the video and the canvas only start
    # painting once the window is on screen.
    time.sleep(1.5)
    result["foreground_after_show"] = bench.guard.check(watched)
    result["title"] = read_title(host_hwnd(host))
    result["page"] = parse_tick_title(result["title"], prefix="RK ")
    after = {name: capture_screen(rect, capture_layered=layered)
             for name, layered in CAPTURE_WAYS.items()}
    result["window_pixels"] = capture_window(host_hwnd(host))
    host.ask(cmd="hide")
    if any(shot is None for shot in after.values()):
        return {**result, "skipped": "the screen could not be captured"}
    boxes = colourkey_sample_boxes(rect)
    result["capture"] = {}
    for way in CAPTURE_WAYS:
        regions = {
            name: region_delta(before[way], after[way], rect.width,
                               rect.height, box, rect)
            for name, box in boxes.items()}
        result["capture"][way] = {"regions": regions,
                                  "verdict": _colourkey_verdict(regions)}
    decided = [way for way, row in result["capture"].items()
               if row["verdict"].get("decided")]
    result["answered_by"] = decided[0] if decided else None
    result["verdict"] = (result["capture"][decided[0]]["verdict"] if decided
                         else {"decided": False,
                               "reason": "neither capture saw the window"})
    return result


def host_hwnd(host: "HostProcess") -> int:
    answer = host.ask(cmd="hwnd")
    return int((answer or {}).get("hwnd") or 0)


def _colourkey_verdict(regions: dict) -> dict:
    """What the numbers say, before anyone reads the table.

    `r3_white` is the control. If the opaque white region did not move, the
    capture never saw the window and every "no change" below it is meaningless
    rather than encouraging -- the failure mode an instrument like this has to
    name for itself.
    """
    def moved(name: str) -> float:
        row = regions.get(name) or {}
        return max(abs(value) for value in row.get("mean_delta_rgb", [0])) \
            if row.get("ok") else 0.0

    control = moved("r3_white")
    if control < 0.5:
        return {"decided": False,
                "reason": "the opaque white control did not move, so the "
                          "capture did not see the window",
                "control_delta": control}
    return {"decided": True, "control_delta": round(control, 3),
            "key_punched": moved("r2_key") < 0.5,
            "unpainted_punched": moved("r1_nothing") < 0.5,
            "video_alpha_punched": moved("r5_video_transparent") < 0.5,
            "video_opaque_shows": moved("r5_video_opaque") >= 0.5,
            "canvas_cleared_punched": moved("r6_canvas_cleared") < 0.5,
            "canvas_opaque_shows": moved("r6_canvas_opaque") >= 0.5,
            "half_alpha_delta": round(moved("r4_white_half_alpha"), 3)}


def experiment_colourkey(bench: "Bench", alphas=(3, 2, 1)) -> dict:
    """Can a pixel of this window be absent from the screen entirely?

    Whole-window opacity cannot do that: it dims every pixel equally, which is
    the dark sheet he saw. `LWA_COLORKEY` can, if the key colour survives the
    page's paint, the WebView2 control, the form behind it and the layered
    window. Every Mario-only option depends on the answer.
    """
    out: dict = {"name": "colourkey", "question":
                 "with the form painted the key colour and the WebView2 "
                 "control told not to paint, which of the page's regions are "
                 "absent from the composed screen?",
                 "key_rgb": list(COLOUR_KEY_RGB),
                 "expectation": COLOURKEY_EXPECTATION}
    width, height = COLOURKEY_RECT
    out["foreground_before_create"] = bench.guard.wait_until_clear()
    spot = _still_backdrop(bench, width, height, out)
    if spot is None:
        return out
    out["rect"] = spot.as_list()
    out["capture_probe"] = _capture_probe(spot)
    key_hex = "%02x%02x%02x" % COLOUR_KEY_RGB
    settings = urlencode({"video": bench.alpha_clip.as_uri(), "key": key_hex,
                          "post": "1"})
    host = HostProcess(f"{COLOURKEY_PAGE.as_uri()}#{settings}", spot,
                       title=RIG_HOST_TITLE, background=f"#{key_hex}")
    bench.hosts.append(host)
    bench.guard.own_pids.add(host.process.pid)
    try:
        hwnd = host.wait_for_window()
        out["hwnd"] = hwnd
        out["foreground_after_create"] = bench.guard.check(
            out["foreground_before_create"])
        if not hwnd:
            out["skipped"] = "the page host never reported a window handle"
            return out
        bench.guard.own_hwnds.add(hwnd)
        out["bridge"] = host.ask(cmd="bridge")
        out["styles"] = style_report(native.apply_overlay_styles(hwnd),
                                     native.get_ex_style(hwnd))
        out["place"] = host.ask(cmd="place", rect=spot.as_list())
        # Three stages, each answering what the one before it leaves open.
        # FIRST, before anything is made transparent: whole-window alpha only,
        # with the WebView2 control still painting its own background. This is
        # what the overlay does today, and it is the baseline every "absent"
        # below has to be measured against.
        out["passes"] = [_colourkey_pass(host, bench, spot, alphas[0],
                                         keyed=False,
                                         stage="opaque_control")]
        out["background"] = host.ask(cmd="background")
        # SECOND, with the control transparent but still NO key: this is where
        # a page-transparent pixel either survives to the screen or does not.
        out["passes"].append(_colourkey_pass(host, bench, spot, alphas[0],
                                             keyed=False,
                                             stage="transparent_no_key"))
        # THIRD, the colour key itself, at each alpha.
        out["passes"] += [_colourkey_pass(host, bench, spot, alpha,
                                          stage="transparent_keyed")
                          for alpha in alphas]
        out["visible_at_end"] = native.is_visible(hwnd)
        return out
    finally:
        host.close()
        out["host_exit_code"] = host.process.poll()
        out["host_lines"] = host.lines[-20:]


EXPERIMENTS = {"e1": experiment_1, "e4": experiment_4,
               "picture": experiment_picture,
               "zorder": experiment_zorder,
               "colourkey": experiment_colourkey}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghost_window_rig",
        description="Measure the ghost overlay's window against a stand-in.")
    parser.add_argument("--experiment", default="e1,e4",
                        help="comma-separated: " + ", ".join(sorted(EXPERIMENTS)))
    parser.add_argument("--scratch", default=None,
                        help="where the clip and the child's log go")
    parser.add_argument("--width", type=int, default=640,
                        help="the stand-in's client width, in ITS own space")
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--out", default=None, help="also write the JSON here")
    parser.add_argument("--role", choices=("rig", "page-host"), default="rig",
                        help="'page-host' is how this file re-enters as a child")
    parser.add_argument("--url", default=None, help="page-host: what to load")
    parser.add_argument("--title", default=RIG_HOST_TITLE)
    parser.add_argument("--rect", default=None,
                        help="page-host: x,y,w,h for the window")
    parser.add_argument("--background", default="#000000",
                        help="page-host: what the FORM behind the control is "
                             "painted; the colour key punches this")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.role == "page-host":
        args.rect = [int(part) for part in args.rect.split(",")] if args.rect else None
        return run_page_host(args)

    scratch = Path(args.scratch) if args.scratch else (
        Path(os.environ.get("TEMP", ".")) / "sm64_ghost_rig")
    scratch.mkdir(parents=True, exist_ok=True)
    # The rig measures in ONE named coordinate space and says which. Python's
    # own manifest leaves this thread unaware, and an unaware reading of an
    # aware window is two thirds of its real size -- the same confusion that
    # put a window half again too big over his other apps.
    pinned = native.set_thread_dpi_context(
        style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    report: dict = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "scratch": str(scratch), "experiments": {},
                    "rig_thread": {"pin_ok": bool(pinned),
                                   "awareness": native.thread_dpi_awareness()}}
    bench = None
    try:
        bench = build_bench(scratch, args.width, args.height)
        report["bench"] = bench.standin_report
        report["overlay_rect"] = bench.overlay_rect.as_list()
        for name in [part.strip() for part in args.experiment.split(",") if part.strip()]:
            if name not in EXPERIMENTS:
                report["experiments"][name] = {"skipped": "unknown experiment"}
                continue
            try:
                report["experiments"][name] = EXPERIMENTS[name](bench)
            except Blocked as blocked:
                report["experiments"][name] = {"skipped": str(blocked)}
            except FocusStolen as stolen:
                report["experiments"][name] = {"focus_stolen": json.loads(str(stolen))}
                report["stopped"] = ("a window of ours took the foreground; no "
                                     "further window experiments were run")
                break
    except Blocked as blocked:
        report["skipped"] = str(blocked)
    finally:
        if bench is not None:
            report["cleanup"] = close_bench(bench)
    text = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8", newline="")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
