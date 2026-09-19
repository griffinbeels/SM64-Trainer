"""Every user32 write the overlay makes, on ONE thread in ONE DPI context.

**The size bug this class exists to make impossible.** DPI awareness on
Windows is a property of the CALLING THREAD, so a rectangle read in one
context and applied in another is a rectangle multiplied by the display scale.
On top of that the old follower multiplied the rectangle it had just read by
`GetDpiForWindow(us) / GetDpiForWindow(pj64)`. PJ64 1.6 is DPI-unaware, so
that ratio is 144/96 at his 150 % scaling whether or not anything was actually
wrong, and his first try put a window half again as wide and tall as the game
over his other apps. Now there is exactly one thread, it says out loud what
context it is in, and it proves the result instead of trusting it.

**The startup race this class exists to survive, found in his SECOND try.**
The window handle exists before the window is ours. pywebview's WinForms
backend shows and hides the form during `create_window` and drives
`Form.Opacity` around it -- and WinForms implements `Opacity` with the very
`WS_EX_LAYERED` bit whole-window alpha needs. So an alpha set at the wrong
moment simply fails. In his log (`evidence/second-try-ghost_window.jsonl`):
the handle appeared at 08:20:06.806, the alpha asked for at that same
millisecond came back `accepted: False, got: None`, and the styles did not
land until 08:20:10.923 -- four seconds later, because styling lived inside a
placement that only ran when the decision CHANGED.

Nothing here is one-shot any more. `prepare()` is the gate, it runs every
tick, and every step is confirmed by a READBACK rather than by a return value:

    a real window handle  ->  the overlay styles  ->  the alpha  ->  may show

Each step retries until its readback agrees, and a step that fails is never
remembered as done. That is the whole of the fix: the old `ensure_alpha`
stored the wanted value whether or not the call worked, so the first failed
attempt was remembered as applied and never retried -- with the visibility
gate opened, the ghost would have come up FULLY OPAQUE, his round-1 symptom.

**Which DPI context, and why this one.** PER_MONITOR_AWARE_V2, set on the
follower thread. What the ghost must cover is the picture the player SEES --
the physical pixels Project64's client area occupies -- and an aware thread is
the one that reads that honestly (live 2026-07-23: PJ64's client reads
1600x1224 from an unaware thread and 2400x1836 from an aware one at 150 %).
`replay/video.py` deliberately chooses UNAWARE for its geometry thread and is
not in disagreement: it wants PJ64's backing SURFACE, because it is copying
the emulator's pixels. Different question, opposite answer -- and both are
only safe because each pins its own thread rather than inheriting one.

The process context is NOT relied on. `SetProcessDpiAwarenessContext` is
refused whenever awareness is already fixed, and pywebview's WinForms backend
calls `SetProcessDPIAware()` before it starts the GUI thread. A thread context
can be set regardless, so that is what this uses -- and it reports what
Windows actually gave back, not what it asked for.

**Nothing here may name Project64 as the window being MODIFIED.** The game's
handle reaches two readers -- where its picture is, and which window sits
above it -- and nothing else. `tests/test_ghost_window_guard.py` reads this
package's source and fails if that ever stops being true.
"""
from dataclasses import asdict, dataclass

