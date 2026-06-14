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

save_pending (live report 2026-06-12): exiting a course WITH a star pops the
  post-star "SAVE & CONTINUE?" course-complete screen; Mario holds
  ACT_EXIT_LAND_SAVE_DIALOG (addresses.SAVE_DIALOG_ACTIONS) for the whole menu
  and confirming an option reloads the area, resetting Usamune's IGT a few
  frames later — an INVOLUNTARY reset, NOT a player retry. A boolean latch is
  set whenever that action is seen; the practice_reset/state_loaded that
  follows carries save_pending=True so the segment engine treats it as an echo
  (segments.py shape 4) and a segment runs THROUGH the save. A latch (not
  frames_since_door-style recency) because the menu-close→reload-reset gap
  varies; the next anchor always consumes the latch (the reload reset is the
  first anchor after the menu), so it cannot linger onto a later real reset.
  Cleared on every anchor and on the boot-range backward jump (self-heal).

frames_since_dialog (live journal 2026-06-14, Lakitu Skip): how many game
  frames have elapsed since Mario was last in a textbox/cutscene action
  (addresses.DIALOG_ACTIONS, plus ACT_INTRO_CUTSCENE — the fresh-file run-start
  dialogue). A textbox engages a TIME-STOP that re-initialises Usamune's overall
  IGT; on the intro the cutscene ends, control is regained (spawned kind="intro")
  and Usamune zeroes the overall counter ONE frame later, so the detector reads
  that drop as a practice_reset. It lands a frame AFTER the spawn (not co-frame
  with any transition or arm) and carries no door/save context, so it slips past
  every other echo shape and wrongly closes the just-armed segment. The segment
  engine keys a fifth echo shape on 0 <= frames_since_dialog <= window so a run
  never splits/resets on a textbox in ANY level (user rule 2026-06-14). None when
  no dialogue has been seen this anchor period. Self-heal (domain rule 4): a
  backward global_timer jump clears _last_dialog_frame so a stale recency value
  cannot poison anchors after a rewind. Mirrors frames_since_door exactly.

Pause streak: consecutive game frames where global_timer advanced but the
  overall IGT did not — game logic stopped, i.e. the Usamune pause menu (or a
  dialog time-stop). Stamped on anchors as paused_frames_before; the tracking
  layer discards reset-closures after long pauses (AFK rule). Emulator pause
  freezes BOTH clocks, so it never grows the streak (documented limitation).

Pause-warp anchor (live feedback 2026-06-12): a menu warp executed straight
  from the pause menu without ever unpausing leaves igt_overall near zero on
  BOTH sides of the warp — no drop edge exists, the IGT classifier is blind,
  and the segment engine never gets its anchor (no swap re-validation, no
  attempt_anchor arming; the user had to warp twice).  Discriminator:
  (curr_level, curr_area) changed while the pause streak was running
  (> PAUSE_WARP_MIN_STREAK) with the section timer <= NEAR_ZERO_IGT on both
  sides.  Walked transitions cannot match — walking runs gameplay frames, so
  IGT grows past the near-zero zone and the streak resets; door-cutscene
  time-stops grow the streak but the player walked there (IGT large).  The
  anchor is captured at the warp tick (frame, streak, actions) but emitted
  one POSITION-STABLE tick later: cross-level warps update the area byte a
  poll tick after the level byte (live journal 3572/3573), and the area
  detector — earlier in main.py's order — must journal the settled position
  before the engine sees the anchor.  A rewind or any classified anchor in
  between supersedes the pending event (never two anchors for one load).

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
from sm64_events.memory.addresses import (ACT_INTRO_CUTSCENE, DEATH_ACTIONS,
                                           DIALOG_ACTIONS, DOOR_ACTIONS,
                                           PASSIVE_ACTIONS, SAVE_DIALOG_ACTIONS)

BOOT_TIMER_MAX = 120   # global_timer below ~4 s after a backward jump = console reset; shared by lifecycle.py
NEAR_ZERO_IGT = 30     # 30 frames = 1 s at 30 fps; <= so exactly 1 s still counts
IGT_WRAP_CEILING = 65000  # u16 wrap guard: 65535->0 looks like a reset without this
PAUSE_WARP_MIN_STREAK = 5  # walked load echoes pause 0-3 frames, menu warps 13+
# (live logs 2026-06-12; segments._MENU_PAUSE_FRAMES mirrors the same evidence)


