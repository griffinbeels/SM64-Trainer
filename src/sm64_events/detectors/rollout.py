# src/sm64_events/detectors/rollout.py
"""rollout: edge into ACT_*_ROLLOUT out of the dive chain.

The dustless trick: dive, then press A/B on the exact landing frame.
Decomp's execute_mario_action loops until the action is stable, so a
frame-perfect input chains ACT_DIVE_SLIDE -> ACT_*_ROLLOUT inside ONE
frame — memory shows a direct ACT_DIVE -> rollout edge and the dive-slide
frame never becomes visible. That absence IS the dustless signal. A late
rollout shows N dive-slide frames first (each one kicks up dust:
PARTICLE_DUST in particle_flags is the corroborating read, characterized
at the live gate); frames_late = N.

frames_late counts DISTINCT game frames observed in ACT_DIVE_SLIDE
(deduped by global_timer: 60 Hz polling sees each 30 fps frame ~twice,
per the poller's every-frame-observed contract). When the first observed
pair already sits mid-slide (attach race), prev==dive_slide still proves
at least one slide frame: frames_late is floored at 1.

Silent by design: rollouts entered from any other action (slide-kick
variants, reconnect mid-rollout) are not the practiced dive trick; the
spec's known suppressors (steep-slope INPUT_ABOVE_SLIDE, fall-damage
knockback) never reach a rollout action at all. Self-heals when
global_timer jumps backward (savestate/reset mid-slide)."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import (ACT_DIVE, ACT_DIVE_SLIDE,
                                          ROLLOUT_ACTIONS)


class RolloutDetector:
    def __init__(self):
        self._slide_frames = 0           # distinct frames observed in dive-slide
        self._last_slide_frame: int | None = None

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.global_timer < prev.global_timer:
            self._slide_frames = 0
            self._last_slide_frame = None
            return []
        events = []
        if (curr.mario_action in ROLLOUT_ACTIONS
                and prev.mario_action not in ROLLOUT_ACTIONS):
            if prev.mario_action == ACT_DIVE:
                events.append(self._event(curr, dustless=True, frames_late=0))
            elif prev.mario_action == ACT_DIVE_SLIDE:
                events.append(self._event(curr, dustless=False,
                                          frames_late=max(1, self._slide_frames)))
        if curr.mario_action == ACT_DIVE_SLIDE:
            if prev.mario_action != ACT_DIVE_SLIDE:
                self._slide_frames = 0       # new slide; drop any stale count
                self._last_slide_frame = None
            if curr.global_timer != self._last_slide_frame:
                self._slide_frames += 1
                self._last_slide_frame = curr.global_timer
        return events

    def _event(self, curr: GameSnapshot, dustless: bool,
               frames_late: int) -> Event:
        return Event(type="rollout", frame=curr.global_timer,
                     timestamp_utc=curr.wall_time_utc,
                     payload={"dustless": dustless, "frames_late": frames_late,
                              "level": curr.curr_level})
