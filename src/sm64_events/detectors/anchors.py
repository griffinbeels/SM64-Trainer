# src/sm64_events/detectors/anchors.py
"""Attempt anchors: classify timer discontinuities into retry events.

practice_reset — Usamune level reset / level re-entry: the overall IGT
  drops to near zero while gGlobalTimer keeps running. Payload carries the
  IGT the moment before the drop — ONE LEG of the attempt, not necessarily
  the whole of it: the counter also restarts at a subarea load and at an
  in-level teleporter, neither of which is a retry, so an attempt can span
  several legs. `tracking/projection.py::_with_carried_igt` sums them (live
  report 2026-08-03: three CCM bridge warps and a reset read 0'08"93 for a
  run that took 0'25"06). This docstring claimed the field WAS the attempt's
  duration until then, and that was true only while every restart was a retry.
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
  The action the anchor INTERRUPTED is swallowed — it belongs to the warp/spawn,
  not to either attempt.

  **Swallowing the anchor's POLL is not the same as swallowing the action, and
  the difference is what task 0084 was** (live report: "warping to the beginning
  of a course inside a subarea results in an extra reset… this is a really
  common problem within subareas"). Two things stretch the interrupted action
  past the anchor's own poll: a 60 Hz poll reads the SAME game frame twice, and
  the reload a reset triggers does not reach Mario's action byte for another
  frame or two. Either way the next poll re-read the very action the anchor had
  just swallowed and marked the FRESH attempt as acted — so the menu warp that
  followed banked a second reset row for an attempt nobody made.

  Measured over both journals, the gap from an anchor to the first mario_acted
  after it: **2,130 at zero frames, 484 at one, 35 across the whole 2-6 band,
  2,370 at 7+.** A lingering byte and real play, with nothing in between. Zero
  frames is not a heuristic at all — global_timer IS the game's frame counter,
  so a second poll of the same value cannot have seen a new action.

  `_anchor_action` is therefore the ACTION INSTANCE, not a frame window: an
  anchor latches the action Mario is holding, and the first poll that reads
  anything else clears the latch for good. Walking again after the reload is
  walking again; the reload's own spawn is what clears it. Every place that
  starts an anchor period latches, including `_update_pause_streak`'s console
  reset, because "nothing from before the boot survives" says the same thing
  about the action byte as it does about the activity flag.

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

area_load (2026-08-01, live report: "if we enter a subarea within a stage, we
  fundamentally DID NOT RESET… showing a reset there is an error"): Usamune
  zeroes the overall IGT on an in-course AREA load exactly as it does on an
  L-reset, so walking into the SSL pyramid fired an anchor and the projector
  recorded a 25-second reset the player never made. This flag says the zero
  came from a load, and `tracking/projection.py` records no attempt for it.

  **The discriminator is the DESTINATION area, and it took three passes of
  measurement to find, because the obvious readings are all wrong.** Against
  his whole journal, 477 anchors coincide with an in-course area edge:
    - a warp ACTION on the anchor covers 6% of them, against 21% of the plain
      resets — insensitive AND pointing the wrong way. Same for a nearby
      `warp_entered` (6% vs 4%) and for door recency (0%).
    - 424 of the 477 move BACK to area 1, and reading those as "L-resets taken
      inside a subarea" is what made this look unfixable. They are not. 330
      follow a level entry or another anchor within 150 frames — they are the
      TAIL of a load, the area byte settling (entering SSL walks it 3 → 2 → 1
      across 47 frames, firing an anchor at each step). The rest are the
      reset's own reload: an L-reset in WF walks the byte 1 → 2 → 1 over 44
      frames and Usamune's counter zeroes on the LAST edge, which is why a
      real reset so often looks like an area change.
    - the edges that go DEEPER (destination != area 1) are the real subarea
      entries, and there are 11 of them in the played-then-transitioned group.
  A course always starts in area 1 (the castle hubs, where 1/2/3 are lobby/
  upstairs/basement, are excluded here for that reason), so "the counter
  zeroed on an edge into an area that is not 1" is the entry and everything
  else is a reset or its own echo. Recency, not co-frame equality: a 60 Hz
  poll of a 30 fps game reads the area byte and the counter on different
  polls of the SAME game frame, so the edge and the zero need a window
  (AREA_LOAD_WINDOW) rather than an exact match.

teleport (2026-08-03, live demo in CCM and WDW; task 0082): an IN-LEVEL
  teleporter — the CCM broken bridge, the WDW corner warps, the HMC toxic-maze
  pads — relocates Mario inside the SAME area, and Usamune zeroes the overall
  counter for it exactly as it does for an L-reset. His words: *"we need to not
  actually detect this as a reset when playing the level, because otherwise we
  can't complete these stars in the practice tool."*

  `area_load` above cannot see it: no area edge fires at all, so there is
  nothing to pair the zero with. **The discriminator is how recently
  ACT_TELEPORT_FADE_OUT ran.** Measured on his three demonstrated warps
  (journal ids 23199/23200, 23218/23219, 23231/23232, 2026-08-03): the counter
  zeroes on the very frame Mario crosses from the fade-out to the fade-in, 42
  frames after touching the pad — `act_teleport_fade_out` calls
  `level_trigger_warp` at actionTimer 20 and the delayed warp takes another 20.
  Recency is therefore 1 frame, and TELEPORT_WINDOW is the same poll-skew
  allowance AREA_LOAD_WINDOW gets, for the same reason.

  **Recency, NOT the current action, and the difference is measured**: Mario's
  action byte still reads ACT_TELEPORT_FADE_IN long after the warp is over —
  the two other anchors in that same session carried it 125 and 172 frames
  later, across a Usamune menu warp into WDW — so testing `action` alone would
  have swallowed a real attempt boundary. A level edge CLEARS the pairing for
  the same reason it clears `_last_area_edge`: the action is shared with
  cap-course warps that really do leave the level, and leaving a level is a
  boundary whoever caused it.

  The anchor is still EMITTED, flagged, exactly as `area_load` is — suppressing
  it outright would be simpler and wrong, because `tracking/segments.py::
  _zeroes_usamune_igt` reads every anchor to know when Usamune's counter last
  restarted, and a segment running through the warp would then bank a time
  measured from the warp instead of from its arm. `projection.py` records no
  attempt for it; `segments.py`'s echo shape (6) keeps it invisible to the
  matcher while still moving that basis frame.

warp_op / frames_since_warp_op (2026-08-01, INERT — read by nothing): the
  game's own pending warp op (`sDelayedWarpOp`) most recently seen non-zero,
  and how long ago. Kept as evidence for the case `area_load` above does NOT
  cover: walking back OUT of a subarea zeroes the counter the same way, and
  is indistinguishable from a reset by destination alone.

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
  that are NOT anchors (death/abandon/hard reset). Carries the ACTION, inert,
  since 2026-08-04 — see the emit site for what not carrying it cost.
  Anchors additionally carry
  acted_tracking: true so old journals (no such events) keep legacy semantics.
  Death actions are involuntary and never count as activity (a same-tick
  mario_acted would defeat the unacted-death discard); involuntary knockback
  still counts — accepted limitation. A LEVEL-EXIT cutscene never counts
  either (addresses.LEVEL_EXIT_ACTIONS, 2026-08-03): Mario is flung out of the
  level with no control, and the byte then LINGERS — 62 anchors across both
  journals land on one of those actions and 62 of 62 read as having acted,
  which is how a menu warp 92 seconds after a death banked a phantom 1.5 s
  reset row on the arrival ("The first time we enter a map should never be
  considered a reset, because there's nothing to reset").

VERIFY (live gate): confirm with the human that a Usamune SECTION state
load moves global_timer backward (full-RAM restore). If Usamune implements
section states as warps instead, loads will classify as practice_reset —
acceptable for attempt tracking, but the payload distinction matters for
the anchor→outcome clock, so characterize it once on real hardware."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors import counter_epoch
from sm64_events.detectors.counter_epoch import EpochTracker
from sm64_events.memory.addresses import (ACT_INTRO_CUTSCENE,
                                           ACT_TELEPORT_FADE_OUT,
                                           CASTLE_LEVELS, DEATH_ACTIONS,
                                           DIALOG_ACTIONS, DOOR_ACTIONS,
                                           LEVEL_EXIT_ACTIONS,
                                           PASSIVE_ACTIONS,
                                           SAVE_DIALOG_ACTIONS)

BOOT_TIMER_MAX = 120   # global_timer below ~4 s after a backward jump = console reset; shared by lifecycle.py
NEAR_ZERO_IGT = 30     # 30 frames = 1 s at 30 fps; <= so exactly 1 s still counts
IGT_WRAP_CEILING = 65000  # u16 wrap guard: 65535->0 looks like a reset without this
PAUSE_WARP_MIN_STREAK = 5  # walked load echoes pause 0-3 frames, menu warps 13+
# The pairing windows and the destination-area rule live in
# `detectors/counter_epoch.py` since 2026-08-04 — igt_clock.py needs the SAME
# answer to know whether the counter it is reading measures a whole star or one
# leg of one, and two modules answering that privately is what made a star's
# row wait 1.5 s (task 0083). Re-exported here because this module's docstring
# is where the evidence for them lives.
AREA_LOAD_WINDOW = counter_epoch.AREA_LOAD_WINDOW
TELEPORT_WINDOW = counter_epoch.TELEPORT_WINDOW
COURSE_START_AREA = counter_epoch.COURSE_START_AREA
# (live logs 2026-06-12; segments._MENU_PAUSE_FRAMES mirrors the same evidence)


class AnchorDetector:
    def __init__(self):
        self._acted = False
        self._acted_reported = False
        # The action the last anchor INTERRUPTED, held back until Mario leaves
        # it — see the docstring's measurement. None once he has.
        self._anchor_action: int | None = None
        self._pause_streak = 0
        self._last_door_frame: int | None = None
        self._last_dialog_frame: int | None = None  # last tick Mario was in a textbox/intro-cutscene action
        self._pending_warp: Event | None = None  # pause-warp anchor awaiting position-stable tick
        self._save_menu_seen = False  # save-prompt screen observed this anchor period
        self._last_warp_op: tuple[int, int] | None = None  # (frame, op), inert — see docstring
        # WHY the counter last restarted — shared with igt_clock.py, which
        # needs the same answer for the star's time (counter_epoch.py).
        self._epoch = EpochTracker()

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
        if self._last_warp_op and curr.global_timer < self._last_warp_op[0]:
            self._last_warp_op = None   # same self-heal as the two above
        # …and the epoch tracker heals its own two recencies in observe().
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
        # The game's own reason for a pending transition, kept the way door and
        # dialogue recency are kept. INERT: journaled for the open question in
        # the docstring (an area load is indistinguishable from an L-reset),
        # read by nothing.
        if curr.pending_warp_op:
            self._last_warp_op = (curr.global_timer, curr.pending_warp_op)
        # Where the area last moved TO within this level, and when the
        # teleporter's fade-OUT last ran — the two recencies that say what a
        # counter restart MEANT. Both live in counter_epoch.EpochTracker now,
        # because igt_clock.py asks the identical question about the identical
        # frame and a second private answer is what cost a star's row 1.5 s.
        self._epoch.observe(prev, curr)
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
            self._anchor_action = curr.mario_action
            self._pause_streak = 0
            self._save_menu_seen = False
            return []
        if events:
            # the action the anchor INTERRUPTED is swallowed — it belongs to
            # the warp/spawn, not to either attempt. Held by IDENTITY rather
            # than for this poll: the byte outlives the poll and often the
            # frame, which is the whole of task 0084 (docstring).
            self._acted = False
            self._acted_reported = False
            self._anchor_action = curr.mario_action
            self._pause_streak = 0
            self._save_menu_seen = False  # consumed by this anchor (the reload reset)
            return events
        self._update_pause_streak(prev, curr)
        if self._anchor_action is not None \
                and curr.mario_action != self._anchor_action:
            self._anchor_action = None  # Mario left it: he owns what he does now
        if (self._anchor_action is None
                and curr.mario_action not in PASSIVE_ACTIONS
                and curr.mario_action not in DEATH_ACTIONS
                and curr.mario_action not in LEVEL_EXIT_ACTIONS):
            self._acted = True
            if not self._acted_reported:
                self._acted_reported = True
                # `action` is INERT and exists to make the NEXT round of this
                # measurable: the journal records when Mario first acted and
                # never WHAT he was doing, so an action id repeated after the
                # reload's spawn is indistinguishable from the anchor's own
                # lingering byte AFTER THE FACT. Task 0084 could measure its
                # own population exactly (a poll of the same global_timer
                # cannot have seen a new action) and could NOT measure what
                # the old detector would have done a hundred frames later.
                return [Event(type="mario_acted", frame=curr.global_timer,
                              timestamp_utc=curr.wall_time_utc,
                              payload={"action": curr.mario_action})]
        return []

    def _update_pause_streak(self, prev: GameSnapshot, curr: GameSnapshot) -> None:
        if curr.global_timer < prev.global_timer:
            # boot-range backward jump (no anchor fired): a console reset —
            # nothing from before the boot survives, activity included
            self._pause_streak = 0
            self._acted = False
            self._acted_reported = False
            self._anchor_action = curr.mario_action
            self._save_menu_seen = False
        elif curr.igt_overall != prev.igt_overall:
            self._pause_streak = 0   # game logic is running
        elif curr.global_timer > prev.global_timer:
            self._pause_streak += curr.global_timer - prev.global_timer
        # equal global_timer: polled faster than one frame — no information

    def _is_area_load(self, curr: GameSnapshot) -> bool:
        """Did Usamune's counter zero because Mario went DEEPER into the level
        — into the pyramid, the volcano — rather than because he retried?

        The full derivation, the three readings that do not work, and why the
        destination area is the one that does, are in the module docstring; the
        rule itself is `counter_epoch.EpochTracker`, shared with igt_clock.py."""
        return self._epoch.is_area_load(curr)

    def _is_teleport(self, curr: GameSnapshot) -> bool:
        """Did Usamune's counter zero because Mario took an IN-LEVEL
        teleporter — the CCM broken bridge, a WDW corner — rather than because
        he retried? The measurement and the two readings it rules out are in
        the module docstring."""
        return self._epoch.is_teleport(curr)

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
        # Inert observation, both anchor kinds (docstring): the last pending
        # warp op and its age. None/None when the game has queued no warp.
        warp_op = self._last_warp_op[1] if self._last_warp_op else None
        frames_since_warp_op = (
            (curr.global_timer - self._last_warp_op[0])
            if self._last_warp_op else None)
        observed = {"warp_op": warp_op,
                    "frames_since_warp_op": frames_since_warp_op,
                    "area": curr.curr_area, "prev_area": prev.curr_area,
                    "area_load": self._is_area_load(curr),
                    "teleport": self._is_teleport(curr)}
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
                                   "frames_since_dialog": frames_since_dialog,
                                   **observed})]
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
                                   "frames_since_dialog": frames_since_dialog,
                                   **observed})]
        return []
