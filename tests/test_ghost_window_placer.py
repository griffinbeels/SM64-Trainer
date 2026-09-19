"""The one thread that moves the overlay, and the read-back that proves it.

Two halves.

The FAKE half drives `Placer` against a Windows that can be told to misbehave
-- to scale what it is given, the way a placement crossing a DPI-awareness
boundary does -- so the correct-once-then-hide rule can be exercised at all.

The REAL half builds the mixed-context hazard out of actual windows: a
stand-in for Project64 created on a thread pinned to UNAWARE (what PJ64 1.6
is), read and covered from a thread pinned to per-monitor aware (what the
follower is). Both windows are created without WS_VISIBLE and destroyed
before the test returns, and the placer's ShowWindow is the one call held back
-- an agent may not put a window on his screen, so whether the ghost is
visible in the right place stays a human check.
"""
import ctypes
import ctypes.wintypes as wt
import sys
import threading

import pytest

from sm64_events.ghost.window import native, style
from sm64_events.ghost.window.geometry import (
    GameWindow,
    Rect,
    follow_decision,
    rects_agree,
    scale_rect,
)
from sm64_events.ghost.window.placer import Placer

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 windows")

OVERLAY = 0xABC
GAME = 0xDEF
PICTURE = Rect(x=120, y=80, width=1600, height=1224)
ABOVE_GAME = 0x111


class FakeWindows:
    """A Windows that answers, and can be told to put the window elsewhere."""

    def __init__(self, *, scale=1.0, above=ABOVE_GAME, game=PICTURE,
                 keeps_alpha=True):
        self.scale = scale
        self.above = above
        self.game = game
        self.keeps_alpha = keeps_alpha
        self.placed: list[tuple] = []
        self.shown: list[tuple] = []
        self.rect: Rect | None = None
        self.alpha: int | None = None
        self.styled = 0

    # reads
    def get_ex_style(self, hwnd):
        return 0x4321

    def get_owner(self, hwnd):
        return 0

    def get_window_rect(self, hwnd):
        return self.rect

    def get_alpha(self, hwnd):
        return self.alpha

    def is_visible(self, hwnd):
        return bool(self.shown and self.shown[-1][0] == "show")

    def window_above(self, hwnd):
        return self.above

    def read_game_window(self, hwnd):
        return GameWindow(found=True, minimized=False, client=self.game)

    def set_thread_dpi_context(self, context):
        return True

    def thread_dpi_awareness(self):
        return "per_monitor"

    def process_dpi_awareness(self):
        return "system"

    # writes, all against the overlay's handle
    def apply_overlay_styles(self, hwnd):
        self.styled += 1
        return 0x4321

    def set_alpha(self, hwnd, alpha):
        self.alpha = alpha if self.keeps_alpha else 255
        return True

    def place(self, hwnd, rect, insert_after):
        self.placed.append((hwnd, rect, insert_after))
        self.rect = scale_rect(rect, self.scale)
        return True

    def show_no_activate(self, hwnd):
        self.shown.append(("show", hwnd))

    def hide(self, hwnd):
        self.shown.append(("hide", hwnd))


def make_placer(**kwargs):
    fake = FakeWindows(**kwargs)
    records: list[dict] = []

    def record(event, **fields):
        records.append({"event": event, **fields})
        return fields

    return Placer(ops=fake, record=record), fake, records


SHOW = follow_decision(GameWindow(found=True, minimized=False, client=PICTURE))
HIDE = follow_decision(None)


# --- the rule ---------------------------------------------------------------

def test_a_placement_that_lands_is_kept_and_shown():
    placer, fake, records = make_placer()
    outcome = placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    assert outcome.ok is True
    assert outcome.corrected is False
    assert outcome.got == PICTURE and outcome.game == PICTURE
    assert len(fake.placed) == 1, "a placement that worked is not repeated"
    assert fake.shown == [("show", OVERLAY)]
    assert any(entry["event"] == "placed" for entry in records)


def test_the_ghost_is_inserted_above_the_game_and_never_below_it():
    placer, fake, _ = make_placer()
    placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    _hwnd, _rect, insert_after = fake.placed[0]
    assert insert_after == ABOVE_GAME
    assert insert_after != GAME, (
        "SetWindowPos inserts BELOW the handle it is given; the game's own "
        "handle would put the ghost under the emulator")


def test_being_already_above_the_game_leaves_the_z_order_alone():
    """Re-inserting every tick churns the whole z-order for nothing."""
    placer, fake, _ = make_placer(above=OVERLAY)
    placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    assert fake.placed[0][2] is None


def test_a_window_windows_scaled_is_corrected_once_and_then_kept():
    """His first try, as arithmetic: we asked for the picture and got one half
    again as wide and tall. One corrected retry lands it."""
    placer, fake, records = make_placer(scale=1.5)
    outcome = placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    assert outcome.ok is True
    assert outcome.corrected is True
    assert len(fake.placed) == 2
    assert fake.placed[1][1] != PICTURE, "the retry asks for something smaller"
    assert fake.shown == [("show", OVERLAY)]
    mismatch = [entry for entry in records if entry["event"] == "place_mismatch"]
    assert mismatch and mismatch[0]["level"] == "error"
    assert mismatch[0]["got"] == scale_rect(PICTURE, 1.5)
    assert mismatch[0]["game"] == PICTURE, "both rectangles are in the record"


