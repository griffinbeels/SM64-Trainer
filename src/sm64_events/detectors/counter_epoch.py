# src/sm64_events/detectors/counter_epoch.py
"""WHY Usamune's overall counter last restarted — one answer, two readers.

Usamune's `USAMUNE_OVERALL` is not a star's clock; it is a LEG's clock. It
restarts when Mario retries, when he warps deeper into a level, when he walks
back OUT of a subarea, and when he takes an in-level teleporter, and nothing
in a snapshot says which of those just happened. Two detectors need that
answer and used to derive it independently:

* `anchors.py` — to stamp `area_load` / `teleport` on the anchor, so the
  attempt projector does not read a pyramid door as a retry;
* `igt_clock.py` — to know whether the counter in front of it measures the
  whole star or only the last leg of one.

The anchor's version was measured against the live journal (its docstring
carries the four readings that do not work and the one that does). The clock's
was a private heuristic — "the edge went back to area 1" — and it was wrong in
the expensive direction: it could not tell a retry's own reload from a warp, so
it flagged the counter unusable and the star's row waited 1.5 s for a
correction that was never coming. **17% of measured grabs paid that wait**
(2026-08-04 audit, task 0083). One module, one answer, and the wait has nothing
left to justify it.

The classification is deliberately a fact about the RESTART rather than about
the star: `EpochTracker.banked` accumulates one leg per involuntary restart and
returns to zero on a real attempt start, so the whole star is always
`banked + the counter's current value`. Measured over 875 grabs in the live
journal: 851 cross no involuntary restart at all, 22 cross one, and **2 cross
two — the two CCM 100-coin rows that published 37 seconds short**. A single
cached leg could never have covered those; an accumulator covers any number by
construction.

TWO SIGNALS SAY A RESTART WAS THE LEVEL'S, and the second closed the residual
the first carried (task 0117, 2026-09-04). The destination area — an edge into
a non-1 area is Mario going deeper — was measured first and is kept. It cannot
see a walk back OUT: the CCM slide's cabin door lands in area 1, the same shape
as a retry's reload, so Slip Slidin' Away dropped its bank at the door,
published the 7 s since it, and took Usamune's 49"10 as a correction 1.5 s
later — "it takes FOREVER for us to show the actual final, aggregate time".
The game's own warp machinery separates them: a WALKED warp (door, warp
object) pends `sDelayedWarpOp` with a countdown and FIRES when that countdown
reaches zero with the op still pending, while a reset never fires one — Usamune
zeroes the op and the countdown together (probe_warp_block, 2026-08-11). Read
back off every journal on this machine: the op was still pending at the zero
on 175 of 176 subarea entries and every one of the 25 door exits, and at a
cancelled ride it had vanished with the countdown still running. The one entry
with a stale op is why the destination reading stays beside it.
"""
from dataclasses import dataclass

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import (ACT_TELEPORT_FADE_OUT, CASTLE_LEVELS,
                                          WARP_OP_WARP_DOOR,
                                          WARP_OP_WARP_OBJECT)

# How far apart the area edge and the counter zero may be and still be one
# event. Not zero: a 60 Hz poll of a 30 fps game reads them on different polls
# of the same game frame, and the area byte is documented to move a poll after
# the level byte.
AREA_LOAD_WINDOW = 10
# Same poll-skew allowance for the in-level teleporter: the counter zeroes 1
# frame after the last fade-out tick on all three demonstrated warps, and
# inputs are locked for the whole fade, so no retry can hide in here.
TELEPORT_WINDOW = 10
COURSE_START_AREA = 1  # every course spawns Mario here; only the castle differs
# How long a LEVEL load keeps moving the area byte. ARRIVING in a course is not
# a warp deeper into one, but frame by frame they look alike: the level byte
# changes once and the area byte then SETTLES (entering SSL walks it 3->2->1
# over ~47 frames), so the load's own edges land long after the level edge that
# explains them and one of them is into a non-1 area. Measured across 911 level
# entries: every edge belonging to a load lands within 59 frames, clustering at
# 44-49, and the earliest genuine warp deeper appears at 60. Skipping them
# changes NO anchor classification in the live journal (0 of 29 `area_load`
# anchors sit inside a tail, measured 2026-08-04) — it only stops the clock
# banking a leg for a door Mario never walked through.
LEVEL_LOAD_TAIL_FRAMES = 60
# How long after a walked warp FIRES its counter zero may land. The area byte
# moves 2 frames after the countdown hits zero and the op clears after that,
# and the zero lands beside the edge: `frames_since_warp_op` on the anchor
# read 0 at 131 level-caused zeros, 1-3 at 44, and 10 once (every journal,
# 2026-09-04). Thirty is twice the slowest of those, and still under the 34
# frames to the closest retry that ever followed a load.
WARP_FIRED_WINDOW = 30
# The ops a walked warp between AREAS pends. The in-level teleporter (0x05)
# zeroes the counter too, and `is_teleport` already reads it off Mario's
# fade-out; a level exit is its own boundary whatever op it pended.
AREA_WARP_OPS = frozenset({WARP_OP_WARP_DOOR, WARP_OP_WARP_OBJECT})


