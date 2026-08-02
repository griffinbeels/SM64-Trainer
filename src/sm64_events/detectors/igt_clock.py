# src/sm64_events/detectors/igt_clock.py
"""Usamune IGT at a touch frame — the SHARED clock behind every grab time.

Extracted from star_grab.py 2026-06-12 (logic unchanged) so the Bowser-3
grand star reports the SAME authoritative time as a collectable star. The
grand star enters ACT_JUMBO_STAR_CUTSCENE and fires key_grabbed, never
star_collected (detectors/key.py), so key.py composes this clock to stamp
the grab with Usamune's exact displayed time — otherwise a grand-star segment
falls back to the wall-frame delta, which is one display-tick short of
Usamune and counts paused frames (live report 2026-06-12: 0'46"23 vs 0'46"26).

Source precedence (see the original rationale in star_grab.py's docstring):
1. "result"  — USAMUNE_STAR_RESULT, the exact number Usamune displays, used
   when its write is believed for THIS moment (the two callers below differ
   only in what makes a write believable).
2. "counter" — USAMUNE_OVERALL back-computed to the asked-for frame, + the
   one-frame display tick. Pause-safe: the overall counter is Usamune's own
   IGT, so it never counts paused frames (unlike a wall-frame delta). The
   manual's footnote — "the in-game timer keeps running internally" — is what
   makes this readable even when Usamune has stopped displaying it, confirmed
   under every STOP value 2026-08-01.
3. "reconstructed" — when the overall counter was RESET within a blink of the
   moment (reset-spamming between attempts), report the clock of the attempt
   that actually earned the grab, extrapolated to that frame.

TWO MOMENTS, one clock. `igt_at` answers "what was on Usamune's timer when
Mario TOUCHED this", which is the whole event for a pipe (warp.py) and for the
Bowser-3 grand star (key.py). `igt_at_xcam` answers "what does Usamune STOP
at", which for a collectable star is a later frame and is the only number a
leaderboard accepts — see its docstring and detectors/star_grab.py.
"""
from collections import deque

from sm64_events.core.snapshot import GameSnapshot
# ONE definition of "the counter is back at zero", shared with the detector
# that turns the same edge into an anchor — two thresholds would mean two
# opinions about whether a run just restarted.
from sm64_events.detectors.anchors import NEAR_ZERO_IGT


