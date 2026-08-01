"""warp_entered: edge into a warp-entry action (pipe touch, teleporter).
The community-comparable moment for 'entered the pipe' segments — the level
edge that follows adds constant fade time, so the matcher anchors on this.
Edge on the already-sampled mario_action; level/area context rides in the
payload so triggers can scope it.

igt: the pipe touch carries Usamune's IGT from the SHARED clock
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

The pipe touch has no Usamune RESULT write of its own (that store is
star-only), so this always takes the clock's `counter` source; a star grabbed
earlier in the same run leaves a stale result behind and IgtClock's own
freshness rule is what keeps it out (test_warp.py pins that). The clock must
see every tick, so it is observed in process() whether or not a warp fires —
this detector is therefore no longer stateless."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.memory.addresses import WARP_ENTRY_ACTIONS


class WarpDetector:
    def __init__(self):
        self._clock = IgtClock()

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if self._clock.empty():
            self._clock.observe(prev)
        events = self._detect(prev, curr)
        self._clock.observe(curr)
        return events

    def _detect(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        entered = (curr.mario_action in WARP_ENTRY_ACTIONS
                   and prev.mario_action not in WARP_ENTRY_ACTIONS)
        if not entered:
            return []
        # The touch frame IS the observed edge frame: unlike a star dance,
        # ACT_DISAPPEARED counts down actionArg rather than actionTimer
        # (decomp act_disappeared), so there is no action-timer backdating to
        # be had here — the event's own `frame` and the frame the IGT is read
        # at stay the same number, whatever the poll phase was.
        igt_frames, source = self._clock.igt_at(curr.global_timer, curr)
        return [Event(type="warp_entered", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"level": curr.curr_level,
                               "area": curr.curr_area,
                               "action": curr.mario_action,
                               "igt_frames": igt_frames,
                               "igt": format_igt(igt_frames),
                               "igt_source": source})]
