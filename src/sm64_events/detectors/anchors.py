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

Both anchor payloads carry mario_acted: whether Mario entered any non-passive
  action since the last anchor. The tracking layer discards reset-closures of
  attempts where the player never acted (no-op reset spam, per user feedback).
  The action transition ON the anchor tick itself is swallowed — it belongs
  to the warp/spawn, not to either attempt.

Both anchor payloads also carry action: curr.mario_action at the tick the
  anchor was detected. This is a journal FACT that lets consumers classify
  load echoes — the segment engine ignores anchors where action is a
  DOOR_ACTION (intra-area castle door), because door animations lock input
  and Usamune resets IGT on every door crossing, producing a synthetic
  anchor that is never a player reset.

Pause streak: consecutive game frames where global_timer advanced but the
  overall IGT did not — game logic stopped, i.e. the Usamune pause menu (or a
  dialog time-stop). Stamped on anchors as paused_frames_before; the tracking
  layer discards reset-closures after long pauses (AFK rule). Emulator pause
  freezes BOTH clocks, so it never grows the streak (documented limitation).
mario_acted event: emitted once per anchor period at Mario's first
  non-passive action, so the tracking layer can judge activity for closures
  that are NOT anchors (death/abandon/hard reset). Anchors additionally carry
  acted_tracking: true so old journals (no such events) keep legacy semantics.
  Death actions are involuntary and never count as activity (a same-tick
  mario_acted would defeat the unacted-death discard); involuntary knockback
  still counts — accepted limitation.

VERIFY (live gate): confirm with the human that a Usamune SECTION state
load moves global_timer backward (full-RAM restore). If Usamune implements
section states as warps instead, loads will classify as practice_reset —
acceptable for attempt tracking, but the payload distinction matters for
the anchor→outcome clock, so characterize it once on real hardware."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import DEATH_ACTIONS, PASSIVE_ACTIONS

BOOT_TIMER_MAX = 120   # global_timer below ~4 s after a backward jump = console reset; shared by lifecycle.py
NEAR_ZERO_IGT = 30     # 30 frames = 1 s at 30 fps; <= so exactly 1 s still counts
IGT_WRAP_CEILING = 65000  # u16 wrap guard: 65535->0 looks like a reset without this


class AnchorDetector:
    def __init__(self):
        self._acted = False
        self._acted_reported = False
        self._pause_streak = 0

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        events = self._classify(prev, curr)
        if events:
            # the action transition ON the anchor tick is swallowed — it
            # belongs to the warp/spawn, not to either attempt
            self._acted = False
            self._acted_reported = False
            self._pause_streak = 0
            return events
        self._update_pause_streak(prev, curr)
        if (curr.mario_action not in PASSIVE_ACTIONS
                and curr.mario_action not in DEATH_ACTIONS):
            self._acted = True
            if not self._acted_reported:
                self._acted_reported = True
                return [Event(type="mario_acted", frame=curr.global_timer,
                              timestamp_utc=curr.wall_time_utc, payload={})]
        return []

    def _update_pause_streak(self, prev: GameSnapshot, curr: GameSnapshot) -> None:
        if curr.global_timer < prev.global_timer:
            # boot-range backward jump (no anchor fired): a console reset —
            # nothing from before the boot survives, activity included
            self._pause_streak = 0
            self._acted = False
            self._acted_reported = False
        elif curr.igt_overall != prev.igt_overall:
            self._pause_streak = 0   # game logic is running
        elif curr.global_timer > prev.global_timer:
            self._pause_streak += curr.global_timer - prev.global_timer
        # equal global_timer: polled faster than one frame — no information

    def _classify(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.global_timer < prev.global_timer:
            if curr.global_timer < BOOT_TIMER_MAX:
                return []  # console reset — GameResetDetector owns this
            return [Event(type="state_loaded", frame=curr.global_timer,
                          timestamp_utc=curr.wall_time_utc,
                          payload={"igt_frames_restored": curr.igt_overall,
                                   "mario_acted": self._acted,
                                   "paused_frames_before": self._pause_streak,
                                   "acted_tracking": True,
                                   "action": curr.mario_action})]
        if (curr.igt_overall < prev.igt_overall
                and curr.igt_overall <= NEAR_ZERO_IGT
                and prev.igt_overall < IGT_WRAP_CEILING):
            return [Event(type="practice_reset", frame=curr.global_timer,
                          timestamp_utc=curr.wall_time_utc,
                          payload={"igt_frames_before": prev.igt_overall,
                                   "mario_acted": self._acted,
                                   "paused_frames_before": self._pause_streak,
                                   "acted_tracking": True,
                                   "action": curr.mario_action})]
        return []