class AnchorDetector:
    def __init__(self):
        self._acted = False
        self._acted_reported = False
        self._pause_streak = 0
        self._last_door_frame: int | None = None
        self._last_dialog_frame: int | None = None  # last tick Mario was in a textbox/intro-cutscene action
        self._pending_warp: Event | None = None  # pause-warp anchor awaiting position-stable tick
        self._save_menu_seen = False  # save-prompt screen observed this anchor period

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        # Self-heal on backward global_timer jump (domain rule 4): stale door
        # recency must not cross a savestate rewind boundary — and neither may
        # a pending pause-warp anchor (its warp context predates the jump).
        if curr.global_timer < prev.global_timer:
            self._pending_warp = None
        if curr.global_timer < (self._last_door_frame or 0):
            self._last_door_frame = None
        if curr.global_timer < (self._last_dialog_frame or 0):
            self._last_dialog_frame = None
        # Track the most recent frame where a door action was observed so that
        # anchors emitted 1-5 frames after the door animation ends can still
        # be classified as echoes via frames_since_door (non-warp door shape).
        if curr.mario_action in DOOR_ACTIONS:
            self._last_door_frame = curr.global_timer
        # Track the most recent textbox/intro-cutscene frame so an IGT re-init a
        # frame or two later (Usamune zeroes the overall counter when control is
        # regained — live journal 2026-06-14, Lakitu Skip) is recognised as a
        # dialogue echo, never a player reset. ACT_INTRO_CUTSCENE is included:
        # it is the fresh-file run-start dialogue, and the reset lands the frame
        # AFTER it ends (Mario already back to gameplay), so only this recency
        # field — not the current action — can classify it.
        if (curr.mario_action in DIALOG_ACTIONS
                or curr.mario_action == ACT_INTRO_CUTSCENE):
            self._last_dialog_frame = curr.global_timer
        # Post-star "SAVE & CONTINUE?" screen: Mario HOLDS ACT_EXIT_LAND_SAVE_DIALOG
        # for the whole menu (live watch 2026-06-12). Confirming an option reloads
        # the area and resets Usamune's IGT a few frames later (Mario already back
        # to idle) — an involuntary reset, not a player retry. Latch it so the
        # practice_reset that follows carries save_pending and the segment engine
        # treats it as an echo (segments.py shape 4). Latch (not recency) because
        # the close-to-reset gap varies; the next anchor always consumes it (the
        # reload reset), so it cannot linger onto a later real reset.
        if curr.mario_action in SAVE_DIALOG_ACTIONS:
            self._save_menu_seen = True
        events = self._classify(prev, curr)
        if events:
            self._pending_warp = None  # one load, one anchor: classified wins
        elif self._pending_warp is not None:
            # Pause-warp pending: emit once the position is STABLE, so the
            # area detector (earlier in main.py order) has journaled the
            # settled post-warp position before the engine sees this anchor.
            if (curr.curr_level, curr.curr_area) \
                    == (prev.curr_level, prev.curr_area):
                events = [self._pending_warp]
                self._pending_warp = None
        elif (self._pause_streak > PAUSE_WARP_MIN_STREAK
              and (curr.curr_level, curr.curr_area)
              != (prev.curr_level, prev.curr_area)
              and curr.igt_overall <= NEAR_ZERO_IGT
              and prev.igt_overall <= NEAR_ZERO_IGT):
            # Pause-warp (module docstring): menu warp with the section timer
            # already near zero — no IGT edge will ever fire for this load.
            # Capture the anchor at the warp tick; the anchor period ends now.
            self._pending_warp = Event(
                type="practice_reset", frame=curr.global_timer,
                timestamp_utc=curr.wall_time_utc,
                payload={"igt_frames_before": prev.igt_overall,
                         "mario_acted": self._acted,
                         "paused_frames_before": self._pause_streak,
                         "acted_tracking": True,
                         "action": curr.mario_action,
                         "prev_action": prev.mario_action,
                         "save_pending": self._save_menu_seen,
                         "frames_since_door":
                             (curr.global_timer - self._last_door_frame)
                             if self._last_door_frame is not None else None,
                         "frames_since_dialog":
                             (curr.global_timer - self._last_dialog_frame)
                             if self._last_dialog_frame is not None else None})
            self._acted = False
            self._acted_reported = False
            self._pause_streak = 0
            self._save_menu_seen = False
            return []
        if events:
            # the action transition ON the anchor tick is swallowed — it
            # belongs to the warp/spawn, not to either attempt
            self._acted = False
            self._acted_reported = False
            self._pause_streak = 0
            self._save_menu_seen = False  # consumed by this anchor (the reload reset)
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
            self._save_menu_seen = False
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
        # Recency since the last textbox/intro-cutscene frame: an IGT re-init
        # within a blink of one is a dialogue echo (segments.py shape 5).
        frames_since_dialog = (
            (curr.global_timer - self._last_dialog_frame)
            if self._last_dialog_frame is not None else None)
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
                                   "save_pending": self._save_menu_seen,
                                   "frames_since_door": frames_since_door,
                                   "frames_since_dialog": frames_since_dialog})]
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
                                   "save_pending": self._save_menu_seen,
                                   "frames_since_door": frames_since_door,
                                   "frames_since_dialog": frames_since_dialog})]
        return []
