# src/sm64_events/detectors/anchors.py
"""Attempt anchors: classify timer discontinuities into retry events.

practice_reset — Usamune level reset / level re-entry: the overall IGT
  drops to near zero while gGlobalTimer keeps running. Payload carries the
  IGT the moment before the drop: that is the failed attempt's duration.
  Drops from near the u16 ceiling are treated as wraparound, not resets —
  the cost is one missed anchor per ~36 min of continuous IGT, which merely
  merges two attempts.
state_loaded — savestate / Usamune section-state load: gGlobalTimer jumps
  backward to a mid-game value (a full-RAM restore rewinds it). Backward
  jumps into the boot range are console resets and belong to game_reset
  (lifecycle.py shares BOOT_TIMER_MAX so exactly one of the two fires).

VERIFY (live gate): confirm with the human that a Usamune SECTION state
load moves global_timer backward (full-RAM restore). If Usamune implements
section states as warps instead, loads will classify as practice_reset —
acceptable for attempt tracking, but the payload distinction matters for
the anchor→outcome clock, so characterize it once on real hardware."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot

BOOT_TIMER_MAX = 120   # global_timer below ~4 s after a backward jump = console reset; shared by lifecycle.py
NEAR_ZERO_IGT = 30     # 30 frames = 1 s at 30 fps; <= so exactly 1 s still counts
IGT_WRAP_CEILING = 65000  # u16 wrap guard: 65535->0 looks like a reset without this


class AnchorDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.global_timer < prev.global_timer:
            if curr.global_timer < BOOT_TIMER_MAX:
                return []  # console reset — GameResetDetector owns this
            return [Event(type="state_loaded", frame=curr.global_timer,
                          timestamp_utc=curr.wall_time_utc,
                          payload={"igt_frames_restored": curr.igt_overall})]
        if (curr.igt_overall < prev.igt_overall
                and curr.igt_overall <= NEAR_ZERO_IGT
                and prev.igt_overall < IGT_WRAP_CEILING):
            return [Event(type="practice_reset", frame=curr.global_timer,
                          timestamp_utc=curr.wall_time_utc,
                          payload={"igt_frames_before": prev.igt_overall})]
        return []
