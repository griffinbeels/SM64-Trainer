"""warp_entered: the ENTRANCE TOUCH — the frame Mario collides with a
painting, portal, hole or pipe — published the moment the game has written
where it leads, which for a painting is the touch frame itself.

The community-comparable moment for a movement or a pipe-entry segment. The
level edge that follows adds a constant fade: measured over 140 castle entries
in the repo journal, **77 frames** for a painting/portal (range 76-77) and
**23** for a pipe (range 23-23). A movement measured to the load therefore
reports the travelling plus the fade — on `SSL → LLL` the fade is 60% of the
recorded time.

## The destination is readable AT THE TOUCH — live round, 2026-08-05

For one day this detector WITHHELD the event until the level byte moved, on
the belief that the touch could not name its own destination. That belief came
from reading decomp rather than from watching RAM, and it cost 2.57 s of dead
air between hitting the DDD portal and the practice-log row appearing — which
is what he reported, three rounds running, and then ruled on: *"we should never
be injecting that much artificial waiting into the system… if I can see the
timer on my screen, we should be able to detect it as well."*

`tools/probe_warp_block.py`, 15 consecutive castle entries, is what settled it
(the numbers and the address walk are in addresses.py beside `WARP_DEST_TYPE`):

  * a PAINTING or PORTAL writes `sWarpDest` at or before the frame Mario's
    action becomes ACT_DISAPPEARED. 13 of 13 already named the destination at
    the touch, 76-78 frames early, and 12 of those differed from the previous
    warp's destination — so it is a real write, not a stale read.
  * a PIPE is the one genuinely delayed warp: `sDelayedWarpOp` pulses 0x04 a
    frame or two after the touch, counts 20 frames down, and only then is
    `sWarpDest` written — 3 frames before the level byte moves. Both pipe
    touches read a STALE castle destination at the touch frame.

Those two pipes are why `type != 0` cannot be the test on its own: it also
survives a COMPLETED painting warp (read live standing idle in DDD). Freshness
is therefore "was the struct just written" — all four bytes watched for a
change, with the two stale pipe reads as the negative cases that prove it.

The destination matters at all because the castle basement alone hosts five
exits (HMC, LLL, SSL, DDD, BitFS): an end condition reading only "a warp in
the castle" would let walking into HMC record a false MIPS Clip success.

`frame` and the IGT are the TOUCH's, always — reading the clock at a later
release would measure the fade, which is exactly the bug the 2026-07-31 pipe
fix removed. Because a released event may describe the PAST, this detector
runs BEFORE `LevelChangeDetector` in `main.build_detectors` (see its
docstring); journaling them the other way round lets the level change close
the attempt the touch belonged to, and one movement records as two.

PUBLISH, in the order checked:
  * the destination struct was just written -> `to` = its level. This is the
    touch frame itself for a painting, and touch+20 for a pipe;
  * a level edge -> `to` = the new level;
  * an area edge -> `to` = the (unchanged) level, an in-level warp;
  * `global_timer` jumping backward (console reset) -> `to` = None;
  * HOLD_CAP_FRAMES elapsed -> `to` = None. This is what covers an in-level
    teleporter (CCM broken bridge, WDW corners; every one
    ACT_TELEPORT_FADE_OUT), which relocates Mario inside his own area and so
    produces no edge to wait for at all.

The last four bounds are why nothing that fired before this change can stop
firing: a hold with no clock is how an event disappears. `to` is therefore
`int | None` — the level Mario ended up in, or None when the warp kept him
where he was or was aborted.

`pending_warp_op` is NOT one of the bounds, and the failed attempt to make it
one is why the list above ends at a struct write rather than a flag. The game
clears `sDelayedWarpOp` when the delayed warp INITIATES, ~57 frames before the
level byte moves — **the flag goes quiet in the MIDDLE of the wait, not at the
end of it** — so a grace window on it published `to: None` on every real
painting entry (his journal, ids 25415 and 25371).

HOLD_CAP_FRAMES 240 is measured with headroom rather than chosen: the observed
fades are 77 frames for a painting/portal and 23 for a pipe, so this is 3x the
slower one. It bounds only the case where NO edge ever arrives, and nothing
consumes a teleporter's touch, so latency there costs nothing while a
too-short bound costs the destination.

FRESH_WINDOW_FRAMES 4 is slack for the poller, not for the game: the write and
the action edge land on the same game frame, but the poll runs at ~120 Hz over
a 30 fps game and a stalled read can miss one. Widening it trades toward
believing a stale struct, so it stays at single digits.

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


def _destination(snapshot: GameSnapshot) -> tuple[int, int, int, int]:
    return (snapshot.warp_dest_type, snapshot.warp_dest_level,
            snapshot.warp_dest_area, snapshot.warp_dest_node)


class WarpDetector:
    HOLD_CAP_FRAMES = 240
    FRESH_WINDOW_FRAMES = 4

    def __init__(self):
        self._clock = IgtClock()
        self._held: dict | None = None
        self._dest: tuple[int, int, int, int] | None = None
        self._dest_written_at: int | None = None

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if self._clock.empty():
            self._clock.observe(prev)
        self._watch_destination(curr)
        events = self._release(prev, curr) + self._touch(prev, curr)
        self._clock.observe(curr)
        return events

    def _watch_destination(self, curr: GameSnapshot) -> None:
        """Stamp the frame `sWarpDest` last changed — the freshness test.

        The FIRST value seen is deliberately not stamped: a detector built
        mid-session would otherwise read whatever warp preceded it as one that
        had just been written, and publish a stale destination on the very
        first touch. Unstamped means "hold", which is the safe direction.
        """
        dest = _destination(curr)
        if self._dest is None:
            self._dest = dest
            return
        if dest != self._dest:
            self._dest = dest
            self._dest_written_at = curr.global_timer

    def _destination_is_live(self, curr: GameSnapshot) -> bool:
        return (curr.warp_dest_type != 0
                and self._dest_written_at is not None
                and 0 <= curr.global_timer - self._dest_written_at
                <= self.FRESH_WINDOW_FRAMES)

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
        if self._destination_is_live(curr):
            return self._publish(curr.warp_dest_level)
        return []

    def _release(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        held = self._held
        if held is None:
            return []
        if self._destination_is_live(curr):
            return self._publish(curr.warp_dest_level)
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
