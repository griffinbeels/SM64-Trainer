"""Every user32 write the overlay makes, on ONE thread in ONE DPI context.

**The bug this class exists to make impossible.** DPI awareness on Windows is
a property of the CALLING THREAD, so a rectangle read in one context and
applied in another is a rectangle multiplied by the display scale. Until
2026-09-19 the overlay spread those calls across whichever thread happened to
ask -- the follower placed, the control pipe set the alpha, the host restyled
-- and on top of that it multiplied the rectangle it had just read by
`GetDpiForWindow(us) / GetDpiForWindow(pj64)`. PJ64 1.6 is DPI-unaware, so
that ratio is 144/96 at his 150 % scaling whether or not anything was actually
wrong, and his first try put a window half again as wide and tall as the game
over his other apps. Now there is exactly one thread, it says out loud what
context it is in, and it proves the result instead of trusting it.

**Which context, and why this one.** PER_MONITOR_AWARE_V2, set on the
follower thread. What the ghost must cover is the picture the player SEES --
the physical pixels Project64's client area occupies on the monitor -- and an
aware thread is the one that reads that rectangle honestly (live 2026-07-23:
PJ64's client reads 1600x1224 from an unaware thread and 2400x1836 from an
aware one at 150 %). `replay/video.py` deliberately chooses UNAWARE for its
geometry thread and is not in disagreement with this: it wants PJ64's backing
SURFACE, because it is copying the emulator's pixels, and the surface is the
logical size. Different question, opposite answer -- and both are only safe
because each pins its own thread rather than inheriting one.

The process context is NOT relied on. `SetProcessDpiAwarenessContext` is
refused whenever awareness is already fixed, which it usually is here:
python.exe carries a manifest that declares it, and pywebview's WinForms
backend calls `SetProcessDPIAware()` before it starts the GUI thread
(`webview/platforms/winforms.py`). A thread context can be set regardless, so
that is what this uses -- and it reports what Windows actually gave back, not
what it asked for.

**Nothing here may name Project64 as the window being MODIFIED.** The game's
handle reaches two readers -- where its picture is, and which window sits
above it -- and nothing else. `tests/test_ghost_window_guard.py` reads this
package's source and fails if that ever stops being true.
"""
from dataclasses import asdict, dataclass

from sm64_events.ghost.window import diag, native, style
from sm64_events.ghost.window.geometry import (
    PLACE_TOLERANCE_PX,
    Placement,
    Rect,
    correction_for,
    rects_agree,
)

#: The context the follower thread runs in. See the module docstring for why
#: this and not UNAWARE.
THREAD_CONTEXT = style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2


@dataclass(frozen=True)
class DpiReport:
    """What coordinate space the overlay is ACTUALLY placing windows in."""

    process: str
    thread: str
    process_pin_ok: bool
    thread_pin_ok: bool


@dataclass(frozen=True)
class PlaceOutcome:
    """One placement, and the read-back that either proved it or did not."""

    ok: bool
    asked: Rect
    got: Rect | None
    game: Rect | None
    insert_after: int
    corrected: bool
    detail: str


