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
  anchor was detected, AND prev_action: prev.mario_action (the action on
  the PREVIOUS poll tick). Together they let consumers distinguish a genuine
  door crossing from a Usamune L-reset that respawns Mario AT a door:
    - Real door crossing: prev_action is in DOOR_ACTIONS (inputs locked
      during the door open animation — the prior tick was already inside
      the animation, e.g. ACT_PULLING_DOOR 0x1320 or ACT_PUSHING_DOOR
      0x1321 or ACT_WARP_DOOR_SPAWN 0x1322).
    - L-reset at door: prev_action is the gameplay action when the player
      pressed L (e.g. ACT_FREEFALL 0x04000440), not a door action.
  The segment engine keys the door-echo clause on prev_action when present,
  falling back to action for events journaled before this field existed.
  Historical events (no prev_action key): .get() returns None → not in
  DOOR_ACTIONS → old conservative close behaviour preserved.

NON-WARP door recency (live gate 2026-06-12, journal seq 26):
  ACT_PULLING_DOOR (0x1320) and ACT_PUSHING_DOOR (0x1321) end Usamune's
  section AFTER the door animation completes — the IGT reset is detected
  1-5 frames after the last door action, when Mario is already idle or
  landing.  At that point neither prev_action nor action is in DOOR_ACTIONS,
  so the action-based echo clause cannot classify it.  Star/key doors
  (ACT_ENTERING_STAR_DOOR 0x1331 etc.) share this late-reset pathology —
  they are DOOR_ACTIONS members precisely so the recency tracker sees them
  (BitS Entry regression, live journal event 3594, 2026-06-12).
  Solution: track the global_timer of the last tick a door action was observed
  (_last_door_frame).  Anchor payloads gain frames_since_door: how many game
  frames have elapsed since the most recent door action (None if never seen).
  The segment engine keys a fourth echo shape on 0 <= frames_since_door <= 30.
  Self-heal (domain rule 4): if global_timer jumps backward, _last_door_frame
  is cleared so a stale recency value cannot poison anchors after the jump.

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
from sm64_events.memory.addresses import DEATH_ACTIONS, DOOR_ACTIONS, PASSIVE_ACTIONS

BOOT_TIMER_MAX = 120   # global_timer below ~4 s after a backward jump = console reset; shared by lifecycle.py
NEAR_ZERO_IGT = 30     # 30 frames = 1 s at 30 fps; <= so exactly 1 s still counts
IGT_WRAP_CEILING = 65000  # u16 wrap guard: 65535->0 looks like a reset without this


class AnchorDetector:
    def __init__(self):
        self._acted = False
        self._acted_reported = False
        self._pause_streak = 0
        self._last_door_frame: int | None = None

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        # Self-heal on backward global_timer jump (domain rule 4): stale door
        # recency must not cross a savestate rewind boundary.
        if curr.global_timer < (self._last_door_frame or 0):
            self._last_door_frame = None
        # Track the most recent frame where a door action was observed so that
        # anchors emitted 1-5 frames after the door animation ends can still
        # be classified as echoes via frames_since_door (non-warp door shape).
        if curr.mario_action in DOOR_ACTIONS:
            self._last_door_frame = curr.global_timer
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
        # frames_since_door: how many game frames have elapsed since the most
        # recent tick where a door action was observed.  None if no door has
        # been seen this anchor period.  The segment engine uses this to
        # classify non-warp door echoes that land 1-5 frames after the door
        # action ends (live gate 2026-06-12, journal seq 26).
        frames_since_door = (
            (curr.global_timer - self._last_door_frame)
            if self._last_door_frame is not None else None)
        if curr.global_timer < prev.global_timer:
            if curr.global_timer < BOOT_TIMER_MAX:
                return []  # console reset — GameResetDetector owns this
            return [Event(type="state_loaded", frame=curr.global_timer,
                          timestamp_utc=curr.wall_time_utc,
                          payload={"igt_frames_restored": curr.igt_overall,
                                   "mario_acted": self._acted,
                                   "paused_frames_before": self._pause_streak,
                                   "acted_tracking": True,
                                   "action": curr.mario_action,
                                   "prev_action": prev.mario_action,
                                   "frames_since_door": frames_since_door})]
        if (curr.igt_overall < prev.igt_overall
                and curr.igt_overall <= NEAR_ZERO_IGT
                and prev.igt_overall < IGT_WRAP_CEILING):
            return [Event(type="practice_reset", frame=curr.global_timer,
                          timestamp_utc=curr.wall_time_utc,
                          payload={"igt_frames_before": prev.igt_overall,
                                   "mario_acted": self._acted,
                                   "paused_frames_before": self._pause_streak,
                                   "acted_tracking": True,
                                   "action": curr.mario_action,
                                   "prev_action": prev.mario_action,
                                   "frames_since_door": frames_since_door})]
        return []
