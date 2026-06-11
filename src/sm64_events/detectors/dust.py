# src/sm64_events/detectors/dust.py
"""Dust tricks: launch actions chained out of a landing action, where the
number of visible landing frames measures how late the input was.

THE registry for chainable dust tricks is TRICKS below — adding a trick
(side flip, long jump...) is one row; detection, payloads, and the live
verify harness pick it up automatically. Per-attempt aggregation is in
tracking/projection.py and keys off the event_type.

Timing model (decomp-verified 2026-06-11, confirmed by a 50-trial live
session; addresses.py carries the quoted evidence): an air action's landing
runs `set_mario_action(<landing>); break;` — the landing action sits in
memory at the END of the landing frame but its function (whose A/B cancel
is checked BEFORE the dust-generating step) first RUNS the next frame.
Cancels out of the landing DO re-execute same-frame. Therefore:

  visible landing frames N >= 1 always;
  frames_late = N - 1;  N == 1 is the frame-perfect, dustless input;
  a direct air->launch edge (N == 0) cannot occur.

frames_late counts DISTINCT game frames (deduped by global_timer: 60 Hz
polling sees each 30 fps frame ~twice, per the poller's every-frame-observed
contract). "Dustless" is defined by input timing, not the visual puff —
jump-landing dust additionally requires forwardVel > 16 (decomp
common_landing_action), so a slow late jump shows no dust on screen;
PARTICLE_DUST in particle_flags stays a corroborating read only.

Refusal over guessing: if the landing's ENTRY edge was never observed
(attach/reconnect mid-slide, savestate load mid-landing — any backward
global_timer jump resets tracking), the count is not trustworthy and the
launch emits NOTHING rather than a wrong classification."""
from dataclasses import dataclass, field

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import (ACT_DIVE_SLIDE, ACT_DOUBLE_JUMP,
                                          ACT_DOUBLE_JUMP_LAND,
                                          ACT_JUMP_LAND, ACT_TRIPLE_JUMP,
                                          ROLLOUT_ACTIONS)


@dataclass(frozen=True)
class DustTrick:
    event_type: str            # wire event type ("rollout", "jump", ...)
    landing: int               # action whose visible frames measure lateness
    launches: frozenset        # actions that complete the trick
    extra: dict = field(default_factory=dict)  # static payload extras


TRICKS = (
    DustTrick("rollout", ACT_DIVE_SLIDE, ROLLOUT_ACTIONS),
    DustTrick("jump", ACT_JUMP_LAND, frozenset({ACT_DOUBLE_JUMP}),
              {"kind": "double"}),
    DustTrick("jump", ACT_DOUBLE_JUMP_LAND, frozenset({ACT_TRIPLE_JUMP}),
              {"kind": "triple"}),
)


class _TrickState:
    __slots__ = ("tracking", "count", "last_frame")

    def __init__(self):
        self.tracking = False    # True only after the entry edge was SEEN
        self.count = 0           # distinct landing frames observed
        self.last_frame = None


class DustTrickDetector:
    def __init__(self):
        self._state = [_TrickState() for _ in TRICKS]

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.global_timer < prev.global_timer:
            for st in self._state:
                st.tracking = False
            return []
        events = []
        for trick, st in zip(TRICKS, self._state):
            if curr.mario_action == trick.landing:
                if prev.mario_action != trick.landing:
                    st.tracking, st.count, st.last_frame = True, 0, None
                if st.tracking and curr.global_timer != st.last_frame:
                    st.count += 1
                    st.last_frame = curr.global_timer
                continue
            if (st.tracking and prev.mario_action == trick.landing
                    and curr.mario_action in trick.launches):
                frames_late = max(0, st.count - 1)
                events.append(Event(
                    type=trick.event_type, frame=curr.global_timer,
                    timestamp_utc=curr.wall_time_utc,
                    payload={"dustless": frames_late == 0,
                             "frames_late": frames_late,
                             "landing_frames": st.count,
                             "level": curr.curr_level, **trick.extra}))
            st.tracking = False
        return events