from sm64_events.ghost.window import diag, native, style
from sm64_events.ghost.window.geometry import (
    PLACE_TOLERANCE_PX,
    Rect,
    correction_for,
    rects_agree,
)
from sm64_events.ghost.window.visibility import CLEAR, Visibility

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
class Readiness:
    """How far up the startup chain the window has actually got.

    Each field is a READBACK, never a return value. `ok` is the one thing that
    permits the window on his screen.
    """

    hwnd: int
    window: bool
    styles: bool
    alpha: bool

    @property
    def ok(self) -> bool:
        return self.window and self.styles and self.alpha


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
        self._alpha: int | None = None
        self._shown: bool | None = None
        self._placed: Rect | None = None
        self._readiness: Readiness | None = None
        #: What the window should be HOLDING right now. It starts fully
        #: transparent and is only ever raised by `apply`, on a proved
        #: placement, after the page has reported a picture -- so the window
        #: can never first appear at anything but invisible.
        self._hold: int = CLEAR
        self._hold_reason = "parked"

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

    # --- the gate -----------------------------------------------------------

    @property
    def applied_alpha(self) -> int | None:
        """The alpha this window is KNOWN to be holding, or None.

        None after every failure, which is the whole point: the value the
        window was merely asked for is not a value it has.
        """
        return self._alpha

    def prepare(self, hwnd: int) -> Readiness:
        """Everything that must read back true before the window may be shown.

        Run EVERY tick, in this order, retrying whatever is not yet true. A
        handle that does not exist yet and a window whose layered bit
        pywebview removed a moment ago are the same thing from here: not
        ready, try again in a tick.

        The alpha it confirms is the one the window is supposed to be HOLDING,
        which starts fully transparent. So "ready" means parked and safe to
        put on screen, and re-asserting it every tick is what defends the
        opacity against WinForms clearing the layered bit underneath us.
        """
        if not hwnd or not self._ops.is_window(hwnd):
            self._alpha = None
            return self._note(Readiness(hwnd=hwnd, window=False, styles=False,
                                        alpha=False))
        styled = self.ensure_styles(hwnd)
        if not styled:
            # The bits were taken back, so no alpha is being applied at all
            # and the window is SOLID -- his round-1 look. Nothing may be on
            # screen until both read back again.
            self._fall_back(hwnd, "styles_lost")
            return self._note(Readiness(hwnd=hwnd, window=True, styles=False,
                                        alpha=False))
        # Alpha AFTER styles, never beside them: SetLayeredWindowAttributes
        # fails on a window that is not WS_EX_LAYERED, which is exactly what
        # happened at 08:20:06.806 in his second try.
        alpha_ok = self.ensure_alpha(hwnd, self._hold, reason=self._hold_reason)
        if not alpha_ok:
            # Measured in the rig: an alpha can be ACCEPTED and then taken
            # away 1.5 s later, and a set can report success on a window that
            # reads back unlayered with no alpha at all. One good readback is
            # never proof, so this drops to parked and keeps asking.
            self._fall_back(hwnd, "alpha_lost")
            alpha_ok = self.ensure_alpha(hwnd, CLEAR, reason="alpha_lost")
        return self._note(Readiness(hwnd=hwnd, window=True, styles=True,
                                    alpha=alpha_ok))

    def _fall_back(self, hwnd: int, reason: str) -> None:
        """Stop showing him anything, and forget what we thought we held."""
        if self._hold != CLEAR or self._shown:
            self._record("parked", level="error", reason=reason,
                         was_holding=self._hold, was_shown=bool(self._shown))
        self._hold, self._hold_reason = CLEAR, reason
        self._alpha = None
        # Hidden, not faded: with the layered bit gone there is no alpha that
        # can make it invisible, so ShowWindow is the only lever left.
        self._visibility(hwnd, False, reason)

    def _note(self, readiness: Readiness) -> Readiness:
        if readiness != self._readiness:
            self._readiness = readiness
            self._record("readiness", level="info" if readiness.ok else "error",
                         **asdict(readiness))
        return readiness

    def ensure_styles(self, hwnd: int) -> bool:
        """Confirm the overlay bits are on, re-applying whenever they are not.

        Not cached, because the bits can be taken away: WinForms implements
        `Form.Opacity` with `WS_EX_LAYERED`, and pywebview sets Opacity on
        this very form while it is starting up. The only honest answer is the
        readback, and the only safe response to a bad one is to ask again.
        """
        if self._has_overlay_styles(hwnd):
            return True
        asked = self._ops.apply_overlay_styles(hwnd)
        kept = self._ops.get_ex_style(hwnd)
        ok = self._has_overlay_styles(hwnd)
        self._record("styles", level="info" if ok else "error",
                     asked=f"{asked:#x}", got=f"{kept:#x}",
                     owner=self._ops.get_owner(hwnd))
        return ok

    def _has_overlay_styles(self, hwnd: int) -> bool:
        kept = self._ops.get_ex_style(hwnd)
        return kept & style.OVERLAY_EX_STYLE == style.OVERLAY_EX_STYLE

    def ensure_alpha(self, hwnd: int, alpha: int, *, reason: str = "") -> bool:
        """Set the whole-window opacity and CONFIRM it. Never remembers a miss.

        This is the only thing that makes the ghost visible or invisible to
        him -- the window itself stays on screen -- so every transition is
        written down with what was asked, what the window kept, and why.

        The bug this replaces stored the wanted value whether or not the call
        worked, so an alpha set a millisecond too early was remembered as
        applied and never retried. It only escaped notice in his second try
        because he moved the slider, which asked for a DIFFERENT value; had he
        left it alone the ghost would have been fully opaque.
        """
        if not hwnd:
            return False
        if self._alpha == alpha and self._ops.get_alpha(hwnd) == alpha:
            return True
        taken = self._ops.set_alpha(hwnd, alpha)
        kept = self._ops.get_alpha(hwnd)
        ok = bool(taken) and kept == alpha
        self._alpha = alpha if ok else None
        self._record("alpha", level="info" if ok else "error",
                     asked=alpha, got=kept, accepted=bool(taken), reason=reason)
        return ok

    # --- what the follower asks for -----------------------------------------

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

    def apply(self, hwnd: int, want: Visibility,
              *, game_hwnd: int = 0) -> PlaceOutcome | None:
        """Make the window match one decision. Returns the placement it made.

        The caller must have had `prepare()` say `ok` first; this does not
        check again, because the order is the point and one owner of it is
        clearer than two.

        **Being on his screen and being VISIBLE to him are different things
        here.** The window stays up over the game picture whatever happens,
        because a WebView2 that is not on screen presents no frames at all; it
        is the ALPHA that goes to and from zero. `ShowWindow` is reserved for
        the cases with no picture to sit over -- the emulator gone, minimized
        or too small, or the session not wanting a ghost at all -- where there
        is nothing for the page to be under anyway.

        The opacity he chose is reached only on a PROVED placement. An
        unproved one parks the window transparent rather than leaving an
        unknown rectangle visible over somebody else's app.
        """
        if not hwnd:
            return None
        if want.rect is None:
            self._placed = None
            self._hold, self._hold_reason = CLEAR, want.reason
            self.ensure_alpha(hwnd, CLEAR, reason=want.reason)
            self._visibility(hwnd, False, want.reason)
            return None
        outcome = self._place(hwnd, want.rect, game_hwnd)
        self._placed = want.rect if outcome.ok else None
        alpha = want.alpha if outcome.ok else CLEAR
        reason = want.reason if outcome.ok else outcome.detail
        if alpha != CLEAR and not self._shown:
            # The window has never been on screen, so no page running in it
            # can have reported a picture yet. Park first; raise next tick.
            alpha, reason = CLEAR, "first_show"
        self._hold, self._hold_reason = alpha, reason
        self.ensure_alpha(hwnd, alpha, reason=reason)
        self._visibility(hwnd, True, reason)
        return outcome

    # --- the parts ----------------------------------------------------------

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
        if show and self._alpha is None:
            # NEVER on screen at an alpha nobody has read back. That is how a
            # ghost appears fully opaque: a window with no layered attributes
            # set is simply solid, and the first alpha of his second try
            # failed silently. Stay off and try again next tick.
            show, reason = False, "alpha_unconfirmed"
        if show == self._shown:
            return
        self._shown = show
        if show:
            self._ops.show_no_activate(hwnd)
        else:
            self._ops.hide(hwnd)
        self._record("visibility", level="info", show=show, reason=reason,
                     visible=bool(self._ops.is_visible(hwnd)))