def test_a_placement_that_cannot_be_proved_hides_the_window():
    """The rule he is owed: the overlay may be missing, and may not be an
    opaque rectangle over somebody else's app."""
    placer, fake, records = make_placer(scale=9.0)
    outcome = placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    assert outcome.ok is False
    assert fake.shown == [("hide", OVERLAY)], "it was never shown at all"
    assert any(entry["event"] == "place_mismatch" and entry["level"] == "error"
               for entry in records)


def test_the_game_moving_under_the_ghost_hides_it_until_the_next_look():
    placer, fake, _ = make_placer(game=Rect(x=0, y=0, width=800, height=600))
    outcome = placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    assert outcome.ok is False
    assert outcome.detail == "game_moved"
    assert fake.shown == [("hide", OVERLAY)]


def test_something_sliding_in_front_of_the_ghost_puts_it_back():
    """The z-order moves without the game picture moving, so the follower's
    "nothing changed" would leave the ghost buried under whatever came
    forward."""
    placer, fake, records = make_placer(above=OVERLAY)
    placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    assert placer.keep_above(OVERLAY, GAME) is False, "already on top"

    fake.above = 0x999  # somebody else is now directly above the emulator
    assert placer.keep_above(OVERLAY, GAME) is True
    assert fake.placed[-1][2] == 0x999
    assert fake.placed[-1][1] == PICTURE, "it goes back where it was"
    assert any(entry["event"] == "z_reasserted" for entry in records)


def test_a_hidden_ghost_is_not_pushed_back_over_anything():
    placer, fake, _ = make_placer()
    placer.apply(OVERLAY, HIDE, game_hwnd=GAME)
    assert placer.keep_above(OVERLAY, GAME) is False
    assert fake.placed == []


def test_a_hidden_decision_moves_nothing():
    placer, fake, _ = make_placer()
    assert placer.apply(OVERLAY, HIDE, game_hwnd=GAME) is None
    assert fake.placed == []
    assert fake.shown == [("hide", OVERLAY)]


def test_nothing_happens_before_the_window_exists():
    placer, fake, _ = make_placer()
    assert placer.apply(0, SHOW, game_hwnd=GAME) is None
    assert fake.placed == [] and fake.shown == []


def test_the_styles_are_applied_once_and_read_back():
    placer, fake, records = make_placer()
    placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    placer.apply(OVERLAY, HIDE, game_hwnd=GAME)
    assert fake.styled == 1
    styles = next(entry for entry in records if entry["event"] == "styles")
    assert styles["owner"] == 0, "the overlay must never have an owner"


# --- opacity ----------------------------------------------------------------

def test_the_opacity_is_set_once_and_read_back():
    placer, fake, records = make_placer()
    placer.ensure_alpha(OVERLAY, 128)
    placer.ensure_alpha(OVERLAY, 128)
    assert fake.alpha == 128
    alphas = [entry for entry in records if entry["event"] == "alpha"]
    assert len(alphas) == 1, "an unchanged opacity is not re-applied"
    assert alphas[0]["asked"] == 128 and alphas[0]["got"] == 128


def test_an_opacity_the_window_did_not_keep_is_an_error():
    placer, _fake, records = make_placer(keeps_alpha=False)
    placer.ensure_alpha(OVERLAY, 128)
    alpha = next(entry for entry in records if entry["event"] == "alpha")
    assert alpha["level"] == "error"
    assert alpha["asked"] == 128 and alpha["got"] == 255


# --- the context ------------------------------------------------------------

def test_the_thread_says_what_context_it_actually_got():
    placer, _fake, records = make_placer()
    report = placer.adopt_thread(process_pin_ok=False)
    assert report.thread == "per_monitor"
    assert report.process == "system"
    assert report.process_pin_ok is False and report.thread_pin_ok is True
    assert any(entry["event"] == "dpi_context" for entry in records)


# --- the real thing, with nothing on screen ---------------------------------

@pytest.fixture
def windows_made():
    """Real top-level windows, created without WS_VISIBLE, destroyed after.

    "STATIC" is a predefined class, so this needs no RegisterClass and no
    message loop.
    """
    made = []

    def make(x=0, y=0, w=320, h=240):
        user32 = ctypes.windll.user32
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
            wt.HINSTANCE, wt.LPVOID]
        hwnd = user32.CreateWindowExW(
            style.OVERLAY_EX_STYLE, "STATIC", "sm64 ghost placer test",
            style.CREATE_STYLE, x, y, w, h, None, None, None, None)
        assert hwnd, "CreateWindowExW refused the overlay style set"
        made.append(int(hwnd))
        return int(hwnd)

    yield make
    for hwnd in made:
        ctypes.windll.user32.DestroyWindow(wt.HWND(hwnd))