@dataclass
class EpochTracker:
    """The counter's epoch: when its zero point was set, and by what.

    Feed it every snapshot pair. Ask it, at a restart, whether the restart was
    involuntary — and ask it, at any moment, what the star had already banked
    before the leg now running.
    """
    #: (frame, area entered) of the last in-level area edge, or None.
    last_area_edge: tuple[int, int] | None = None
    #: Last tick an in-level teleporter's fade-OUT was observed, or None. The
    #: fade-IN action lingers for hundreds of frames and cannot be used here.
    last_teleport_frame: int | None = None
    #: (frame, op) of the last walked warp whose countdown reached zero with
    #: the op still pending — the level executing a door or a warp object.
    #: None until one fires, and None again after the restart it explains.
    last_warp_fired: tuple[int, int] | None = None
    #: Frames this star had already taken before the leg now running.
    banked: int = 0
    #: When the level byte last changed — everything the arrival then does to
    #: the area byte belongs to the arrival (LEVEL_LOAD_TAIL_FRAMES).
    level_edge_frame: int | None = None

    def observe(self, prev: GameSnapshot, curr: GameSnapshot) -> None:
        """Track the recencies the predicates read. Call once per tick,
        BEFORE asking anything about this tick."""
        if curr.global_timer < prev.global_timer:
            self._heal(curr)
        if curr.curr_level != prev.curr_level:
            # A level entry is the start of a run: the counter measures from
            # the door, which IS the whole star. Nothing carries across it, and
            # an in-level teleporter is over the moment the level moves — the
            # same action serves cap-course warps that really do leave. A
            # walked warp that fired INTO this level is the same boundary
            # (the BitDW pipe pends op 4 like a chimney does), so it is
            # forgotten here before it could bank the castle's counter.
            self.last_area_edge = None
            self.last_teleport_frame = None
            self.last_warp_fired = None
            self.banked = 0
            self.level_edge_frame = curr.global_timer
            return
        if curr.curr_area != prev.curr_area:
            self.last_area_edge = (curr.global_timer, curr.curr_area)
        if curr.mario_action == ACT_TELEPORT_FADE_OUT:
            self.last_teleport_frame = curr.global_timer
        if self._warp_fired(prev, curr):
            self.last_warp_fired = (curr.global_timer, curr.pending_warp_op)

    @staticmethod
    def _warp_fired(prev: GameSnapshot, curr: GameSnapshot) -> bool:
        """Did a walked warp's countdown just reach zero with its op still
        pending? That is the level EXECUTING the warp. A cancelled ride never
        gets here: the reset zeroes op and countdown on the same frame. The op
        must already have been seen on the previous poll so a read torn across
        the arming frame (op set, countdown not yet) cannot pass as a fire."""
        return (curr.pending_warp_op in AREA_WARP_OPS
                and prev.pending_warp_op == curr.pending_warp_op
                and prev.delayed_warp_timer > 0
                and curr.delayed_warp_timer == 0)

    def _heal(self, curr: GameSnapshot) -> None:
        """A savestate load rewinds the clock; recency that predates the jump
        describes another timeline (domain rule 4)."""
        if self.last_area_edge and curr.global_timer < self.last_area_edge[0]:
            self.last_area_edge = None
        if (self.last_teleport_frame is not None
                and curr.global_timer < self.last_teleport_frame):
            self.last_teleport_frame = None
        if self.last_warp_fired and curr.global_timer < self.last_warp_fired[0]:
            self.last_warp_fired = None

    def is_area_load(self, curr: GameSnapshot) -> bool:
        """Did the counter zero because the LEVEL moved Mario — into the
        pyramid, down the volcano, back out of the slide's cabin door — rather
        than because he retried?

        Two readings, either suffices. A walked warp FIRED just before the
        zero: the game's own countdown reached zero with the door or warp-
        object op still pending, which a reset never produces, and which does
        not care where the warp led — the slide's exit door back to area 1 is
        as much the level's doing as the chimney in. Or the destination is a
        NON-1 area: a course always starts in area 1, so an edge deeper is
        Mario going deeper while a retry's reload walks the byte back to 1.
        The second was measured first and covers the one entry in 176 whose op
        had gone stale; its derivation and the three readings that do not work
        are in `anchors.py`'s docstring."""
        if curr.curr_level in CASTLE_LEVELS:
            return False
        if self.last_warp_fired is not None:
            fired_frame, _ = self.last_warp_fired
            if curr.global_timer - fired_frame <= WARP_FIRED_WINDOW:
                return True
        if self.last_area_edge is None:
            return False
        frame, entered = self.last_area_edge
        if (self.level_edge_frame is not None
                and frame - self.level_edge_frame <= LEVEL_LOAD_TAIL_FRAMES):
            return False        # the arrival's own settling, not a door
        return (entered != COURSE_START_AREA
                and curr.global_timer - frame <= AREA_LOAD_WINDOW)

    def is_teleport(self, curr: GameSnapshot) -> bool:
        """Did the counter zero because Mario took an IN-LEVEL teleporter — the
        CCM broken bridge, a WDW corner — rather than because he retried?"""
        if self.last_teleport_frame is None:
            return False
        return curr.global_timer - self.last_teleport_frame <= TELEPORT_WINDOW

    def involuntary(self, curr: GameSnapshot) -> bool:
        """Was this restart something the LEVEL did to Mario, rather than a
        retry he asked for? The one question the accumulator turns on."""
        return self.is_area_load(curr) or self.is_teleport(curr)

    def restarted(self, curr: GameSnapshot, pre_zero_overall: int) -> None:
        """The counter just zeroed. Bank the leg it was measuring, or start a
        fresh star — whichever this restart was.

        `pre_zero_overall` is the last value observed BEFORE the zero, so a
        missed poll costs at most the one game frame of skew every reading here
        already carries; it is never back-computed across the gap.

        One fired warp explains ONE restart: the load's own zero takes it, and
        a retry right behind it — "enter, reset immediately" — finds nothing to
        claim and starts the star over."""
        if self.involuntary(curr):
            self.banked += max(0, pre_zero_overall)
        else:
            self.banked = 0
        self.last_warp_fired = None