class Placer:
    """The overlay's window, driven from the one thread that owns it."""

    def __init__(self, ops=native, *, record=diag.record,
                 tolerance: int = PLACE_TOLERANCE_PX):
        self._ops = ops
        self._record = record
        self._tolerance = tolerance
        self._styled = False
        self._alpha: int | None = None
        self._shown: bool | None = None
        self._placed: Rect | None = None

    # --- the one context ----------------------------------------------------

    def adopt_thread(self, *, process_pin_ok: bool = False) -> DpiReport:
        """Pin the CALLING thread, then say what Windows really gave it.

        Called first thing on the follower thread. The readback matters more
        than the setter's return: "the call succeeded" and "this thread is
        per-monitor aware" are different facts, and only the second one places
        a window where it was told.
        """
        thread_ok = bool(self._ops.set_thread_dpi_context(THREAD_CONTEXT))
        report = DpiReport(process=self._ops.process_dpi_awareness(),
                           thread=self._ops.thread_dpi_awareness(),
                           process_pin_ok=bool(process_pin_ok),
                           thread_pin_ok=thread_ok)
        self._record("dpi_context", level="info", **asdict(report))
        return report

    # --- what the follower asks for -----------------------------------------

    def ensure_alpha(self, hwnd: int, alpha: int) -> None:
        """Set the whole-window opacity and read back what the window kept."""
        if not hwnd or alpha == self._alpha:
            return
        taken = self._ops.set_alpha(hwnd, alpha)
        kept = self._ops.get_alpha(hwnd)
        self._alpha = alpha
        self._record("alpha", level="info" if kept == alpha else "error",
                     asked=alpha, got=kept, accepted=bool(taken))

    def keep_above(self, hwnd: int, game_hwnd: int) -> bool:
        """Put the ghost back directly above Project64 if something got between.

        Run every tick, because the z-order moves without the game picture
        moving: another window coming forward slides over the ghost and the
        placement itself has not changed, so the follower's "nothing to do"
        would leave it buried. Cheap -- one `GetWindow` -- and it does nothing
        at all while the ghost is hidden.
        """
        if not hwnd or not game_hwnd or not self._shown or self._placed is None:
            return False
        if int(self._ops.window_above(game_hwnd) or 0) == hwnd:
            return False
        outcome = self._place(hwnd, self._placed, game_hwnd)
        self._record("z_reasserted", level="info", ok=outcome.ok,
                     insert_after=outcome.insert_after)
        if not outcome.ok:
            self._visibility(hwnd, False, outcome.detail)
        return True

    def apply(self, hwnd: int, placement: Placement,
              *, game_hwnd: int = 0) -> PlaceOutcome | None:
        """Make the window match one decision. Returns the placement it made.

        A placement that cannot be proved HIDES the window. That is the whole
        rule: the overlay is allowed to be missing, and is not allowed to be
        an opaque rectangle over somebody else's app.
        """
        if not hwnd:
            return None
        self._ensure_styles(hwnd)
        if not placement.show or placement.rect is None:
            self._placed = None
            self._visibility(hwnd, False, placement.reason)
            return None
        outcome = self._place(hwnd, placement.rect, game_hwnd)
        self._placed = placement.rect if outcome.ok else None
        self._visibility(hwnd, outcome.ok,
                         placement.reason if outcome.ok else outcome.detail)
        return outcome

    # --- the parts ----------------------------------------------------------

    def _ensure_styles(self, hwnd: int) -> None:
        if self._styled:
            return
        asked = self._ops.apply_overlay_styles(hwnd)
        kept = self._ops.get_ex_style(hwnd)
        self._styled = True
        self._record("styles", level="info" if kept == asked else "error",
                     asked=f"{asked:#x}", got=f"{kept:#x}",
                     owner=self._ops.get_owner(hwnd))

    def _z_target(self, hwnd: int, game_hwnd: int) -> int | None:
        """The handle to insert AFTER so the ghost lands directly above PJ64.

        None means "leave the z-order alone", which is the answer once we
        already are that window -- re-inserting every tick churns the order
        for nothing.
        """
        if not game_hwnd:
            return None
        above = int(self._ops.window_above(game_hwnd) or 0)
        return None if above == hwnd else above

    def _game_rect(self, game_hwnd: int) -> Rect | None:
        if not game_hwnd:
            return None
        return self._ops.read_game_window(game_hwnd).client

    def _place(self, hwnd: int, wanted: Rect, game_hwnd: int) -> PlaceOutcome:
        insert_after = self._z_target(hwnd, game_hwnd)
        asked, corrected = wanted, False
        for last_try in (False, True):
            self._ops.place(hwnd, asked, insert_after=insert_after)
            got = self._ops.get_window_rect(hwnd)
            game = self._game_rect(game_hwnd)
            ours_ok = rects_agree(wanted, got, self._tolerance)
            game_ok = game is None or rects_agree(wanted, game, self._tolerance)
            if ours_ok and game_ok:
                # Recorded on the GOOD path too, not only the bad one: the
                # question a failed try has to answer from disk is "what
                # rectangle did it actually put there", and a log that only
                # speaks up when our own check already failed cannot answer it.
                self._record("placed", level="info", wanted=wanted, asked=asked,
                             got=got, game=game, insert_after=insert_after or 0,
                             corrected=corrected)
                return PlaceOutcome(ok=True, asked=asked, got=got, game=game,
                                    insert_after=insert_after or 0,
                                    corrected=corrected, detail="placed")
            detail = ("game_moved" if ours_ok
                      else "scaled" if game_ok else "unplaceable")
            fix = (None if last_try or not game_ok or got is None
                   else correction_for(wanted, got))
            self._record("place_mismatch", level="error", detail=detail,
                         wanted=wanted, asked=asked, got=got, game=game,
                         insert_after=insert_after or 0, correction=fix)
            if fix is None:
                return PlaceOutcome(ok=False, asked=asked, got=got, game=game,
                                    insert_after=insert_after or 0,
                                    corrected=corrected, detail=detail)
            asked, corrected = fix, True
        raise AssertionError("unreachable: the loop returns on both passes")

    def _visibility(self, hwnd: int, show: bool, reason: str) -> None:
        if show == self._shown:
            return
        self._shown = show
        if show:
            self._ops.show_no_activate(hwnd)
        else:
            self._ops.hide(hwnd)
        self._record("visibility", level="info", show=show, reason=reason,
                     visible=bool(self._ops.is_visible(hwnd)))
