"""warp_entered: the ENTRANCE TOUCH — the frame Mario collides with a
painting, portal, hole or pipe — held until it knows where it leads.

The community-comparable moment for a movement or a pipe-entry segment. The
level edge that follows adds a constant fade: measured over 140 castle entries
in the repo journal, **77 frames** for a painting/portal (range 76-77) and
**23** for a pipe (range 23-23). A movement measured to the load therefore
reports the travelling plus the fade — on `SSL → LLL` the fade is 60% of the
recorded time.

## Why the event is HELD (2026-08-04, task 0081)

The touch cannot name its own destination, and this is a fact about the game
rather than a gap in our reads. decomp `src/game/level_update.c` (fetched
2026-08-04): `level_trigger_warp()` sets `sDelayedWarpOp`, `sDelayedWarpTimer`
and `sSourceWarpNodeId` and writes NOTHING to `sWarpDest`. That struct is
filled 77 frames later by `initiate_delayed_warp()` -> `initiate_warp()`,
immediately before the level unloads. So reading `sWarpDest` at the touch
returns the PREVIOUS warp's destination — stale and entirely plausible, the
worst failure shape available.

The destination matters because the castle basement alone hosts five exits
(HMC, LLL, SSL, DDD, BitFS): an end condition reading only "a warp in the
castle" would let walking into HMC record a false MIPS Clip success.

Hence a HELD EMIT, the same shape `StarGrabDetector` uses for the x-cam:
record the touch, publish it back-dated once an edge answers the question.
`frame` and the IGT are the TOUCH's, always — reading the clock at release
would measure the fade, which is exactly the bug the 2026-07-31 pipe fix
removed. Because a released event describes the PAST, this detector runs
BEFORE `LevelChangeDetector` in `main.build_detectors` (see its docstring);
journaling them the other way round lets the level change close the attempt
the touch belonged to, and one movement records as two.

RELEASE, in the order checked:
  * a level edge -> `to` = the new level;
  * an area edge -> `to` = the (unchanged) level, an in-level warp;
  * `global_timer` jumping backward (console reset) -> `to` = None;
  * HOLD_CAP_FRAMES elapsed -> `to` = None. This is what covers an in-level
    teleporter (CCM broken bridge, WDW corners; every one
    ACT_TELEPORT_FADE_OUT), which relocates Mario inside his own area and so
    produces no edge to wait for at all.

The last two bounds are why nothing that fired before this change can stop
firing: a hold with no clock is how an event disappears. `to` is therefore
`int | None` — the level Mario ended up in, or None when the warp kept him
where he was or was aborted.

## `pending_warp_op` CANNOT release this early — live round, 2026-08-05

A grace window on that flag looked like the precise way to resolve a
teleporter promptly, and it published `to: None` on every real painting entry
instead. The game clears `sDelayedWarpOp` when the delayed warp INITIATES —
`sDelayedWarpTimer` is 20 — and there are ~57 more frames of fade before the
level byte moves. **The flag goes quiet in the MIDDLE of the wait, not at the
end of it.** His journal, ids 25415 and 25371: touch at frame 2519145,
`level_changed 6 -> 23` at 2519222, exactly 77 frames apart, and the event
published "destination unknown" around frame 30 of that — so MIPS Clip kept
timing to the DDD load, which is the whole thing this was built to stop.

HOLD_CAP_FRAMES 240 is measured with headroom rather than chosen: the observed
fades are 77 frames for a painting/portal (range 76-77 over 140 entries) and 23
for a pipe, so this is 3x the slower one. It bounds only the case where NO edge
ever arrives, and nothing consumes a teleporter's touch, so latency there costs
nothing while a too-short bound costs the destination.

igt: the touch carries Usamune's IGT from the SHARED clock
(detectors/igt_clock.py), exactly like a star or key grab, so a segment
ending here records Usamune's own number instead of a wall-frame delta
(domain rule 3). Live report 2026-07-31: BitDW "No Reds" displayed 0'35"90
where Usamune showed 0'35"96 — two frames, and NOT a constant offset to
correct for. `close.frame - arm.start_frame` measures from the frame the
ANCHOR DETECTOR OBSERVED Usamune's counter drop, which is the zero frame or
one frame after it depending on which 60 Hz poll caught the 30 Hz drop; and
it counts paused frames, which Usamune's counter never does. Measured over
626 real star attempts of the same shape (grab close, anchor arm) in the
user's own journal: Usamune's displayed time minus the frame delta was +1 on
57%, +2 on 21%, -1 on 10%, 0 on 2%, plus a long negative tail wherever the
player paused. Reading the counter AT THE CLOSE removes the arm-frame
alignment from the answer entirely.

The touch has no Usamune RESULT write of its own (that store is star-only),
so this always takes the clock's `counter` source; a star grabbed earlier in
the same run leaves a stale result behind and IgtClock's own freshness rule is
what keeps it out (test_warp.py pins that). The clock must see every tick, so
it is observed in process() whether or not a touch fires — this detector has
not been stateless since 2026-07-31, and now holds a pending touch as well."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.memory.addresses import WARP_ENTRY_ACTIONS


class WarpDetector:
    HOLD_CAP_FRAMES = 240

    def __init__(self):
        self._clock = IgtClock()
        self._held: dict | None = None

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if self._clock.empty():
            self._clock.observe(prev)
        events = self._release(prev, curr) + self._touch(prev, curr)
        self._clock.observe(curr)
        return events

    def _touch(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        entered = (curr.mario_action in WARP_ENTRY_ACTIONS
                   and prev.mario_action not in WARP_ENTRY_ACTIONS)
        if not entered or self._held is not None:
            return []
        # The touch frame IS the observed edge frame: unlike a star dance,
        # ACT_DISAPPEARED counts down actionArg rather than actionTimer
        # (decomp act_disappeared), so there is no action-timer backdating to
        # be had here — the event's own `frame` and the frame the IGT is read
        # at stay the same number, whatever the poll phase was.
        igt_frames, source = self._clock.igt_at(curr.global_timer, curr)
        self._held = {"frame": curr.global_timer, "level": curr.curr_level,
                      "area": curr.curr_area, "action": curr.mario_action,
                      "igt_frames": igt_frames, "igt_source": source,
                      "wall_time_utc": curr.wall_time_utc}
        return []

    def _release(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        held = self._held
        if held is None:
            return []
        if curr.curr_level != prev.curr_level:
            return self._publish(curr.curr_level)
        if curr.curr_area != prev.curr_area:
            return self._publish(curr.curr_level)
        if curr.global_timer < held["frame"]:
            return self._publish(None)
        if curr.global_timer - held["frame"] >= self.HOLD_CAP_FRAMES:
            return self._publish(None)
        return []

    def _publish(self, to: int | None) -> list[Event]:
        held, self._held = self._held, None
        return [Event(type="warp_entered", frame=held["frame"],
                      timestamp_utc=held["wall_time_utc"],
                      payload={"level": held["level"], "area": held["area"],
                               "action": held["action"], "to": to,
                               "igt_frames": held["igt_frames"],
                               "igt": format_igt(held["igt_frames"]),
                               "igt_source": held["igt_source"]})]