class NoShow:
    """Real user32 for everything except the two calls that reach his screen."""

    def __init__(self):
        self.shown: list[tuple] = []

    def __getattr__(self, name):
        return getattr(native, name)

    def show_no_activate(self, hwnd):
        self.shown.append(("show", hwnd))

    def hide(self, hwnd):
        self.shown.append(("hide", hwnd))


@pytest.fixture
def unaware_standin():
    """A stand-in for Project64: a real window created on an UNAWARE thread.

    A window belongs to the thread that made it, so the thread has to outlive
    the test -- and DPI awareness is per thread, which is exactly the hazard
    being reproduced. PJ64 1.6 is DPI-unaware; this window is too.
    """
    made: dict = {}
    ready, finished = threading.Event(), threading.Event()

    def own_the_window():
        made["set_ok"] = native.set_thread_dpi_context(
            style.DPI_AWARENESS_CONTEXT_UNAWARE)
        made["context"] = native.thread_dpi_awareness()
        user32 = ctypes.windll.user32
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
            wt.HINSTANCE, wt.LPVOID]
        made["hwnd"] = int(user32.CreateWindowExW(
            style.OVERLAY_EX_STYLE, "STATIC", "sm64 ghost pj64 stand-in",
            style.CREATE_STYLE, 100, 80, 320, 240, None, None, None, None))
        ready.set()
        finished.wait(30)
        ctypes.windll.user32.DestroyWindow(wt.HWND(made["hwnd"]))

    thread = threading.Thread(target=own_the_window, name="pj64-standin",
                              daemon=True)
    thread.start()
    assert ready.wait(10), "the stand-in window was never created"
    assert made["context"] == "unaware"
    try:
        yield made["hwnd"]
    finally:
        finished.set()
        thread.join(timeout=10)
        assert not thread.is_alive(), "the stand-in thread outlived its test"


def test_reading_and_placing_in_one_context_lands_exactly(unaware_standin,
                                                          windows_made):
    """The whole of defect C, against real windows in two DPI worlds.

    The stand-in is DPI-unaware like Project64; the placer pins the calling
    thread to per-monitor aware and does the read, the placement and the
    read-back there. What comes back must be the same rectangle, uncorrected:
    the old code multiplied it by GetDpiForWindow(ours)/GetDpiForWindow(theirs)
    and put a window half again as big on his screen.
    """
    before = native.thread_dpi_awareness()
    ops = NoShow()
    placer = Placer(ops=ops, record=lambda *a, **k: None)
    try:
        report = placer.adopt_thread(process_pin_ok=False)
        assert report.thread == "per_monitor"

        overlay = windows_made(w=10, h=10)
        seen = native.read_game_window(unaware_standin)
        assert seen.client is not None
        placement = follow_decision(
            GameWindow(found=True, minimized=False, client=seen.client))

        outcome = placer.apply(overlay, placement, game_hwnd=unaware_standin)
        assert outcome is not None
        assert outcome.ok is True, f"placement was not proved: {outcome}"
        assert outcome.corrected is False, (
            "an honest read and an honest placement need no correction")
        assert outcome.got == seen.client
        assert outcome.game == seen.client
        assert outcome.insert_after != unaware_standin, (
            "the ghost may never be inserted below the emulator")
        assert ops.shown == [("show", overlay)]
        assert native.is_visible(overlay) is False, (
            "nothing this test made may reach his screen")
    finally:
        native.set_thread_dpi_context(
            style.DPI_AWARENESS_CONTEXT_UNAWARE if before == "unaware"
            else style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)


def test_the_dpi_ratio_the_old_code_corrected_by_overshoots_the_picture(
        unaware_standin, windows_made):
    """The defect itself, reproduced against real windows.

    The old follower multiplied the rectangle it had just read by
    `GetDpiForWindow(overlay) / GetDpiForWindow(pj64)`. That ratio compares an
    unaware window (always 96) with an aware one (the monitor's DPI), so at
    any scaling above 100 % it is greater than 1 -- and the rectangle it was
    applied to was already correct. Here it is, growing a rectangle that
    matched the stand-in into one that does not.

    At 100 % scaling the ratio is 1, there is nothing to overshoot, and the
    assertion below is simply that the read was already right.
    """
    before = native.thread_dpi_awareness()
    try:
        native.set_thread_dpi_context(
            style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        picture = native.read_game_window(unaware_standin).client
        assert picture is not None

        ours = windows_made(w=10, h=10)
        game_dpi, our_dpi = native.window_dpi(unaware_standin), native.window_dpi(ours)
        assert game_dpi > 0 and our_dpi > 0
        old_factor = our_dpi / game_dpi

        assert rects_agree(picture, picture), "the honest read needs no factor"
        if old_factor > 1.0:
            assert not rects_agree(picture, scale_rect(picture, old_factor)), (
                f"the old {old_factor:g}x factor moved a correct rectangle")
    finally:
        native.set_thread_dpi_context(
            style.DPI_AWARENESS_CONTEXT_UNAWARE if before == "unaware"
            else style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