class IgtClock:
    HISTORY_FRAMES = 150       # keep ~5 s of game time
    RESET_GRACE_FRAMES = 30    # a grab < 1 s after an IGT reset concluded the PRIOR attempt
    DISPLAY_TICK = 1           # counter path: Usamune's display is one tick ahead
    RESULT_FRESH_FRAMES = 15   # the result write lands within a few frames of the touch
    # An area load and the counter zero it causes are not simultaneous to a
    # 60 Hz poll of a 30 fps game: the area byte and the counter are separate
    # reads in a twelve-read snapshot, and a cross-level warp is documented to
    # move the area byte a whole poll after the level byte. Ten frames is far
    # wider than either skew and far narrower than any gap between two real
    # transitions, and the cost of pairing them WRONGLY is latency, never a
    # wrong number — see counter_may_be_subarea_local.
    AREA_LOAD_WINDOW = 10

    def __init__(self):
        # (global_timer, igt_overall, igt_result) samples
        self._history: deque[tuple[int, int, int]] = deque()
        # (frame, level, area, overall) of the previous observation — the
        # basis tracking below needs the level and area, which the sample
        # history deliberately does not carry.
        self._last: tuple[int, int, int, int] | None = None
        self._area_edge_frame: int | None = None
        self._zero_frame: int | None = None
        self._subarea_local = False

    def empty(self) -> bool:
        return not self._history

    def counter_may_be_subarea_local(self) -> bool:
        """Is `USAMUNE_OVERALL`'s zero point an AREA load rather than the start
        of this run — i.e. does it measure part of the star instead of all of
        it? (star_grab.py reads this to decide whether it can trust its own
        derivation enough to publish immediately.)

        MAY, not IS. An L-reset taken inside a subarea also zeroes the counter
        beside an area change, and no signal in a snapshot separates the two:
        measured 2026-08-01 across his whole journal, of 477 in-course
        area-load anchors only 6% carry a warp action and 6% a nearby
        `warp_entered`, while 21% of the ones that are plainly resets do. So
        this answers the SAFE half of the question — a false True costs the
        settle wait on a grab that did not need it (~23% of his grabs, the
        ones after a subarea reset), and a false False is caught by the
        correction path. Both failure directions are recoverable, which is the
        only reason a heuristic is allowed here at all; the ATTEMPT side of
        the same ambiguity has no such luxury and is deliberately unfixed."""
        return self._subarea_local

    def observe(self, snap: GameSnapshot) -> None:
        h = self._history
        if h and snap.global_timer < h[-1][0]:
            h.clear()  # time went backward (savestate / console reset)
        if not h or snap.global_timer > h[-1][0]:
            h.append((snap.global_timer, snap.igt_overall, snap.igt_result))
        cutoff = snap.global_timer - self.HISTORY_FRAMES
        while h and h[0][0] < cutoff:
            h.popleft()
        self._track_basis(snap)

    def _track_basis(self, snap: GameSnapshot) -> None:
        """Remember WHY the counter last zeroed, so a reader can ask whether
        its zero point is the start of the star or the start of a subarea."""
        prev, self._last = self._last, (snap.global_timer, snap.curr_level,
                                        snap.curr_area, snap.igt_overall)
        if prev is None:
            return
        frame, level, area, overall = prev
        if snap.curr_level != level:
            # A level entry rebases the counter legitimately: it measures from
            # the door, which IS the whole star. Clears the pairing so a
            # previous level's area edge cannot reach across.
            self._area_edge_frame = self._zero_frame = None
            self._subarea_local = False
            return
        if snap.curr_area != area:
            self._area_edge_frame = snap.global_timer
            if (self._zero_frame is not None
                    and snap.global_timer - self._zero_frame
                    <= self.AREA_LOAD_WINDOW):
                self._subarea_local = True   # the edge was seen after the zero
        if snap.igt_overall < overall and snap.igt_overall <= NEAR_ZERO_IGT:
            self._zero_frame = snap.global_timer
            self._subarea_local = (
                self._area_edge_frame is not None
                and snap.global_timer - self._area_edge_frame
                <= self.AREA_LOAD_WINDOW)

    def igt_at(self, touch_frame: int, curr: GameSnapshot) -> tuple[int, str]:
        """Usamune's number at a TOUCH — used by key.py (grand star) and
        warp.py (pipe), where the touch is the whole event."""
        return self._reading(touch_frame, curr, self._result_is_fresh)

    def igt_at_xcam(self, xcam_frame: int, curr: GameSnapshot) -> tuple[int, str]:
        """Usamune's number at the X-CAM moment — the leaderboard-legal time.

        Same arithmetic, one different question of the result store: it is
        believed only when its write landed AT OR AFTER the x-cam frame.
        A write BEFORE it is Usamune's grab-time write, and taking that is
        exactly the bug this exists to fix — under `STOP=GrabX` Usamune writes
        the store TWICE (manual: "stops the timer on star-grab first, then
        updates the timer on Xcam"), and the first write sat inside
        `RESULT_FRESH_FRAMES` of every short fall. Measured 2026-08-01: 9-28
        frames low across three grabs under GrabX, and the gap was not constant,
        so no offset could have fixed it.

        On a GROUND grab the two frames coincide, so Usamune's own write is
        still taken verbatim and that path stays torn-read-free."""
        return self._reading(xcam_frame, curr, self._result_written_at_or_after)

    def settled_result_at_or_after(self, xcam_frame: int,
                                   curr: GameSnapshot) -> int | None:
        """Usamune's OWN written answer for an x-cam, or None if it never
        wrote one after that moment (`STOP` of Grab or None).

        Worth waiting for rather than deriving, and the reason is not
        precision: `USAMUNE_OVERALL` is **subarea-local**. It restarts at an
        area warp inside a level, so on a multi-area star our counter measures
        the time since entering the subarea and Usamune's store holds the whole
        star. Live 2026-08-01, his own report — LLL "Hot-Foot-It into the
        Volcano" read 0'40"63 against Usamune's 0'52"46 (356 frames), and SSL
        "Inside the Ancient Pyramid" 0'02"43 against 0'19"13 (502 frames), while
        the nine single-area stars in the same run matched exactly."""
        samples = list(self._history)
        samples.append((curr.global_timer, curr.igt_overall, curr.igt_result))
        if not curr.igt_result:
            return None
        if not self._result_written_at_or_after(xcam_frame, samples):
            return None
        return curr.igt_result

    def _reading(self, frame: int, curr: GameSnapshot, believe_result
                 ) -> tuple[int, str]:
        samples = list(self._history)
        samples.append((curr.global_timer, curr.igt_overall, curr.igt_result))
        candidate, source = self._primary(frame, curr, samples, believe_result)
        # Reset-race guard, regardless of source: when the overall counter
        # was reset within a blink of the moment, even Usamune's own result
        # write holds the post-reset near-zero time — but the grab concluded
        # the attempt that was being played.
        for (g_a, ov_a, _), (_, ov_b, _) in zip(reversed(samples[:-1]),
                                                reversed(samples[1:])):
            if ov_b >= ov_a:
                continue  # running (or paused); not a reset
            # overall counter was reset in the game-frame gap after g_a
            if frame <= g_a or candidate < self.RESET_GRACE_FRAMES:
                prior = max(0, ov_a + (frame - g_a))
                return prior + self.DISPLAY_TICK, "reconstructed"
            break
        return candidate, source

    def _primary(self, frame: int, curr: GameSnapshot,
                 samples: list[tuple[int, int, int]],
                 believe_result) -> tuple[int, str]:
        if curr.igt_result and believe_result(frame, samples):
            return curr.igt_result, "result"  # the exact displayed number
        post = max(0, curr.igt_overall - (curr.global_timer - frame))
        return post + self.DISPLAY_TICK, "counter"

    def _last_result_write_after(self, samples: list[tuple[int, int, int]]
                                 ) -> int | None:
        """The last frame KNOWN to precede a change in the result store, or
        None when it was never observed changing (a stale prior result)."""
        latest = samples[-1][2]
        for g, _, res in reversed(samples[:-1]):
            if res != latest:
                return g
        return None

    def _result_is_fresh(self, touch_frame: int,
                         samples: list[tuple[int, int, int]]) -> bool:
        """True when the result store changed within a few frames of the
        touch — i.e., it was written for THIS grab, not a previous star."""
        before = self._last_result_write_after(samples)
        return before is not None and before >= touch_frame - self.RESULT_FRESH_FRAMES

    def _result_written_at_or_after(self, xcam_frame: int,
                                    samples: list[tuple[int, int, int]]) -> bool:
        """True when the write can only have landed on or after `xcam_frame`.

        The write is bracketed: it happened somewhere after the last sample
        still holding the old value, so `before + 1` is the earliest frame it
        can have been. Requiring that to reach the x-cam frame is deliberately
        the conservative half of the bracket — a rejected write costs a fall
        through to the counter, which is the same number give or take our own
        one-frame read skew, while a wrongly ACCEPTED one is the grab-time
        value and is wrong by however long Mario was in the air."""
        before = self._last_result_write_after(samples)
        return before is not None and before + 1 >= xcam_frame
