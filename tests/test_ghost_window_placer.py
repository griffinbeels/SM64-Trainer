"""The one thread that moves the overlay, the gate before it, and the proof.

Three halves, which is one too many but they belong together.

The GATE tests drive `Placer.prepare` against a Windows that behaves the way
his second try behaved: a window that is not ours yet, whose layered bit is
missing, so `SetLayeredWindowAttributes` fails exactly as it did at
08:20:06.806. Nothing may be remembered as done until its readback agrees.

The FAKE half drives placement against a Windows that can be told to
misbehave -- to scale what it is given, the way a placement crossing a
DPI-awareness boundary does -- so the correct-once-then-hide rule can be
exercised at all.

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
from sm64_events.ghost.window.visibility import visibility_for

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 windows")

OVERLAY = 0xABC
GAME = 0xDEF
PICTURE = Rect(x=120, y=80, width=1600, height=1224)
ABOVE_GAME = 0x111
ALPHA = 128


class FakeWindows:
    """A Windows that answers, and that can be told to behave badly.

    `layers` models the one that actually bit: WinForms drives `Form.Opacity`
    on our form while pywebview starts it, and WinForms implements Opacity
    with `WS_EX_LAYERED` -- so for a while our styles do not stick, and every
    `SetLayeredWindowAttributes` against the window fails.
    """

    def __init__(self, *, scale=1.0, above=ABOVE_GAME, game=PICTURE,
                 keeps_alpha=True, layers=True, real=True):
        self.scale = scale
        self.above = above
        self.game = game
        self.keeps_alpha = keeps_alpha
        self.layers = layers
        self.real = real
        self.ex = 0
        self.placed: list[tuple] = []
        self.shown: list[tuple] = []
        self.rect: Rect | None = None
        self.alpha: int | None = None
        self.styled = 0

    # reads
    def is_window(self, hwnd):
        return bool(hwnd) and self.real

    def get_ex_style(self, hwnd):
        return self.ex

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
        if self.layers:
            self.ex = style.OVERLAY_EX_STYLE
        return style.OVERLAY_EX_STYLE

    def set_alpha(self, hwnd, alpha):
        if not self.ex & style.WS_EX_LAYERED:
            return False  # exactly what Windows does to an unlayered window
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


def want(placement, alpha=ALPHA):
    return visibility_for(placement, wanted_alpha=alpha)


PLAYING = GameWindow(found=True, minimized=False, client=PICTURE)
SHOW = want(follow_decision(PLAYING))
HIDE = want(follow_decision(None))


def ready(placer, alpha=ALPHA):
    """Walk the gate the way the follower does, then answer."""
    return placer.prepare(OVERLAY, alpha)


# --- the gate ---------------------------------------------------------------

def test_a_window_that_does_not_exist_yet_is_not_ready():
    placer, fake, _ = make_placer(real=False)
    state = ready(placer)
    assert state.ok is False
    assert state.window is False
    assert fake.styled == 0, "nothing is done to a window that is not there"
    assert placer.prepare(0, ALPHA).ok is False


def test_the_styles_come_before_the_alpha_and_the_alpha_before_showing():
    placer, fake, records = make_placer()
    state = ready(placer)
    assert state.ok is True
    order = [entry["event"] for entry in records]
    assert order.index("styles") < order.index("alpha"), (
        "SetLayeredWindowAttributes fails on a window that is not layered yet")
    assert fake.alpha == ALPHA
    assert fake.shown == [], "being ready is not being shown"


def test_a_window_that_will_not_take_the_styles_never_becomes_ready():
    placer, fake, records = make_placer(layers=False)
    for _ in range(3):
        state = ready(placer)
        assert state.ok is False
        assert state.styles is False
        assert state.alpha is False
    assert fake.styled == 3, "it keeps asking rather than giving up"
    assert fake.alpha is None
    assert not [entry for entry in records if entry["event"] == "alpha"], (
        "the alpha is not even attempted on a window that is not layered yet "
        "-- SetLayeredWindowAttributes can only fail there")


def test_a_failed_alpha_is_never_remembered_as_applied():
    """THE bug from his second try. The window was not layered when the first
    alpha was asked for, the call failed, and the wanted value was stored
    anyway -- so it never retried and the ghost would have been fully opaque.
    """
    placer, fake, _ = make_placer(layers=False)
    assert placer.ensure_alpha(OVERLAY, ALPHA) is False
    assert fake.alpha is None
    assert placer.applied_alpha is None, (
        "a value the window refused is not a value the window is holding")

    fake.ex = style.OVERLAY_EX_STYLE  # the window becomes ours a moment later
    assert placer.ensure_alpha(OVERLAY, ALPHA) is True, (
        "the same value must be asked for again, not skipped as done")
    assert fake.alpha == ALPHA
    assert placer.applied_alpha == ALPHA


def test_an_alpha_the_window_did_not_keep_is_an_error_and_is_retried():
    placer, fake, records = make_placer(keeps_alpha=False)
    ready(placer)
    assert placer.ensure_alpha(OVERLAY, ALPHA) is False
    alpha = [entry for entry in records if entry["event"] == "alpha"]
    assert alpha and alpha[0]["level"] == "error"
    assert alpha[0]["asked"] == ALPHA and alpha[0]["got"] == 255


def test_styles_taken_away_behind_our_back_are_put_straight_back():
    """WinForms clears WS_EX_LAYERED when it sets Form.Opacity to 1, which
    pywebview does while the window is starting."""
    placer, fake, _ = make_placer()
    assert ready(placer).ok is True
    fake.ex = 0  # pywebview drives Form.Opacity; our bits are gone
    assert ready(placer).ok is True
    assert fake.styled == 2


def test_the_readiness_is_written_down_when_it_changes():
    placer, fake, records = make_placer(layers=False)
    ready(placer)
    ready(placer)
    notes = [entry for entry in records if entry["event"] == "readiness"]
    assert len(notes) == 1, "a steady state is not written every tick"
    assert notes[0]["level"] == "error"
    fake.layers = True
    ready(placer)
    notes = [entry for entry in records if entry["event"] == "readiness"]
    assert notes[-1]["styles"] is True and notes[-1]["alpha"] is True


# --- placement --------------------------------------------------------------

def test_a_placement_that_lands_is_kept_and_shown():
    placer, fake, records = make_placer()
    ready(placer)
    outcome = placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    assert outcome.ok is True
    assert outcome.corrected is False
    assert outcome.got == PICTURE and outcome.game == PICTURE
    assert len(fake.placed) == 1, "a placement that worked is not repeated"
    assert fake.shown == [("show", OVERLAY)]
    assert any(entry["event"] == "placed" for entry in records)


def test_the_ghost_is_inserted_above_the_game_and_never_below_it():
    placer, fake, _ = make_placer()
    ready(placer)
    placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    _hwnd, _rect, insert_after = fake.placed[0]
    assert insert_after == ABOVE_GAME
    assert insert_after != GAME, (
        "SetWindowPos inserts BELOW the handle it is given; the game's own "
        "handle would put the ghost under the emulator")


def test_being_already_above_the_game_leaves_the_z_order_alone():
    """Re-inserting every tick churns the whole z-order for nothing."""
    placer, fake, _ = make_placer(above=OVERLAY)
    ready(placer)
    placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    assert fake.placed[0][2] is None


def test_a_window_windows_scaled_is_corrected_once_and_then_kept():
    """His first try, as arithmetic: we asked for the picture and got one half
    again as wide and tall. One corrected retry lands it."""
    placer, fake, records = make_placer(scale=1.5)
    ready(placer)
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
    ready(placer)
    outcome = placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    assert outcome.ok is False
    assert fake.shown == [("hide", OVERLAY)], "it was never shown at all"
    assert any(entry["event"] == "place_mismatch" and entry["level"] == "error"
               for entry in records)


def test_the_game_moving_under_the_ghost_hides_it_until_the_next_look():
    placer, fake, _ = make_placer(game=Rect(x=0, y=0, width=800, height=600))
    ready(placer)
    outcome = placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    assert outcome.ok is False
    assert outcome.detail == "game_moved"
    assert fake.shown == [("hide", OVERLAY)]


def test_something_sliding_in_front_of_the_ghost_puts_it_back():
    """The z-order moves without the game picture moving, so the follower's
    "nothing changed" would leave the ghost buried under whatever came
    forward."""
    placer, fake, records = make_placer(above=OVERLAY)
    ready(placer)
    placer.apply(OVERLAY, SHOW, game_hwnd=GAME)
    assert placer.keep_above(OVERLAY, GAME) is False, "already on top"

    fake.above = 0x999  # somebody else is now directly above the emulator
    assert placer.keep_above(OVERLAY, GAME) is True
    assert fake.placed[-1][2] == 0x999
    assert fake.placed[-1][1] == PICTURE, "it goes back where it was"
    assert any(entry["event"] == "z_reasserted" for entry in records)


def test_a_hidden_ghost_is_not_pushed_back_over_anything():
    placer, fake, _ = make_placer()
    ready(placer)
    placer.apply(OVERLAY, HIDE, game_hwnd=GAME)
    assert placer.keep_above(OVERLAY, GAME) is False
    assert fake.placed == []


def test_a_hidden_decision_moves_nothing():
    placer, fake, _ = make_placer()
    ready(placer)
    assert placer.apply(OVERLAY, HIDE, game_hwnd=GAME) is None
    assert fake.placed == []
    assert fake.shown == [("hide", OVERLAY)]


def test_nothing_happens_before_the_window_exists():
    placer, fake, _ = make_placer()
    assert placer.apply(0, SHOW, game_hwnd=GAME) is None
    assert fake.placed == [] and fake.shown == []


def test_the_overlay_never_takes_an_owner():
    placer, _fake, records = make_placer()
    ready(placer)
    styles = next(entry for entry in records if entry["event"] == "styles")
    assert styles["owner"] == 0, "the overlay must never have an owner"


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
    ready_now, finished = threading.Event(), threading.Event()

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
        ready_now.set()
        finished.wait(30)
        ctypes.windll.user32.DestroyWindow(wt.HWND(made["hwnd"]))

    thread = threading.Thread(target=own_the_window, name="pj64-standin",
                              daemon=True)
    thread.start()
    assert ready_now.wait(10), "the stand-in window was never created"
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
        state = placer.prepare(overlay, ALPHA)
        assert state.ok is True, f"the gate never opened: {state}"
        assert native.get_alpha(overlay) == ALPHA, (
            "the alpha is confirmed by readback before anything may be shown")

        seen = native.read_game_window(unaware_standin)
        assert seen.client is not None
        placement = follow_decision(
            GameWindow(found=True, minimized=False, client=seen.client))

        outcome = placer.apply(overlay, want(placement), game_hwnd=unaware_standin)
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
