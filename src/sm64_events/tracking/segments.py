"""Segment trigger vocabulary + matcher engine (spec 2026-06-11).

ONE registry: TRIGGERS/GUARDS drive (a) definition validation at the API
boundary, (b) the matcher, (c) GET /api/segments/vocab that renders the
builder GUI (ui/components/segments.js builds its own editor sentence
straight from the raw label/template vocab() ships — a second, independent
renderer, not a gap this module needs to fill), and (d)
card_waiting_for_sentence's read-only "waiting for" line on an armed practice
card (spec 2026-07-28-multi-step-segments, Task 6). Adding a trigger type =
one TriggerType row here (label + card_label + params + the sentence
template the builder and card_waiting_for_sentence both render from). `label`
is editor voice ("You enter level {to}") for the builder's own read-only
preview; `card_label` is the SAME clause read as an imperative step
("Enter {to}") for the practice card, which supplies its own "Waiting for"
frame around it — the builder needs a sentence with a hole in it, the card
needs a phrase with a value in it, and past experience says collapsing them
into one string reads wrong in one of the two places every time (see
card_waiting_for_sentence's docstring). This module used to also render an
editor-voice sentence of its own (`waiting_for_sentence`), until Task 7
(2026-07-28) found it had no caller left in `src/` — the builder GUI never
called it (see (c) above) and `card_waiting_for_sentence` had fully
superseded it for the practice card — and deleted it (YAGNI: a neutral
clause-text renderer gets re-added WITH a caller if one ever needs it, not
kept alive by its own tests).

Matcher invariants (spec §Matcher semantics — tests are the contract):
- closures (success/failure) process BEFORE arming; one event may close an
  attempt AND re-arm the next (practice_reset in an attempt_anchor segment)
- COROLLARY — a def whose START and END can be satisfied by the SAME event is
  UNFIREABLE.  Closures run only for an ALREADY-ARMED def, so such a def arms
  on the very event that should close it and then hangs armed until something
  unrelated disarms it; it can never record an attempt.  The trap is a
  `level_exit from=A` / `level_enter to=B` pair where the world has a DIRECT
  A->B edge (one level_changed satisfies both) — e.g. DDD -> BitFS through the
  sub (23 -> 19), which shipped broken and surfaced as a segment stuck
  "running" in an unrelated course (live report 2026-07-24).  Start such a
  segment on an EARLIER event instead (the star that opens the way, an area
  crossing).  Guarded for the seeded corpus by
  tests/test_defaults_corpus.py::test_no_movement_starts_and_ends_on_the_SAME_event
- ARM POSITION must be RUNNABLE (live report 2026-07-27): a matched start
  trigger arms only if the segment could still be run from where the event
  actually left Mario standing (can_run_from — its own section comment carries
  the three rules and why they read the definition rather than the world
  topology).  A start trigger describes what happened, not where it ended: the
  seeded `level_exit from=X` clauses omit `to` because every real course exit
  lands in the castle, so a Usamune menu warp fabricating WF -> CCM (ONE
  level_changed 24 -> 5) armed WF -> SSL inside Cool, Cool Mountain and left it
  showing as ACTIVE SEGMENT ... Running — nothing below disarms a def whose
  player then stays put.  game_reset is exempt (ctx.level is the PRE-reset
  level until the next level_changed).
- TOPOLOGICAL VALIDITY (spec 2026-08-01, live report: WF -> SSL reading ACTIVE
  SEGMENT inside the Bowser 1 arena, and LLL -> HMC reading ACTIVE SEGMENT
  inside LLL).  Every settled position change is judged ONE FRAME LATE against
  the world graph in memory/addresses.py, by SegmentEngine._flush_move:
    (1) a move that is not an EDGE cancels every armed def (the warp menu or a
        savestate fabricated it), and
    (2) a legal move that strictly INCREASES the hop count to a def's next
        required place cancels that def.
  Both are SILENT (no row -- a movement that never happened must not bank a
  failure) and both exempt an arm that began AT OR AFTER the move, so warping
  somewhere to practise still arms what lives there.  A step naming no place
  (step_node -> None) is unconstrained, which is what keeps fights, pipe
  entries and star endings out of rule (2) without a list of special cases; so
  is a node the def itself names as a step (declared_nodes), which is how a
  route that deliberately re-enters a place says so.
  The ONE-FRAME DEFER IS REQUIRED, not cautious: every castle entry loads the
  lobby for a poll before settling, so judged raw a basement course exit reads
  as the non-edge "SSL -> Lobby".
  A topological cancel is the ONLY disarm in this engine the player can undo --
  see SegmentEngine._cancelled: a real anchor at the position the arm stood in
  brings it back (redoing a `level_exit from=30` start would mean redoing a
  whole Bowser fight), a real anchor ELSEWHERE forfeits it for good, and it
  expires on the same staleness budget a loose arm gets.
  This deliberately REVERSES can_run_from's refusal to consult that table; the
  circularity that refusal named is answered by
  tools/measure_topology_cancels.py, which scores the rules against the
  JOURNAL -- 82 of 82 successes survive on one, 110 of 112 on the other, and
  both losses were read back to the raw events and ARE the live report itself,
  recorded as a time.
- anchor closures are POSITION-GATED for _feed_strict/_feed_waypoint (segment
  swap, live report 2026-06-12) — see the LOOSE bullet below for why
  _feed_loose does NOT inherit the "elsewhere" half of this gate. Each _Arm
  remembers the MatchContext (level, area) where it armed — the segment's
  start position; a co-frame establishing area_changed pins the area for
  level_changed arms (ctx.area is stale during the level event — the area
  detector establishes one event later on the same tick).
  - Anchor AT the arm position: attempt BOUNDARY, not a state change — a
    real practice_reset/state_loaded closes the current attempt AND re-arms
    the same segment at the anchor frame (practice-loop continuation —
    Usamune respawns at the level's last entrance, which is the segment's
    start position; live-gate amendment 2026-06-12). The segment never
    stops being armed; the UI chip stays lit.
  - Anchor SOMEWHERE ELSE (Usamune menu warp / savestate into another
    area): RELOCATION — the player is moving, not practicing, so a failed-
    attempt row would lie. No row; the segment disarms (its start
    conditions no longer hold) and defs anchored at the destination arm in
    the same event's arm phase — the armed set always reflects where Mario
    actually is. None on either side = unknown (legacy journals) →
    conservative match (the pre-area continuation behavior).
- CROSS-AREA relocation also fires on the area_changed ITSELF, not just on
  anchors (live report 2026-06-13: warping between the lobby and upstairs
  double-armed both segments). An area_changed to a DIFFERENT area than where a
  segment armed disarms it (no row) — Mario left its start position — even when
  the co-frame load-echo anchor that would relocate it is echo-suppressed; a
  SAME-area door fires no area_changed, so the intra-area echo keeps it armed.
  And a cross-area RELOCATION anchor (co-frame with a real area edge =
  _last_area_edge_frame) may ARM an IDLE destination segment even though its
  warp landing spawns in ACT_WARP_DOOR_SPAWN (door-echo-classified) — else
  warping to the lobby never re-arms an attempt_anchor segment. Scoped to idle
  defs so it never rebases an armed one.
- DESTINATION subarea is DEFERRED then resolved LIVE (live report 2026-06-13):
  a level_enter/level_exit with a to_subarea can't be confirmed on the edge —
  the castle loads the lobby (area 1) transiently, then warps Mario to the real
  area a poll later, same game frame (detectors/level.py). Such a start match
  goes to _pending (not _armed) keyed on the required area; each co-frame
  area_changed updates the entry and ARMS the instant .area == required, RETRACTS
  the instant a later co-frame moves away (the transient lobby before a
  basement/upstairs settle). So a Lobby destination arms on ENTRY (its only
  co-frame is the establishing 1->1), and a basement/upstairs destination arms on
  its real-edge settle — both prompt. The entry stays in _pending until the frame
  advances (then dropped), so a later co-frame can still retract. start_frame
  stays the entry frame. SOURCE subarea (from_subarea) needs no deferral — Mario
  was settled there, so the lambda checks from_area off the edge. to_subarea is
  honoured on START triggers only.
- guards re-evaluate on EVERY arm and re-arm
- re-firing a start trigger while armed re-arms (timer restarts, no row);
  a refire whose guards FAIL leaves the existing arm untouched (the old
  start_frame keeps running).  PLAYER ACTIONS ONLY: an echo anchor matching
  an attempt_anchor start trigger neither arms nor re-arms (see load-echo
  rule — echo invisibility).  For a plain (waypoint-free) def this refire-
  while-armed is otherwise SILENT (no notices — `fresh` is False, since the
  def never left `self._armed`) EXCEPT for a plain LOOSE def, which emits the
  ordinary segment_disarmed+segment_armed pair (live audit 2026-07-29): a
  loose def survives everything short of death/game_reset/staleness, so it
  is the one mode where the player can genuinely still be mid-attempt when
  its own start condition happens again, and the restart needs to be VISIBLE
  rather than a silent re-timing under them.
- level_changed matching neither start nor end disarms silently (no row);
  area_changed and session_started never record rows
- WAYPOINT-BEARING defs (SegmentDef.waypoints non-empty, spec
  2026-07-23-default-routes-foundation) replace this whole armed-branch chain
  with an ordered-sequence matcher (SegmentEngine._feed_waypoint — see its
  docstring for the full precedence): the def's own start-trigger refire is
  suppressed while armed (progress owns re-arming, not the generic re-arm
  path); a real anchor mid-sequence REWINDS progress to 0 and re-arms in
  place, recording a reset row exactly like this chain's own (round 2, live
  report 2026-07-30 — this used to record no row at all, the open VERIFY
  item _feed_waypoint's own docstring named until the user settled it); an
  off-sequence star/key grab or wrong-destination level crossing silently
  cancels (disarm, no row) instead of the plain silent level_changed disarm
  above
- LOOSE defs (SegmentDef.match_mode == "loose", spec
  2026-07-28-multi-step-segments) replace the armed branch AGAIN — regardless
  of waypoints — with SegmentEngine._feed_loose (see its own docstring for
  the full precedence): the player says only where a segment starts and
  where it ends, so a star grab, a key grab, an area change and an off-route
  level crossing all pass straight through, transparently, where the strict
  or waypoint chains above would cancel or disarm. A real anchor (practice
  reset/savestate load) NOT at the arm position is transparent too (fixed
  live report 2026-07-28) — the strict/waypoint relocation disarm does not
  apply here, because a loose route is free to cross through positions it
  didn't start at and often must; only an anchor AT the arm position still
  means something (a genuine retry: reset row, re-arm in place). That
  removes every cancel rule that used to bound an arm, so every loose _Arm
  instead carries a
  staleness deadline (_Arm.deadline_frame, set at every arm/re-arm site by
  the one helper _deadline_for) and the deadline is checked FIRST — ahead of
  even the end trigger and the death/game_reset rows — because an arm that
  has outlived its budget is presumed abandoned, and a success or failure
  recorded through it would be a claim about a run the player walked away
  from. The budget itself (budget_frames) is MIN_BUDGET_FRAMES, or
  BUDGET_FACTOR times the definition's best success so far if that is
  larger — a def with history gets a tighter window than a first-timer's.
- EXCLUSIVE defs (SegmentDef.match_mode == "exclusive", spec
  2026-07-28-multi-step-segments, third match_mode) share _feed_strict with
  plain STRICT defs — same method, one extra gated branch, not a new
  handler: a star or Bowser-key grab that isn't the def's own end trigger
  silently cancels it (no row), exactly as a waypoint-bearing def's
  major-action cancel already does. Everything else — relocation, echoes,
  a real anchor at/off the arm position, death, game_reset, an off-route
  level crossing — is identical to STRICT. This mode exists for a plain
  two-endpoint span with no natural intermediate waypoint (e.g. "enter a
  Bowser pipe without going for its 8-red-coin star"), where inventing a
  fake waypoint just to reach _feed_waypoint would be a lie in the corpus.
  A def that combines this mode WITH waypoints still routes to
  _feed_waypoint like any other non-loose waypoint-bearing def (dispatch
  precedence is unchanged) — _feed_waypoint already cancels on the same
  star/key-grab rule as part of its own design, so that combination needs
  no special case.
- failure rows only on practice_reset/state_loaded (reset), death,
  game_reset (hard_reset); AFK closures (paused >= 150 frames) discard, and
  so do no-op closures (acted_tracking true, mario_acted false — warp/reset
  spam where Mario never moved; mirrors the star-side discard)
- rta_frames = close.frame - start_frame; a would-be-negative value on a
  SUCCESS discards the attempt (end before arm is a genuine anomaly —
  self-heal, domain rule 4), but failure closures record the row with
  rta_frames=None (game_reset's boot-range frame makes this the ONLY way
  hard_reset rows exist).  EXCEPTION — a close event carrying Usamune's own
  IGT records THAT as the time instead of the wall-frame delta, so the
  segment matches Usamune's display exactly and stays pause-safe (live report
  2026-06-12, Bowser 3 read 0'46"23 vs Usamune 0'46"26).  The events that
  carry one are key_grabbed, star_collected and — since 2026-07-31 —
  warp_entered, each stamped from the shared clock (detectors/igt_clock.py;
  the grand star never fires star_collected, so key.py stamps it), plus
  death, whose payload carries the raw counter.
  WHY THE DELTA IS NOT THE IGT (live report 2026-07-31: BitDW "No Reds"
  displayed 0'35"90 where Usamune showed 0'35"96, the report that put the
  igt on warp_entered).  Two independent errors, neither a constant:
  (a) start_frame is the frame the ANCHOR DETECTOR OBSERVED Usamune's counter
  drop, which is the zero frame or one frame after it depending on which
  60 Hz poll caught the 30 Hz drop; (b) the delta counts paused frames and
  Usamune's counter does not.  Measured over 626 grab-closed, anchor-armed
  star attempts in the user's own journal — the one shape where the SAME
  attempt records both numbers — Usamune's display minus the delta was +1 on
  57%, +2 on 21%, -1 on 10%, 0 on 2%, with a long negative tail wherever the
  player paused.  So `rta` and `igt` never coincided; they agreed within a
  frame or two whenever nobody paused, which reads the same until it doesn't.
  WHEN THE PAYLOAD IGT IS THE SEGMENT'S TIME: only when Usamune's counter was
  zeroed on the very frame the segment armed AND has not been zeroed since —
  `SegmentEngine._last_igt_zero_frame == arm.start_frame`, checked in _close
  rather than assumed.  This used to be an assumption ("every grab-closed
  segment today arms at the level/area load where Usamune resets IGT... none
  exists; revisit if one is created"), and it was already false in one place:
  a def spanning a DOOR (seg:100c->exit:bbh) crosses an IGT reset the matcher
  deliberately ignores as an echo, and would have banked the since-the-door
  time as its own.  A def armed mid-level and closed on a grab is the other
  shape.  Both now fall back to the delta, which at least spans the right two
  moments.  igt_frames on the Attempt stays None — segments remain RTA-only
  to the UI/PB layer; only the rta VALUE changes.
- load-echo rule: Usamune resets IGT on every level/area load, so the
  anchor detector emits a synthetic practice_reset on the same global-timer
  frame as the triggering transition.  Echo classification uses ORDERED shapes
  evaluated top-to-bottom; the first match wins:
    (1) arm-frame echo: ev.frame == arm.start_frame -- suppressed
        UNCONDITIONALLY.  The level_changed that armed the segment and the
        anchor it triggers share the same tick; the player may have been
        paused for minutes before entering (large paused_frames_before normal).
        (live gate 2026-06-12, seq 40-45)
    (2) door-context echo: prev_action/action in DOOR_ACTIONS, or
        frames_since_door 0-30 -- suppressed UNCONDITIONALLY.  Positive
        evidence of a door animation; pause-buffering at a door then crossing
        stays an echo.  Subshapes:
        (2a) intra-area door echo: NO area_changed (same area on both sides),
             but Usamune IGT resets -> anchor fires in a DOOR_ACTIONS member
             (push/pull/warp-spawn 0x1320-0x1322 or star/key-door cutscene
             0x132E/0x132F/0x1331 — addresses.py is the registry; inputs
             locked, never a player reset).
             Keyed on prev_action first (door anim was running the prev tick);
             fallback to action for old events without prev_action.
             Race fix (2026-06-12): L-resets respawn in ACT_WARP_DOOR_SPAWN
             (0x1322); prev_action=gameplay (not a door action) -> closes.
        (2b) non-warp door recency echo: ACT_PULLING/PUSHING_DOOR end the
             Usamune section AFTER the animation -- IGT reset arrives 1-5
             frames later; neither action nor prev_action carries door context.
             frames_since_door bridges the gap.  Historical events (no key)
             fall through to conservative close.
             (live gate 2026-06-12, seq 26)
    (3) transition co-frame echo: ev.frame == _last_transition_frame AND
        paused_frames_before <= _MENU_PAUSE_FRAMES (5) -- suppressed.
        Walked load echoes carry paused_frames_before 0-3; this gate passes
        them through as echoes.  Menu warps (06-01-00, etc.) are also co-frame
        but pass through the pause menu: paused_frames_before 13-890 observed
        (live logs 2026-06-12) -> the pause gate FAILS -> falls through to the
        real-reset path -> closes the stale attempt and re-arms at the warp
        frame.  A deliberate menu action is never an involuntary load echo.
        (live-gate amendment 2026-06-12)
    (4) save-prompt echo: ev.payload["save_pending"] is True -- suppressed
        UNCONDITIONALLY.  Exiting a course WITH a star pops the post-star
        "SAVE & CONTINUE?" course-complete screen; confirming an option
        reloads and resets Usamune's IGT, firing a practice_reset frames
        later (idle Mario, no position change, paused_frames_before 0) that
        is neither co-frame, a door, nor AFK -- it slipped through (1)-(3)
        and wrongly closed the armed segment (MIPS Clip: HMC exit -> save
        prompt reset the segment, live report 2026-06-12).  The anchor
        detector sets save_pending when the save menu was observed this
        anchor period (anchors.py); such a reload is involuntary, so the
        user wants the segment to run through it ("INCLUDING the save
        prompt").  Historical events (no key): .get() -> False -> conservative
        close behaviour preserved.
    (5) dialogue/cutscene echo: 0 <= frames_since_dialog <= _DIALOG_ECHO_WINDOW
        -- suppressed.  A textbox/cutscene engages a TIME-STOP that
        re-initialises Usamune's overall IGT.  On a fresh-file Lakitu Skip the
        intro cutscene ends, control is regained (spawned kind="intro" arms the
        segment), and Usamune zeroes the overall counter ONE frame later -- the
        detector reads that drop as a practice_reset.  It lands a frame AFTER
        the spawn (so NOT co-frame with any transition/arm -> shapes 1/3 miss)
        and carries no door/save context (shapes 2/4 miss), so it slipped
        through and closed the just-armed Lakitu Skip with a bogus ~1-frame
        "reset" row (live journal 2026-06-14).  frames_since_dialog (anchors.py)
        is the recency discriminator -- mirrors frames_since_door (2b).  We
        never split timing on a textbox in any level/circumstance (user rule
        2026-06-14).  Historical events (no key): .get() -> None -> out of
        window -> conservative close behaviour preserved.
    (6) in-level teleporter echo: ev.payload["teleport"] is True -- suppressed
        unconditionally.  The CCM broken bridge, the WDW corner warps and the
        HMC toxic-maze pads relocate Mario inside the SAME area, so no
        area_changed fires for shape (3) to catch and no door/dialogue context
        exists for (2)/(5) -- yet Usamune zeroes its overall counter for the
        warp exactly as it does for an L-reset.  "these should not trigger
        resets, because they are a legitimate part of the level" (live demo in
        CCM and WDW, 2026-08-03).  detectors/anchors.py::_is_teleport is the
        discriminator (fade-out recency, NOT the current action -- the action
        byte reads FADE_IN long after the warp) and carries the measurement.
        Historical events (no key): .get() -> False -> conservative close
        behaviour preserved.
  Shapes (1)/(3) are detected by frame equality.  Shape (2) is detected by
  prev_action/action in DOOR_ACTIONS (falling back through the chain) or
  frames_since_door.  Shape (5) is detected by frames_since_dialog recency.
  Shape (6) is a detector-set boolean.
  Historical events (no prev_action / frames_since_door / frames_since_dialog):
  .get() returns None -> conservative close behaviour preserved.
  ECHO INVISIBILITY (live regression 2026-06-12): an echo anchor is
  involuntary -- it is INVISIBLE to the engine entirely: no closure, no
  continuation re-arm, no arm-phase arm/re-arm, for every def.  Without
  this, an echo matching an attempt_anchor start trigger rebased the _Arm
  in the arm phase (LBLJ's lobby-door section reset rebased
  start_frame/started_utc to the door, so replay + rta began at the door).
  Shapes (2a)/(2b)/(3)/(5) depend only on the event, so they are classified
  ONCE per event before the per-def loop (anchor_is_echo); shape (1)
  depends on the per-def arm and is checked per def in BOTH the closure
  and arm phases.  Real anchors still take the continuation re-arm in the
  closure phase; the arm-phase attempt_anchor replace stays idempotent
  for those.
  KNOWN EDGE (no code): a savestate load INTO A DIFFERENT AREA emits a
  corrective area_changed co-frame with state_loaded; that state_loaded will
  be classified as a co-frame echo if paused_frames_before <= 5.  The
  negative-rta self-heal covers the time-jump consequences.  Acceptable: door
  echoes are constant, this edge is rare.
"""
import re
from dataclasses import dataclass, field, replace
from typing import Callable

from sm64_events.memory.addresses import (AREA_LOBBY, BOWSER_STAGE_LEVELS,
                                          CASTLE_AREA_NAMES, CASTLE_LEVELS,
                                          CASTLE_REGION_LEVELS,
                                          CASTLE_REGION_NODES,
                                          CASTLE_SECRET_STAR_AREAS,
                                          COURSE_BY_LEVEL, COURSE_NAMES,
                                          course_for_level,
                                          DOOR_ACTIONS, LEVEL_CASTLE_INSIDE,
                                          LEVEL_NAMES, node_key, node_label,
                                          node_short_label,
                                          region_for_node, star_count,
                                          star_name, world_connections,
                                          world_regions)
from sm64_events.core.landmark import same_landmark
from sm64_events.detectors.moment import MOMENTS
# The verb splice ("Open a door" -> "Open") lives with the row labeller and
# is borrowed, not copied: a landmark-pinned clause must read exactly like
# the row it was picked from, and two copies of the rule is how they drift.
from sm64_events.tracking.eventlabel import _verb
from sm64_events.tracking import topology

# The moment vocabulary, read as a SET for validation. detectors/moment.py's
# MOMENTS is the registry; this file never names a kind of its own, so adding
# one stays a single row over there.
_MOMENT_KINDS = frozenset(m.kind for m in MOMENTS)
_MOMENT_LABELS = {m.kind: m.label for m in MOMENTS}

_ANCHOR_TYPES = ("practice_reset", "state_loaded")  # attempt-anchor events

_AFK_PAUSE_FRAMES = 150  # mirrors the star-side AFK discard (projection.py)

_MENU_PAUSE_FRAMES = 5  # walked load echoes carry paused_frames_before 0-3
# (live logs 2026-06-12); menu warps pass through the pause menu: 13-890
# observed. A co-frame anchor preceded by a pause is a deliberate menu
# action, never an involuntary load echo.

_DOOR_ECHO_WINDOW = 30  # frames; non-warp doors reset the section 1-5 frames
# after the door action ends (watch trace 2026-06-12); poll stalls add a few.
# No human completes a door AND L-resets within a second; misclassifying a
# borderline instant reset (eaten, segment stays armed) is cheaper than
# constant false failures on every walk-through door.

_DIALOG_ECHO_WINDOW = 30  # frames; the intro IGT re-init lands +1 frame after
# control is regained (live journal 2026-06-14), but the recency is measured
# from the last in-textbox/cutscene poll, which may be a few frames earlier when
# polls are sparse — so allow ~1 s, same as the door window and for the same
# reason. The intro spawn is fresh-file-only; no human meaningfully L-resets
# within a second of a textbox, so an eaten borderline reset (segment stays
# armed) is cheaper than the false ~1-frame reset on every textbox.

# Staleness budget for a LOOSE arm (spec 2026-07-28-multi-step-segments).
# Loose matching removes every cancel rule that used to bound an arm, so
# without a deadline a segment the player abandoned reads "Running" until the
# next F1 — the exact symptom of the 2026-07-24 live report (WF -> SSL stuck
# running in an unrelated course), reintroduced by design rather than by bug.
#
# MEASURED 2026-07-28 (Task 9, tools/measure_budget.py) against the real
# journal: 18,656 events, 2026-06-11 -> 2026-07-28, all 67 seeded definitions
# replayed with match_mode forced "loose" (the mode the budget applies to —
# the stored definitions keep their own authored mode). 106 timed segment
# completions (105 with each def's own stored mode; the +1 is just the
# handful of plain movements this corpus happens to have, not a verdict on
# the feature — the movements that actually NEED loose matching, e.g. Bowser
# 2 -> BitS, 100-coin -> exit star, Bowser reds -> pipe, don't exist in the
# corpus until a later task). Distribution: min 219, median 1188, p95 2733,
# MAX 4244 frames (141.5 s).
#
# MIN_BUDGET_FRAMES = 5400 is the Task 3 starting guess, CONFIRMED rather
# than replaced: the grid below (min_frames, expired-completion-count at
# BUDGET_FACTOR in {3,4,6,8}) shows 3600 still clips 3-4 real completions,
# while 4500 is the first round value to clip zero — bisection puts the
# exact zero-margin boundary at 4245 (one frame past the observed max). That
# boundary is only 6% above 4244, i.e. no headroom for ordinary run-to-run
# variance on the SAME 16 defs that have any history at all, let alone the
# longer loose-native movements a later task is about to seed. 5400 keeps a
# 27% margin over the observed max and is the smaller of the two once
# "margin" is taken seriously, so it survives unchanged:
#   1800  ->  5-8 expired   2700  ->  3-4 expired   3600  ->  3-4 expired
#   4500  ->  0 expired     5400  ->  0 expired      7200  ->  0 expired
#
# BUDGET_FACTOR = 6 is UNFALSIFIED at the floor we ship, not confirmed the
# same way as MIN_BUDGET_FRAMES above — say so plainly. Only 16 of 67
# definitions have even one timed success (11 have five or more); the other
# 51 have no history, so BUDGET_FACTOR never applies to them at all.
#
# Below 4500, the four tested factor values (3/4/6/8) give different expired
# counts — see the grid above, 3 to 8 expired depending on the pair. At 4500
# and above, every one of the four gives 0 expired. 6 ships because nothing
# AT THE SHIPPED FLOOR (5400) contradicts it, not because this journal chose
# it over 3 or 8 — every tested factor already reads 0 there. Re-run
# tools/measure_budget.py once the loose-native movements above exist and
# have their own history; a longer typical completion is what would let the
# factor discriminate again at the values we ship.
MIN_BUDGET_FRAMES = 5400   # 3 minutes at 30 fps; the floor for a def with no history
BUDGET_FACTOR = 6          # multiple of the definition's best success so far


def budget_frames(best_success_frames: int | None) -> int:
    """How long a loose arm may run before it is presumed abandoned. Floored
    so a definition with no history — or a very fast one — still gets a
    humane window."""
    if not best_success_frames:
        return MIN_BUDGET_FRAMES
    return max(MIN_BUDGET_FRAMES, BUDGET_FACTOR * best_success_frames)


# Segment attempt ids live in a disjoint namespace from star attempt ids
# (which are raw journal ids): id = arm-event journal id + OFFSET * def_id.
# Stable across rebuilds, unique across defs armed by the same event, and
# the underlying journal id (for recency ordering) is id % OFFSET.
SEGMENT_ATTEMPT_OFFSET = 10 ** 10


@dataclass(frozen=True)
class MatchContext:
    level: int | None        # tracked level AFTER this event applied
    prev_level: int | None   # tracked level BEFORE this event
    num_stars: int | None    # last star_collected payload num_stars; None = unknown
    area: int | None = None  # tracked area AFTER this event (area_changed "to");
                             # None = unknown (legacy journals without area events)
    # (course_id, star_id) of the most recent star GRAB / attributed star
    # ATTEMPT (any outcome), tracked by the Projector from closed attempts;
    # None = unknown (fresh boot, post-game_reset, legacy journals) — the
    # last_star_* guards conservatively FAIL on None (spec 2026-07-23).
    last_star_grabbed: tuple | None = None
    last_star_attempted: tuple | None = None
    # Active-route scoping (spec 2026-07-23-default-routes-foundation): the
    # journaled route_selected member set, and the standalone segment target.
    # An in_active_route-guarded def arms only if its id is in one of these.
    # None/empty = no active route.
    route_segments: frozenset | None = None
    target_segment: int | None = None
    # The landmark catalogue (key -> name), for the moment pin's SAME-NAME
    # collapse (round 13 items 2+3): a star door is two objects, so a pin on
    # one half must fire when he pushes the other once both carry his one
    # name. None = no catalogue in hand (bare test contexts) = key equality
    # only, byte-for-byte the pre-collapse behaviour.
    landmark_names: dict | None = None


@dataclass(frozen=True)
class SegmentDef:
    id: int
    name: str
    enabled: bool
    start_triggers: list
    end_triggers: list
    guards: list
    # Ordered middle steps; [] = plain start/end pair. Defaulted (deviation
    # from the brief's non-default positional field, spec 2026-07-23): a
    # non-default field here would TypeError every existing SegmentDef(...)
    # construction that omits it, AND contradicts the brief's own
    # test_segmentdef_defaults_empty_waypoints, which constructs one without
    # passing waypoints and asserts it defaults to []. default_factory=list
    # keeps that test meaningful while `_load_segment_defs` still works
    # unchanged (the db row always supplies the key — Task 1).
    waypoints: list = field(default_factory=list)
    # The strategy this segment is practiced with unless the user picks another
    # (spec 2026-07-24-segment-default-strat). None = no default, which is what
    # every user-created segment and the ten legacy trick defs carry; the 55
    # castle movements carry "Standard", because there is basically one way to
    # do a movement. Applied by Projector (it pre-seeds strat_by_segment), NOT
    # here — the matcher is strategy-blind and stays that way. Defaulted for
    # the same reason waypoints is.
    default_strat: str | None = None
    # Which armed-branch matcher runs for this definition (spec
    # 2026-07-28-multi-step-segments). Defaulted to "strict" for the same
    # reason waypoints and default_strat are: a non-default field would
    # TypeError every existing SegmentDef(...) construction that omits it, and
    # "strict" is what every pre-migration row means. New rows are created
    # "loose" by db.insert_segment_def — an authoring default, not a claim
    # about existing data.
    match_mode: str = "strict"
    # The entity this is a SUBSECTION of, or None for a top-level segment --
    # which is every definition that existed before 2026-08-05 (task 0087).
    # Defaulted for the same reason waypoints/default_strat/match_mode are: a
    # non-default field would TypeError every existing construction.
    #
    # THIS ONE FIELD is the whole difference between a segment and a
    # subsection. Everything a subsection needs -- attempts, personal bests,
    # strategies, ladders, ranks, the practice log, the builder, the matcher
    # -- already exists and is kind-dispatched between stars and segments
    # (rule 11), so a third KIND would fan out across roughly twenty files and
    # buy nothing. The selector filters on this for progressive disclosure,
    # and a castle MOVEMENT owns subsections through the identical field
    # ("segment:<id>"), which is why that case needs no mechanism of its own.
    #
    # The key format is the one sheet-library's mapping module already emits
    # ("star:<course>:<slot>" / "segment:<id>"), so a subsection this tooling
    # creates is directly mappable from the community sheet with no bridge on
    # either side.
    parent: str | None = None


# `area:<level>` / `area:<level>:<subarea>` — a castle-area parent, the
# recorder's terminal tile (round 14: "for the castle areas, those are the
# high level areas, so it shouldn't have a further drill down").
_PARENT_KEY = re.compile(r"^(?:star:\d+:\d+|segment:\d+|area:\d+(?::\d+)?)$")


@dataclass(frozen=True)
class TriggerType:
    key: str
    label: str
    # Card-facing phrasing (spec 2026-07-28-multi-step-segments, Task 6): the
    # SAME template BY DEFAULT, read as an imperative step ("Enter" / "Exit" /
    # "Grab the key") instead of editor voice ("You enter level" / "You exit
    # level" / "You grab a Bowser key / grand star") -- see
    # card_waiting_for_sentence. A type overrides `card_template` below only
    # when the editor's own template genuinely reads wrong on a card (fix
    # round 1, 2026-07-28: star_grabbed's "in {course}, star {star}" produced
    # "Grab the star in Dire, Dire Docks, star Board Bowser's Sub" -- visibly
    # a template artifact).
    card_label: str
    params: dict  # name -> {"kind": "level"|"area"|"course"|"star"|"int", "required": bool}
    template: str  # sentence after the type label: "{to} coming from {from}"
    match: Callable[[dict, object, MatchContext], bool]
    # None = card rendering reuses `template` verbatim (every type but
    # star_grabbed today). Set only when the shared template's WORD ORDER or
    # phrasing is wrong for the imperative card voice -- this is still the
    # SAME registry doing the SAME job one field further, not a second
    # renderer: it goes through the identical `_render_clause` tokenizer and
    # `_resolve_param` lookups, just against a different template string.
    card_template: str | None = None
    # Per-param FALLBACK TEXT for the card template only, keyed by param
    # name -- e.g. {"star": "a star"}. The editor's pruning rule drops a
    # param's whole literal+placeholder segment when its clause value is
    # unset (an optional `from` renders nothing rather than "coming from ");
    # that is right for a connector clause but wrong for star_grabbed's
    # `star` on a card, where dropping it silently would leave "Grab in
    # Bowser in the Fire Sea" with no object. A param listed here renders
    # UNCONDITIONALLY on the card, substituting this text when unset, so
    # "Grab a star in <course>" is what a course-only clause reads instead of
    # vanishing the word "star" entirely. Never serialized to vocab() (the
    # builder's own clause form has no fallback-text concept, only real
    # dropdown values) -- read only by `_render_clause` in card mode.
    card_fallbacks: dict = field(default_factory=dict)
    # THIRD voice, and the shortest: a two-or-three-word noun for the practice
    # card's one-line step track (2026-08-03), where a chip has room for a
    # place and nothing else. Most types never need it — a clause naming a
    # world node is labelled by `node_short_label` and never reaches this — so
    # None means "fall back to card_label", which is why the registry carries
    # only the three rows whose card voice is a verb phrase ("Enter the pipe")
    # where the track wants the thing itself ("Pipe").
    chip_label: str | None = None


def _real_edge(ev) -> bool:
    # establishing/corrective level & area events may carry from == to;
    # those are bookkeeping, not movement — never an anchor.
    return ev.payload.get("from") != ev.payload.get("to")


def _only_castle(param: str) -> dict:
    """A castle-subarea param applies only when its companion level param is
    the Castle Inside interior (level 6) — the only level with named subareas.
    The builder reads only_when to show/hide the selector; the matcher does NOT
    gate on it (a subarea set against a non-castle level just never matches,
    since that level has no such area index)."""
    return {"param": param, "equals": LEVEL_CASTLE_INSIDE}


# `flow` annotations on the level_enter/level_exit params: the builder
# (ui/components/segments.js) constrains each side's dropdown to world-
# possible moves (addresses.WORLD_EDGES_*, shipped as vocab "connections").
# A "dest"-role param filters by the source side's SUCCESSORS, a "source"
# param by the destination's PREDECESSORS; peer/peer_subarea name the sibling
# params carrying the other side. UI-only — validation and the matcher never
# read flow (the Usamune warp menu can fabricate any edge, and stored defs
# must keep working regardless of the topology table).
_DEST_FLOW = {"role": "dest", "peer": "from", "peer_subarea": "from_subarea"}
_SOURCE_FLOW = {"role": "source", "peer": "to", "peer_subarea": "to_subarea"}


# NB: views._segment_start_areas (the castle quick-select banner) reads the
# `to_subarea`/`area` PARAM NAMES off these trigger dicts STATICALLY to decide
# which segments a subarea offers — it depends on those names, NOT the match
# lambdas. A rename here silently breaks the banner; the contract is pinned by
# test_views.test_segment_banner_param_names_match_the_registry.
TRIGGERS: dict[str, TriggerType] = {t.key: t for t in [
    # level_enter/level_exit gain a conditional subarea on EACH side (to/from);
    # the selector is hidden unless that side is Castle Inside (only_castle).
    # SOURCE subarea (from_subarea) reads from_area off the level edge — Mario
    # was settled there, so the lambda checks it directly. DESTINATION subarea
    # (to_subarea) is NOT checked here: the castle loads the lobby transiently
    # before warping to the real area a poll later (detectors/level.py), so the
    # lambda matches the level+from+from_subarea and the ENGINE defers a
    # to_subarea match into _pending, arming once the settled co-frame area
    # matches (SegmentEngine._pending). to_subarea is therefore honoured only on
    # START triggers; on an END trigger the destination subarea is ignored.
    TriggerType("level_enter", "You enter level", "Enter",
                {"to": {"kind": "level", "required": True,
                        "flow": _DEST_FLOW},
                 "to_subarea": {"kind": "subarea", "required": False,
                                "only_when": _only_castle("to"),
                                "flow": _DEST_FLOW},
                 "from": {"kind": "level", "required": False,
                          "flow": _SOURCE_FLOW},
                 "from_subarea": {"kind": "subarea", "required": False,
                                  "only_when": _only_castle("from"),
                                  "flow": _SOURCE_FLOW}},
                "{to} {to_subarea} coming from {from} {from_subarea}",
                lambda p, ev, ctx: ev.type == "level_changed" and _real_edge(ev)
                and ev.payload["to"] == p["to"]
                and (p.get("from") is None or ev.payload["from"] == p["from"])
                and (p.get("from_subarea") is None
                     or ev.payload.get("from_area") == p["from_subarea"])),
    TriggerType("level_exit", "You exit level", "Exit",
                {"from": {"kind": "level", "required": True,
                          "flow": _SOURCE_FLOW},
                 "from_subarea": {"kind": "subarea", "required": False,
                                  "only_when": _only_castle("from"),
                                  "flow": _SOURCE_FLOW},
                 "to": {"kind": "level", "required": False,
                        "flow": _DEST_FLOW},
                 "to_subarea": {"kind": "subarea", "required": False,
                                "only_when": _only_castle("to"),
                                "flow": _DEST_FLOW}},
                "{from} {from_subarea} going to {to} {to_subarea}",
                lambda p, ev, ctx: ev.type == "level_changed" and _real_edge(ev)
                and ev.payload["from"] == p["from"]
                and (p.get("to") is None or ev.payload["to"] == p["to"])
                and (p.get("from_subarea") is None
                     or ev.payload.get("from_area") == p["from_subarea"])),
    # "enter area" is the castle-region condition (live-confirmed semantics
    # 2026-06-12): the region dropdown offers only the castle hubs
    # (CASTLE_REGION_LEVELS), and the subarea is OPTIONAL — "Any" / a single-
    # area hub matches any area in that level. Matches area_changed, so it
    # fires on intra-castle movement too (lobby->basement = "enter Basement"),
    # unlike level_enter which fires only on the level boundary crossing.
    # `from` scopes the SOURCE subarea ("enter Basement coming from Lobby",
    # live request 2026-07-23) and additionally rejects TRANSIENT sources:
    # every castle entry passes through the lobby before settling
    # (detectors/level.py), so a course exit into the basement emits from=1
    # exactly like a genuine lobby walk — from_transient (detectors/area.py)
    # is the discriminator. Legacy events without the key conservatively
    # match (None = unknown -> match, the codebase-wide convention).
    # THE CASTLE GATES CAME OFF 2026-08-05 (task 0087). Both were in the
    # VOCABULARY, never in the matcher -- the lambda below has only ever
    # compared the payload's level to the clause's, and its `area` check is
    # equally level-agnostic. What the gates prevented was AUTHORING: the
    # level list was pinned to the castle regions and the two subarea
    # selectors only appeared for the castle interior, so "entered the SSL
    # pyramid" (level 8, area 2) and "left the LLL volcano" could not be
    # expressed at all -- and "entering a subarea within the level" is one of
    # the conditions subsections are actually built out of.
    #
    # `topology.node_for` still counts subareas only INSIDE the castle
    # interior, deliberately and unchanged: courses have their own areas and
    # the world graph does not model them, so such a clause places at LEVEL
    # granularity. That is a real answer rather than a gap -- the wrong-turn
    # cancel keeps working, one resolution coarser.
    TriggerType("area_enter", "You enter area", "Enter",
                {"level": {"kind": "level", "required": True},
                 "area": {"kind": "subarea", "required": False,
                          "only_when": _only_castle("level")},
                 "from": {"kind": "subarea", "required": False,
                          "only_when": _only_castle("level")}},
                "{level} {area} coming from {from}",
                lambda p, ev, ctx: ev.type == "area_changed" and _real_edge(ev)
                and ev.payload["level"] == p["level"]
                and (p.get("area") is None or ev.payload["to"] == p["area"])
                and (p.get("from") is None
                     or (ev.payload["from"] == p["from"]
                         and not ev.payload.get("from_transient", False)))),
    # Two conditions read the SAME journal event and mean different things,
    # and the difference is one sentence: `warp_entered` names where you ARE,
    # `entrance_touched` names where the entrance LEADS. Splitting them is a
    # live report (2026-08-05): the combined form put three controls on one row
    # -- "You touch a warp/pipe" IN Castle Inside GOING TO Dire, Dire Docks --
    # and required knowing that the DDD portal lives in the castle interior
    # before you could express "ends when I hit the DDD entrance" at all.
    # *"it's not obvious what this would mean... Like, a specific option for
    # triggering the warp into the course."*
    TriggerType("warp_entered", "You touch a warp/pipe here", "Touch the pipe",
                {"level": {"kind": "level", "required": True}},
                "in {level}",
                lambda p, ev, ctx: ev.type == "warp_entered"
                and ev.payload["level"] == p["level"],
                chip_label="Pipe"),
    # The ENTRANCE TOUCH: the frame Mario collides with the painting, portal,
    # hole or pipe that leads INTO a course -- 77 frames before it loads (23
    # at a pipe). ONE control, because the entrance's own level is derived
    # (topology.entrance_level) rather than asked for: the basement alone
    # hosts five exits, so the destination is the only thing that identifies
    # an entrance, and it is the only thing a player knows.
    #
    # A payload without `to` is a row written before 2026-08-04, when the
    # detector could not know one; `projection.warp_destinations` recovers it
    # on replay from the level edge that followed. Unrecovered, it matches
    # nothing here -- the conservative direction, since an old journal must
    # not start matching something new.
    TriggerType("entrance_touched", "You touch a course entrance",
                "Touch",
                {"to": {"kind": "level", "required": True,
                        "flow": _DEST_FLOW}},
                "to {to}",
                lambda p, ev, ctx: ev.type == "warp_entered"
                and ev.payload.get("to") is not None
                and ev.payload["to"] == p["to"],
                card_template="the {to} entrance",
                chip_label="Entrance"),
    TriggerType("key_grabbed", "You grab a Bowser key / grand star",
                "Grab the key",
                # key_grabbed claims all three fight-ending grabs: the Bowser
                # 1/2 keys AND the Bowser 3 grand star (which='grand', level
                # 34) — the grand star never fires star_collected, so a
                # "beat Bowser 3" segment ends HERE, not on star_grabbed.
                # See detectors/key.py.
                {"level": {"kind": "level", "required": False}},
                "in {level}",
                lambda p, ev, ctx: ev.type == "key_grabbed"
                and (p.get("level") is None
                     or ev.payload["level"] == p["level"]),
                chip_label="Key"),
    TriggerType("star_grabbed", "You grab a star", "Grab",
                {"course": {"kind": "course", "required": False},
                 "star": {"kind": "star", "required": False}},
                "in {course}, star {star}",
                lambda p, ev, ctx: ev.type == "star_collected"
                and (p.get("course") is None
                     or ev.payload["course_id"] == p["course"])
                and (p.get("star") is None
                     or ev.payload["star_id"] == p["star"]),
                # Card template leads with the STAR, editor leads with the
                # course -- "Grab Board Bowser's Sub in Dire, Dire Docks"
                # reads right; "Grab in Dire, Dire Docks, star Board
                # Bowser's Sub" is the template artifact this replaces (fix
                # round 1, 2026-07-28). `star`'s fallback keeps the object of
                # the sentence present even when the clause names a course
                # but no specific star ("Grab a star in <course>").
                card_template="{star} in {course}",
                card_fallbacks={"star": "a star"}),
    # THE SUBSECTION TRIGGER, and the only one in this registry that fires
    # without Mario going anywhere -- every other type is a place change or a
    # collection, which is why the journal was empty inside a course and a
    # subsection could not be authored at all (task 0087).
    #
    # It names no kind of its own: `kind` is matched against the payload, so
    # inventing a moment stays ONE ROW in detectors/moment.py's MOMENTS and
    # never touches this file. That indirection is the user's requirement --
    # "we need this to be flexible so that we allow for the invention and
    # innovation of new sections as needed" (2026-08-05).
    #
    # `ordinal` unset means ANY occurrence, which is what a subsection with
    # one unambiguous boundary wants; set, it is the Nth since the attempt
    # opened. It exists for START triggers: waypoints already order everything
    # after the arm, but "the 5th door in Big Boo's Haunt" is a start and a
    # start has no arm to count from.
    # CARD LABEL IS DELIBERATELY EMPTY, and it is the only type where that is
    # right. Every other card_label is the verb ("Enter", "Grab") because the
    # type fixes the verb and the params fill in the object. Here the VERB
    # varies per moment and lives on the moment's own label ("Open a door",
    # "Trigger a textbox"), so a type-level verb can only be prepended to a
    # phrase that already has one -- "Reach Open a door" was the first draft.
    # An empty label makes the card read "Open a door #5 in Big Boo's Haunt",
    # the imperative step the card voice asks for. tests/test_segments.py's
    # card-label containment check is vacuous for an empty string, so that
    # test names this type explicitly and asserts the moment's own label leads
    # the sentence instead -- the guard keeps its teeth rather than passing by
    # accident.
    # `landmark` (round 12 item 3) pins WHICH one — the catalogue key of the
    # specific door/pole/pickup, matched against the payload's own
    # `landmark.key`. The recorder writes it (a picked row means THAT door,
    # never "some first door"); there is no hand-authoring control for one,
    # the same way nobody hand-types a spawn coordinate. It renders through
    # the catalogue's name where the caller has one (see _resolve_param's
    # `names`), and it is deliberately NOT in the template: with no name the
    # sentence falls back to the kind's own wording rather than printing a
    # raw key at a human.
    TriggerType("moment_reached", "A moment happens", "",
                {"kind": {"kind": "moment", "required": True},
                 "ordinal": {"kind": "int", "required": False},
                 "landmark": {"kind": "landmark", "required": False},
                 "level": {"kind": "level", "required": False},
                 "area": {"kind": "subarea", "required": False,
                          "only_when": _only_castle("level")}},
                "{kind} #{ordinal} in {level} {area}",
                lambda p, ev, ctx: ev.type == "moment_reached"
                and ev.payload.get("kind") == p["kind"]
                and (p.get("ordinal") is None
                     or ev.payload.get("ordinal") == p["ordinal"])
                and (p.get("landmark") is None
                     or same_landmark(
                         p["landmark"],
                         (ev.payload.get("landmark") or {}).get("key"),
                         ctx.landmark_names if ctx else None))
                and (p.get("level") is None
                     or ev.payload.get("level") == p["level"])
                and (p.get("area") is None
                     or ev.payload.get("area") == p["area"]),
                # The one-line step track wants a thing, not a phrase; a
                # moment clause names a place so node_short_label normally
                # answers first, and this is the fallback for one that does
                # not pin a level.
                chip_label="Moment"),
    TriggerType("spawned", "You spawn into the game", "Spawn",
                {"level": {"kind": "level", "required": False}},
                "in {level}",
                lambda p, ev, ctx: ev.type == "spawned"
                and (p.get("level") is None
                     or ev.payload["level"] == p["level"])),
    TriggerType("attempt_anchor", "Practice reset / savestate load",
                "Reset or reload",
                {"level": {"kind": "level", "required": True},
                 "area": {"kind": "subarea", "required": False,
                          "only_when": _only_castle("level")}},
                "in {level} {area}",
                # Optional area scoping prevents cross-arming: a basement
                # respawn must not arm a lobby-anchored segment.  Added for
                # warp-menu arming (live gate 2026-06-12): Usamune's warp
                # menu (06 01 00) deposits Mario at the castle lobby
                # entrance with only a practice_reset — no level edge — so
                # LBLJ seeds attempt_anchor(level=6, area=1).  The area
                # detector journals before the anchor detector (main.py
                # order), so ctx.area is already the post-warp area when
                # the anchor arrives.  ctx.area None (legacy journals)
                # conservatively fails a scoped anchor.
                lambda p, ev, ctx: ev.type in ("practice_reset",
                                               "state_loaded")
                and ctx.level == p["level"]
                and (p.get("area") is None or ctx.area == p["area"])),
    TriggerType("reset_game", "The game resets (F1 / console reset)",
                "Reset the game",
                {}, "on F1 or console reset",
                lambda p, ev, ctx: ev.type == "game_reset"),
]}


def _resolve_param(kind: str, value, clause: dict,
                   names: dict | None = None) -> str:
    """Display text for one clause param, by the vocabulary's own KIND — the
    few TRIGGERS params ever carry. `star` also reads the clause's `course`
    (a star's name is meaningless without one; a course-less star clause
    falls back to the generic "Star N" addresses.star_name itself uses for
    an unrecognised course). `names` is the landmark catalogue where the
    caller has one — see _render_clause."""
    if kind == "level":
        return LEVEL_NAMES.get(value, f"Level {value}")
    if kind == "subarea":
        return CASTLE_AREA_NAMES.get(value, f"Area {value}")
    if kind == "course":
        return COURSE_NAMES.get(value, f"Course {value}")
    if kind == "star":
        course = clause.get("course")
        return star_name(course, value) if course is not None \
            else f"Star {value + 1}"
    if kind == "moment":
        # The moment's own label, never its wire key: detectors/moment.py
        # owns the wording, so "Open a door" cannot drift from what the
        # builder's dropdown and the timeline's sentence already say.
        #
        # A clause pinning a NAMED landmark reads by that name — "Open the
        # CCM Door", through the same verb splice eventlabel's rows use, so
        # the sentence he saves matches the row he picked (round 12 item 3).
        # Unnamed (or no catalogue in hand), the kind's own wording stands.
        label = _MOMENT_LABELS.get(value, str(value))
        named = (names or {}).get(clause.get("landmark"))
        return f"{_verb(label)} the {named}" if named else label
    return str(value)


_TEMPLATE_TOKENS = re.compile(r"(\{\w+\})")


def _render_clause(clause: dict, names: dict | None = None) -> str:
    """One trigger clause -> plain English for the practice card, through
    TRIGGERS[type].card_label + .card_template (spec 2026-07-28-multi-step-
    segments; card_waiting_for_sentence below is the only caller). Until
    Task 7 (2026-07-28) this also served an editor-voice sibling,
    waiting_for_sentence, through a pair of (label_attr, template_attr)
    parameters selecting which fields to read — deleted, along with that
    function, once it lost its last caller in `src/` (YAGNI: re-add the
    parameters WITH a second caller if editor voice is ever needed here
    again, not speculatively). `card_template` is None for every type but
    star_grabbed today, so `spec.card_template or spec.template` is a
    genuine no-op there — reusing the editor's template IS the default.

    A param the clause leaves unset drops its own SEGMENT of the template —
    the literal words that introduce it, together with the placeholder —
    rather than leaving a dangling connector: a level_enter clause with no
    `from` renders "You enter level Castle Inside", never "...coming from ".
    Done by pairing each placeholder with the literal text immediately
    BEFORE it (that is what "introduces" it, e.g. "coming from {from}");
    text after the LAST placeholder is unconditional trailing punctuation.
    A required param (the common case) is always present, so this only ever
    prunes an OPTIONAL one the author left blank ("any level" etc).

    A param named in `card_fallbacks` (fix round 1, 2026-07-28) is the ONE
    exception to that pruning: its segment renders unconditionally, using
    the fallback text in place of a resolved value when the clause leaves
    it unset. star_grabbed's `star` is the one param that needs this —
    pruning it would leave a course-only card clause reading "Grab in
    Bowser in the Fire Sea" with no object.

    A placeholder naming a param outside its own trigger's `params` (an
    authoring bug in TRIGGERS itself) renders the brace literally instead of
    raising — test_every_trigger_template_resolves_cleanly is the guard that
    catches this at test time, on the day such a type is added, rather than
    a user seeing the brace.

    This tokenizer is deliberately independent of
    ui/components/segments.js's, which builds its own editor sentence
    straight from the label/template vocab() ships raw — see
    card_waiting_for_sentence's docstring for why that is not a second
    door.

    `names` is the landmark catalogue (db.landmark_names()), passed by the
    one caller that has a db in hand (the synthesize endpoint) so a
    landmark-pinned moment clause reads "Open the CCM Door" — the sentence
    he picked the row by. Without it the kind's own wording stands, which is
    vague but never wrong."""
    spec = TRIGGERS[clause["type"]]
    template = spec.card_template or spec.template
    tokens = _TEMPLATE_TOKENS.split(template)
    parts: list[str] = []
    i = 0
    while i + 1 < len(tokens):
        literal_before, placeholder = tokens[i], tokens[i + 1]
        name = placeholder[1:-1]
        meta = spec.params.get(name)
        if meta is None:
            parts.append(literal_before)
            parts.append(placeholder)
        else:
            value = clause.get(name)
            if value is not None:
                parts.append(literal_before)
                parts.append(_resolve_param(meta["kind"], value, clause,
                                            names))
            elif name in spec.card_fallbacks:
                parts.append(literal_before)
                parts.append(spec.card_fallbacks[name])
        i += 2
    parts.append(tokens[-1])   # trailing literal after the last placeholder
    return f"{spec.card_label} {''.join(parts)}".strip()


def clause_sentence(clause: dict, names: dict | None = None) -> str:
    """Public entry point onto `_render_clause`, for callers OUTSIDE
    `tracking/` (Task 13's synthesize-preview API endpoint, behind the
    "record what I just did" timeline picker). Same card_label/card_template
    rendering `card_waiting_for_sentence` uses for an armed segment's
    "waiting for" line, so a synthesized-but-unsaved clause reads in the
    IDENTICAL voice a saved one would -- a one-line alias, not a second
    template walk. `names` (the landmark catalogue) lets a landmark-pinned
    clause read by the name he gave the thing."""
    return _render_clause(clause, names)


def card_waiting_for_sentence(d: SegmentDef, progress: int) -> str:
    """Plain language for what an ARMED definition is waiting for next (spec
    2026-07-28-multi-step-segments, Task 6): its next unconsumed waypoint
    (`d.waypoints[progress]`), or its end trigger once every waypoint is
    consumed (`progress >= len(d.waypoints)`), rendered through
    TRIGGERS[type].card_label + .card_template as an imperative STEP —
    "Enter Shifting Sand Land", never editor-voice "You enter level
    Shifting Sand Land". A clause-set is an ANY-OF list (see `_matches`) —
    its members join with " or ".

    Editor voice reads wrong under a "Waiting for" label: a second-person
    clause written to stand alone as a sentence ("You enter level X") reads
    as "Waiting for You enter level X", because that string was written to
    stand alone, not to fill a hole in a shorter one. The card supplies its
    own frame; this only needs to be the imperative step that goes in the
    hole — a different artifact from a sentence with a hole in it, not a
    shorter version of one. (This function had an editor-voice twin,
    waiting_for_sentence, deleted Task 7/2026-07-28 once it lost its last
    caller — see _render_clause's docstring.)

    Read-only sibling of ui/components/segments.js's ClauseRow, which
    tokenizes the SAME TRIGGERS[type].template into an editable FORM
    (dropdowns interleaved with muted words, entangled with
    setParam/onChange/visible()/allowedIds/vocab.connections). DESIGN
    QUESTION settled before waiting_for_sentence was first written
    (progress.md, Task 4): that is a different artifact from a read-only
    "waiting for X" string, not a second implementation of one thing to
    unify with. Both consumers read the ONE TRIGGERS registry and the same
    vocab enums (generated from Python), so neither restates the template —
    the JS side edits a clause, this side describes one. Do not "helpfully"
    merge them.

    What IS shared with the editor voice is the template/param-pruning
    machinery in _render_clause, since that is the same mechanical
    substitution regardless of voice — `card_template` (fix round 1,
    2026-07-28) is still that SAME machinery, reading a different template
    string for the one type (star_grabbed) whose shared template read as a
    visible artifact on a card ("Grab the star in Dire, Dire Docks, star
    Board Bowser's Sub")."""
    clause_set = (d.waypoints[progress] if progress < len(d.waypoints)
                 else d.end_triggers)
    return " or ".join(_render_clause(clause) for clause in clause_set)


def _step_chip(clause: dict) -> str:
    """The shortest honest name for ONE clause: the place it lands on."""
    node = step_node(clause)
    if node is not None:
        return node_short_label(node)
    kind = clause.get("type")
    if kind == "star_grabbed" and clause.get("course") is not None:
        return star_name(clause["course"], clause.get("star", 0))
    trigger = TRIGGERS.get(kind)
    if trigger is None:
        return str(kind)
    return trigger.chip_label or trigger.card_label


def card_step_labels(d: SegmentDef) -> list[str]:
    """Every step this definition requires, shortest-form, in order —
    its waypoints then its end trigger, one label each.

    The card draws these as a single line of chips with the arm's own
    `progress` marking which are done, which is live, and which are still
    ahead ("✓ Basement › ▶ SSL"). `card_waiting_for_sentence` answers a
    different question — the FULL imperative for the one step you are on —
    and both are shipped, because a track with room for a place has no room
    for "coming from Bowser in the Fire Sea" and a player still needs to be
    told which door.

    A clause SET is an any-of (see `_matches`), so its members collapse to
    their distinct labels. The one shape in the shipped corpus that has more
    than one is the 100-coin exit's end — six `star_grabbed` alternatives in
    a single course, meaning "leave with anything" — which reads as
    **Any star** rather than as six names nobody can fit on a line. Stated
    as a rule about the clauses (same type, same course, more than two), not
    as a lookup for that family, so a user-authored def of the same shape
    reads the same way.
    """
    labels = []
    for clause_set in list(d.waypoints) + [d.end_triggers]:
        distinct = list(dict.fromkeys(_step_chip(c) for c in clause_set))
        if len(distinct) == 1:
            labels.append(distinct[0])
        elif len(clause_set) > 2 \
                and {c.get("type") for c in clause_set} == {"star_grabbed"} \
                and len({c.get("course") for c in clause_set}) == 1:
            labels.append("Any star")
        else:
            labels.append(" / ".join(distinct))
    return labels


def arm_level(trig: dict) -> int | None:
    """Level Mario stands in the moment this START trigger arms, or None
    when the trigger carries no (or an unknowable) arm location — reads the
    same param NAMES as the registry rows above, decoupled from the match
    lambdas. Shared by views.py's quick-select banner helpers and the
    projector's segment-target retirement (2026-07-23)."""
    kind = trig.get("type")
    # `moment_reached` places exactly like `area_enter`: a moment names where
    # it HAPPENS, and Mario is standing there when it fires. Missing until
    # 2026-08-05, which made every subsection started by a moment invisible in
    # every selector row -- the row filters on this, so a definition it cannot
    # place is a definition nobody can pick. Same reason `step_node` has its
    # own branch for the type.
    if kind in ("area_enter", "attempt_anchor", "spawned", "moment_reached"):
        return trig.get("level")
    # `entrance_touched` is the SAME SHAPE, and Griffin named it as one on
    # 2026-08-05: "the event for entering a course warp, not actually warping
    # into it... probably a pretty overarching theme in these types of
    # segments". Its `to` is where the entrance LEADS, and Mario does not
    # arrive for 77 frames -- so the arm level is where the ENTRANCE lives,
    # which is the one derivation `topology.entrance_level` owns (the same
    # door `fires_from` already checks arm positions against). Reading `to`
    # here instead would place the definition in a course the player is not
    # standing in yet.
    if kind == "entrance_touched":
        return topology.entrance_level(trig.get("to"))
    if kind in ("level_enter", "level_exit"):
        return trig.get("to")   # level_exit: Mario ends up at the DESTINATION
    return None


# The world NODE a clause leaves Mario standing in once it fires (spec
# 2026-08-01-topological-segment-validity). This is the FOURTH table in this
# module read as param NAMES rather than through the match lambdas, and each
# answers a genuinely different question: `arm_level` = which LEVEL a START
# trigger leaves him in; `_ORIGIN_PARAMS` = where a definition may be picked
# from; `_PRECONDITION_PARAM` = where a clause must fire FROM; this one = the
# node a NEXT step lands him on, which is what the hop-distance rule measures.
#
# A type with no branch here, or a branch whose param the clause leaves unset,
# answers None = "this step names no place" — UNCONSTRAINED, the codebase's
# unknown-means-yes convention. That is what exempts every Bowser fight
# (key_grabbed), every star-ending step and every unpinned `level_exit` from
# the topological rules, rather than a list of special cases somebody would
# have to maintain. `warp_entered` (the three legacy pipe defs) stays in that
# list; `entrance_touched`, which replaced it on 55 definitions, does not —
# see its branch below.
#
# NOTE a `level_enter to=6` step with no `to_subarea` resolves to the bare "6",
# which is NOT a node in WORLD_EDGES_* (the interior is keyed by subarea), so
# `topology.hops` answers None and such a step is unconstrained. Deliberate:
# mapping it to the lobby the way `region_for_node` does would be a claim about
# where the player ends up that the clause itself does not make.
def step_node(clause: dict) -> str | None:
    kind = clause.get("type")
    if kind in ("level_enter", "level_exit"):
        return topology.node_for(clause.get("to"), clause.get("to_subarea"))
    if kind == "area_enter":
        return topology.node_for(clause.get("level"), clause.get("area"))
    if kind == "entrance_touched":
        # The touch fires 77 frames before Mario arrives, so he is still in the
        # castle when it does -- but the node this STEP leads to is the
        # destination, and that is what the hop-distance rule measures. It
        # therefore resolves exactly as the `level_enter` it replaced on 55
        # definitions (task 0081): standing in the basement DDD is 1 hop,
        # wander to the lobby and it is 2, so Rule 2 fires unchanged.
        #
        # Getting this wrong would have been SILENT and total: None means
        # unconstrained, so re-pointing the whole castle corpus onto a
        # place-less clause would have switched the topological cancel off for
        # every movement at once with nothing going red. `warp_entered` keeps
        # answering None and stays unconstrained, as it always has.
        return topology.node_for(clause.get("to"), None)
    if kind == "moment_reached":
        # A moment names where it HAPPENS, so it places exactly like an
        # area_enter. Answering None here would mean UNCONSTRAINED and would
        # switch the topological wrong-turn cancel off for every subsection at
        # once, with nothing going red -- the silent, total failure task 0081
        # documents for `warp_entered`. A clause naming no level still answers
        # None, which is the same "no constraint" every place-less clause
        # already gives and is a real answer rather than a gap.
        return topology.node_for(clause.get("level"), clause.get("area"))
    return None


def declared_nodes(d) -> frozenset:
    """Every world node this definition NAMES as a step of its own route — its
    waypoints and its end triggers (spec 2026-08-01-topological-segment-
    validity).

    Griffin's nuance, and the whole of Rule 2's exemption: sometimes you
    genuinely enter a stage in order to use its exit, and a route that really
    does pass back through somewhere DECLARES it. A node the definition names
    is therefore never a wrong turn, however the hop count moves.

    Read as a SET rather than by comparing against the arm's live `progress`,
    which was the first design and is subtly wrong: the waypoint match and the
    position judgement land on the same game frame but on DIFFERENT events (the
    `level_changed` advances progress, the co-frame `area_changed` records the
    move, and the judgement runs a frame later). By then the arm is already
    measuring against its NEXT step, so a correctly-followed waypoint reads as
    a move away from what comes after it, and the declared re-entry cancels
    itself. A set has no such ordering to get wrong.

    Start triggers are deliberately excluded: they say where the route BEGAN,
    not where it goes, and returning to your own start is exactly the shape
    (exit LLL, walk back into LLL) this rule exists to catch.
    """
    nodes = set()
    for step in list(d.waypoints) + [d.end_triggers]:
        for clause in step:
            node = step_node(clause)
            if node is not None:
                nodes.add(node)
    return frozenset(nodes)


def path_nodes(d) -> tuple:
    """The world nodes this definition names as steps of its own route, IN
    ORDER — its waypoints first, then its end trigger (spec
    2026-08-02-strict-path-segments).

    A TUPLE and not `declared_nodes`'s frozenset, because a set cannot hold a
    place twice and cannot say which way you were walking. Both are properties
    Griffin asked for by name: `SSL → SSL → LLL` is two cursor positions that
    happen to name the same place, and the Lobby is legal walking IN to a
    `WF → Basement → SSL` and a deviation walking back out of it.

    This is a SECOND READER of the same data, not a replacement — the set is
    still right for its own job (see `declared_nodes`: the waypoint match and
    the position judgement land on the same frame via different events, so an
    index compared against the arm's live `progress` reads a correctly-followed
    waypoint as a move away from what comes next). The cursor this feeds is
    advanced only by the SETTLED position, of which there is exactly one per
    frame, so it has no such race.

    A clause-set contributes ONE node when its members agree and NOTHING when
    they disagree or name none. An any-of step means "either is fine" and a
    cursor cannot hold two positions, so it declines to constrain — the same
    unknown-means-yes convention `step_node` and `topology.hops` already take,
    and what keeps the 100-coin family and every Bowser fight out of the rule
    without an exemption list. Contributions are SKIPPED rather than padded
    with None: the cursor must never have to step over a hole.
    """
    path = []
    for step in list(d.waypoints) + [d.end_triggers]:
        nodes = {step_node(clause) for clause in step}
        if len(nodes) == 1:
            node = nodes.pop()
            if node is not None:
                path.append(node)
    return tuple(path)


def start_areas(start_triggers: list) -> list:
    """[[level, area], …] — the castle SUBAREAS a segment explicitly starts in.

    Subarea-scoped triggers only, so a whole-level rule never claims every
    subarea (that is what keeps LBLJ out of Upstairs). Derived from the trigger
    param NAMES (stable across the matcher), so this stays decoupled from the
    registry above:
      area_enter / attempt_anchor / moment_reached : (level, area)
      level_enter / level_exit    : (to, to_subarea)   [to_subarea exists once
          the subarea-trigger work lands; until then .get() returns None and the
          row contributes nothing — forward-safe]
    The UI (ui/components/stagebanner.js) filters these by the current castle
    subarea (stage_changed carries level+area) to offer one-click segment
    targets. NOT the reader for "may this be practiced here" — that is
    start_origin (see tracking/practicable.py): arm_level answers None for 50
    of the 51 seeded level_exit clauses, so these two place only 11 of 65
    definitions and the banner shows no castle movement at all.
    """
    out: list = []
    for trig in start_triggers:
        kind = trig.get("type")
        if kind in ("area_enter", "attempt_anchor", "moment_reached"):
            level, area = trig.get("level"), trig.get("area")
        elif kind in ("level_enter", "level_exit"):
            level, area = trig.get("to"), trig.get("to_subarea")
        elif kind == "entrance_touched":
            # Derived, never asked for: an entrance clause carries only where
            # it LEADS, and the place Mario touches it from is the world
            # graph's answer (`topology.entrance_node` -- the same door
            # `arm_level` and `fires_from` use). The subarea is the whole
            # point here: the level alone says "Castle Inside" for every
            # basement and lobby entrance alike, which no row can filter on.
            node = topology.entrance_node(trig.get("to"))
            level = topology.entrance_level(trig.get("to"))
            area = topology.node_area(node)
        else:
            continue
        if level is not None and area is not None and [level, area] not in out:
            out.append([level, area])
    return out


def start_levels(start_triggers: list) -> list:
    """The LEVELS a segment explicitly starts in, ignoring subarea.

    The Bowser banner (BitDW/BitFS/BitS courses + the 1/2/3 arenas) has no
    castle-style subareas — it offers segments by level alone (pipe-entry
    segments start in level 17/19/21; fight segments in 30/33/34). Reads the
    same trigger param NAMES as start_areas, taking only the level; `spawned`
    carries a level too (e.g. Lakitu Skip). The UI filters these by the current
    level.
    """
    out: list = []
    for trig in start_triggers:
        level = arm_level(trig)
        if level is not None and level not in out:
            out.append(level)
    return out


def start_level_set(start_triggers: list,
                     waypoints: list | None = None) -> set[int] | None:
    """Levels this segment can plausibly occupy while it's the active
    practice target — its start triggers UNION every waypoint step's
    clauses — or None when that is unknowable — any location-free clause
    (star_grabbed / key_grabbed without a level / reset_game / ...) means
    "can be anywhere". The projector retires a segment target on entering a
    level outside this set (a level-bound segment cannot possibly be the
    active practice focus from a level it can't occupy — user report
    2026-07-23); None never retires.

    Waypoints matter for MULTI-LEVEL segments (spec
    2026-07-23-default-routes-foundation, fix 2026-07-24): a segment whose
    sequence re-enters an earlier level (e.g. SL->HMC starts on `level_exit
    from=10 to=16` but waypoints re-enter SL at level 10) would otherwise
    have its target wrongly retired the instant a waypoint lands back in a
    level outside the START set alone — the bug this function's waypoints
    parameter fixes. Defs with no waypoints (today, all ten seeded defs)
    reproduce the pre-fix result exactly."""
    if not start_triggers:
        return None
    levels = set()
    for trig in start_triggers:
        level = arm_level(trig)
        if level is None:
            return None
        levels.add(level)
    for step in (waypoints or []):
        for clause in step:
            level = arm_level(clause)
            if level is None:
                return None
            levels.add(level)
    return levels


# course id -> its level, for star_grabbed clauses. COURSE_BY_LEVEL is 1:1.
_LEVEL_BY_COURSE = {course: level for level, course in COURSE_BY_LEVEL.items()}


# --- Can this segment be RUN from here? (arm-position plausibility) --------
# A start trigger says what HAPPENED, not where it left Mario standing: 50 of
# the 51 seeded `level_exit from=X` clauses omit `to`, because in the real
# world every course exit lands in the castle. The Usamune warp menu fabricates
# edges the world does not have — a WF -> CCM menu warp is ONE level_changed
# 24 -> 5 — so "you left WF" fired with Mario standing in Cool, Cool Mountain
# and armed WF -> SSL there, a castle movement that cannot be run from inside a
# course. Nothing disarms a def whose player then stays put, so it read as
# "ACTIVE SEGMENT  WF -> SSL  Running" for six minutes of CCM practice (live
# report 2026-07-27, journal ids 1547-1564).
#
# Three questions, none of which consults the world-edge table: a stored def
# must keep matching whatever edges the emulator invents (the same reason
# TRIGGERS' `flow` annotations are UI-only), and a check derived from that
# table could only ever be tested against the table it came from.
#   A. can the segment's NEXT required step still fire from here? (fires_from)
#   B. did an unpinned course exit land somewhere a course exit cannot?
#   C. are we already standing at every finish it has?  (_end_destination_level)
# Any "no" makes the arm impossible and the engine refuses it, so the armed
# set — which is what the practice page pins and calls ACTIVE SEGMENT — can
# never name a segment the player is standing somewhere unable to run.

# The level Mario must ALREADY be standing in for a clause to fire, as the
# param NAME carrying it — read as data, exactly like arm_level and
# _ORIGIN_PARAMS read the registry's param names rather than its match lambdas.
# A new trigger type is one row here, or "anywhere" by default.
#
# NB this is a THIRD, distinct mapping: `level_exit from=8` FIRES from SSL,
# ARMS in the castle (arm_level -> `to`), and is FILED under SSL
# (_ORIGIN_PARAMS). level_enter and star_grabbed carry no row because their
# precondition is not a plain param read — see fires_from.
_PRECONDITION_PARAM: dict[str, str] = {
    "level_exit": "from",
    "area_enter": "level",
    "warp_entered": "level",
    "key_grabbed": "level",
    "attempt_anchor": "level",
    "spawned": "level",
    # A moment fires where Mario is standing, so its `level` is both where it
    # happens and where it must be firable from.
    "moment_reached": "level",
}


def fires_from(trig: dict, level: int) -> bool:
    """Could this clause fire with Mario standing in `level`?

    True whenever the clause names no place it must fire from (reset_game, an
    unscoped key grab, a Toad star) — the codebase-wide "unknown means yes".
    """
    kind = trig.get("type")
    if kind == "level_enter":
        # A level entry needs a REAL edge (_real_edge: from != to), so an
        # unscoped one fires from anywhere EXCEPT its own destination — that
        # single exclusion is what stops a fabricated WF -> CCM warp arming
        # "WF -> CCM" inside CCM, where it could only ever hang armed.
        source = trig.get("from")
        # `.get("to")`, not `trig["to"]`: this was the last bare subscript in
        # the function, and it was safe only while every caller fed VALIDATED
        # definitions. Task 16's lint endpoint is the first that feeds
        # in-progress editor state, where a just-added clause is bare
        # `{"type": "level_enter"}` -- and this raised KeyError -> 500 while
        # the editor rendered the error beside Save. An unknown destination
        # excludes nothing, so `level != None` is True: fires from anywhere,
        # which is this module's own unknown-means-yes convention.
        return level == source if source is not None else level != trig.get("to")
    if kind == "star_grabbed":
        # A star grab happens in its course's level; course 0 (the castle
        # secret stars) and an unscoped clause name no level of their own.
        course_level = _LEVEL_BY_COURSE.get(trig.get("course"))
        return course_level is None or level == course_level
    if kind == "entrance_touched":
        # An entrance clause names no level, so the level it must fire FROM is
        # derived -- the same one door the corpus authors through, so the gate
        # and the corpus cannot disagree about where an entrance lives. None
        # (an unknown destination) is unconstrained, as everywhere else here.
        entrance = topology.entrance_level(trig.get("to"))
        return entrance is None or entrance == level
    param = _PRECONDITION_PARAM.get(kind)
    required = trig.get(param) if param else None
    return required is None or level == required


def _exit_landing_is_impossible(start_clause: dict, level: int) -> bool:
    """A course exit lands in the castle — did this one land somewhere else?

    52 of the 53 seeded `level_exit` clauses omit `to`, so the DEFINITION says
    nothing about where the player ends up and the emulator decides. The world
    is hub-and-spoke: leaving anything but a castle level (6/16/26) puts Mario
    in one, so an arm on such a clause that lands elsewhere is a menu warp and
    the movement it would time never happened. This is what stops a WF -> CCM
    warp arming "WF -> Secret Aquarium" in Cool, Cool Mountain — a def that (A)
    waves through, since its `level_enter to=20` end is firable from any level
    but 20, and that in reality can now only ever disarm.

    The two one-way in-course shortcuts the world does have (DDD -> BitFS
    through the sub, HMC -> CotMC) are rejected too, deliberately: no seeded
    movement arms on either, and one that did would arm doomed — its remaining
    route needs the castle, and the level change back there disarms it.
    """
    return (start_clause.get("type") == "level_exit"
            and start_clause.get("to") is None
            and start_clause["from"] not in CASTLE_LEVELS
            and level not in CASTLE_LEVELS)


def _end_destination_level(trig: dict) -> int | None:
    """The level this end clause leaves Mario standing in, when that is
    knowable AT ARM TIME — otherwise None.

    Only `level_enter` qualifies, and the omissions are deliberate rather than
    unfinished. `area_enter` names a castle SUBAREA, and an arm taken on a
    level_changed records a STALE ctx.area (the area detector establishes the
    new area one event later, same tick — see feed), so "are we already there"
    cannot be answered for it; every seeded movement that ends in a castle area
    legitimately arms elsewhere in the castle, which is exactly the case a
    level-only comparison would wrongly reject. A `level_exit` end names where
    Mario leaves FROM, not where he lands.
    """
    return trig.get("to") if trig.get("type") == "level_enter" else None


def can_run_from(d, start_clause: dict, level: int | None) -> bool:
    """Could a segment freshly armed by `start_clause`, with Mario standing in
    `level`, still be run to completion? The engine's arm gate — see the
    section comment above.

    Unknown position (legacy journals carry no level events) always passes:
    "could be anywhere", the same conservative reading start_level_set takes.
    """
    if level is None:
        return True
    # (B) see _exit_landing_is_impossible.
    if _exit_landing_is_impossible(start_clause, level):
        return False
    # (C) A journey cannot start at its own destination. Redundant with (B) for
    # the seeded corpus, but it is the rule that holds for a def whose start
    # DOES pin a destination, and it is what (A) cannot see for a WAYPOINT-
    # bearing def — that one still has an unrelated first step to take, so a
    # BBH -> DDD menu warp armed "BBH -> DDD" inside DDD, whose remaining route
    # (basement, then DDD again) is walkable and would have banked a bogus
    # attempt.
    destinations = [_end_destination_level(c) for c in d.end_triggers]
    if destinations and all(dest == level for dest in destinations):
        return False
    # (A) Whatever comes next is the ONLY thing that can happen before this def
    # disarms: a plain def is closed or silently disarmed by the next level
    # change, a waypoint-bearing one advances or is cancelled by it. So the
    # step after the arm has to be firable from where the arm actually landed.
    nxt = d.waypoints[0] if d.waypoints else d.end_triggers
    return any(fires_from(clause, level) for clause in nxt)


# --- Segment ORIGIN: where a definition can start (spec 2026-07-24) --------
# Per-trigger source of the arm POSITION, as (level param, subarea param) —
# read as data, exactly like arm_level reads the registry's param names rather
# than its match lambdas. Adding a trigger type to TRIGGERS means adding one
# row here, or accepting None ("Anywhere") by default.
#
# NB this is NOT arm_level's mapping: a level_exit ARMS at its destination but
# ORIGINATES at its source. "SSL -> LLL" is filed under SSL because that is
# what the rule keys on (52 of the 53 seeded exits omit `to`; the one that
# carries it, MIPS Clip, is still filed by its source, which is the point).
_ORIGIN_PARAMS: dict[str, tuple[str, str | None]] = {
    "level_exit": ("from", "from_subarea"),
    "level_enter": ("to", "to_subarea"),
    "area_enter": ("level", "area"),
    "attempt_anchor": ("level", "area"),
    "spawned": ("level", None),
    "warp_entered": ("level", None),
    "key_grabbed": ("level", None),
    # A subsection is practiced where its first moment fires (task 0087).
    "moment_reached": ("level", "area"),
}

ANYWHERE_LABEL = "Anywhere"


def star_origin(course: int | None, star: int | None = None) -> str | None:
    """The world node a STAR is practiced in. Course 0 (castle secret stars)
    has no level of its own — only the MIPS catches are known
    (CASTLE_SECRET_STAR_AREAS); anything else stays unplaced.

    The star-side sibling of start_origin, so "where does this live" has ONE
    answer per entity KIND and both are the same node vocabulary — which is
    what lets tracking/practicable.py ask one question about either.
    """
    if course is None:
        return None
    if course == 0:
        area = CASTLE_SECRET_STAR_AREAS.get(star)
        return node_key(LEVEL_CASTLE_INSIDE, area) if area is not None else None
    level = _LEVEL_BY_COURSE.get(course)
    return node_key(level) if level is not None else None


def stage_origin(level: int | None, area: int | None = None) -> str | None:
    """The world node the PLAYER is standing in, in the same vocabulary.

    Only the castle interior is keyed by subarea — every other level is one
    place, and courses have interior areas of their own (CCM's slide is area
    2) that must not split a course into two nodes.
    """
    if level is None:
        return None
    return node_key(level, area if level == LEVEL_CASTLE_INSIDE else None)


def origin_course(node: str | None) -> int | None:
    """The COURSE a world node belongs to, or None for the castle interior,
    the hubs and the Bowser arenas -- the places that are TRANSIT.

    This is the vocabulary the retirement rule speaks (projection.py caveat
    12): setting a target needs you standing exactly at its node, but staying
    on it only needs you not to have walked into a different course, because
    every course is entered through the castle. So a card may keep showing a
    castle movement while you walk back to its start, and must stop the moment
    you are somewhere else entirely -- a segment practiced in the lobby still
    read "ACTIVE SEGMENT LBLJ" inside Whomp's Fortress and again in Hazy Maze
    Cave (live report 2026-07-27).

    NOT views.segment_courses, which asks the same question through
    `start_levels` (= `arm_level`, where a trigger LEAVES Mario) and so
    answers None for 54 of the 65 seeded definitions -- including every
    movement that starts in a course. That reader is why this one exists.
    """
    if node is None:
        return None
    return course_for_level(int(node.partition(":")[0]))


def _clause_origin(trig: dict) -> str | None:
    kind = trig.get("type")
    if kind == "star_grabbed":
        return star_origin(trig.get("course"), trig.get("star"))
    params = _ORIGIN_PARAMS.get(kind)
    if params is None:
        return None
    level_param, area_param = params
    level = trig.get(level_param)
    if level is None:
        return None
    area = trig.get(area_param) if area_param else None
    return node_key(level, area)


def _refines(current: str, candidate: str) -> bool:
    """candidate names the subarea of the same level current left unspecified."""
    return (":" in candidate and ":" not in current
            and candidate.partition(":")[0] == current)


def start_origin(start_triggers: list) -> str | None:
    """The world node a segment can START in, or None when its rules carry no
    place at all (reset_game, an unscoped key grab, a Toad star).

    MOST SPECIFIC WINS: LBLJ's `level_enter to=6` plus `attempt_anchor 6/1`
    resolves to the lobby, since the anchor knows the subarea and the level
    entry does not. If two clauses name genuinely DIFFERENT places, the FIRST
    one wins — no seeded definition does this, and a user-built one gets a
    stable answer plus an override in the editor if it guessed wrong.
    """
    origin = None
    for trig in start_triggers:
        candidate = _clause_origin(trig)
        if candidate is None:
            continue
        if origin is None or _refines(origin, candidate):
            origin = candidate
    # A subarea-less castle interior ("6", from `level_enter to=6` with no
    # to_subarea) is the LOBBY: every castle entry lands there before settling
    # elsewhere — the transient-lobby behaviour detectors/level.py journals and
    # area_changed's `from_transient` flags. Normalized HERE rather than at the
    # region lookup, because a node with a region but no PLACE in
    # origin_taxonomy renders its raw key as a group header (review I1).
    if origin == node_key(LEVEL_CASTLE_INSIDE):
        return node_key(LEVEL_CASTLE_INSIDE, AREA_LOBBY)
    return origin


def segment_origin(segment_id: int, start_triggers: list,
                   overrides: dict | None) -> str | None:
    """THE resolved origin of one definition: derived, unless the user has
    overridden it in the editor (the `origin_overrides` ui_state KV, keyed by
    segment id as a JSON object string).

    Every consumer goes through here — the library stamp (views.stamp_origins),
    the target rule (tracking/service), and target retirement
    (tracking/projection) — so a corrected origin cannot mean one place in the
    picker and another to the thing deciding what you may practice.
    """
    override = (overrides or {}).get(str(segment_id))
    return override if override else start_origin(start_triggers)


def hundred_coin_entity(start_triggers: list,
                       waypoints: list) -> tuple[int, int] | None:
    """(course_id, 6) when a definition's own sequence -- start_triggers or
    any waypoint's clause-set -- includes grabbing a main course's 100-coin
    star, else None (spec 2026-07-28-multi-step-segments, "the 100-coin star
    IS the segment"). THE resolver for "which entity does this definition's
    completed attempt belong to" -- projection.py reattributes a closed
    HUNDRED_COIN_EXIT-family attempt to this star (course_id/star_id,
    segment_id cleared) instead of the segment itself, and views.py uses the
    same answer to keep that family off the segment sections/segment_
    targets/picker union entirely: the star IS the practiced thing now, the
    segment is only its timing engine.

    Takes raw trigger LISTS, not a whole SegmentDef -- same convention as its
    neighbours `start_origin`/`segment_origin` above, and the reason: a raw
    `/api/segments` row (a dict, not a SegmentDef) needs the same answer for
    the picker's exclusion (views.stamp_origins), so this must not require
    constructing a dataclass a caller may not have.

    Same structural clause-search tracking/service.py::_hundred_coin_redirect
    used for the star->segment TARGET redirect this change retires, run in
    reverse (segment -> its star, not star -> its segment): identity, not
    ingredients, and deliberately NOT a category/seed_key lookup -- Task 20's
    HUNDRED_COIN_EXIT category and seed_key naming are corpus-authoring
    facts, and a def a user has reshaped, renamed, or built from scratch
    keeps matching by what it now DOES. Only star_id 6 counts (addresses.py's
    own "100 Coins is star 6 on every main course" rule) -- stars 0-5 are
    untouched by this family end to end."""
    clauses = list(start_triggers)
    for waypoint in waypoints:
        clauses.extend(waypoint)
    for clause in clauses:
        if clause.get("type") == "star_grabbed" and clause.get("star") == 6:
            return clause.get("course"), 6
    return None


def arms_ambiently(start_triggers: list) -> bool:
    """True when a definition arms merely by the player being present in a
    star-bearing stage -- entering it, or already standing in it via an
    `attempt_anchor` -- rather than by a deliberate action (leaving
    somewhere, grabbing a star, a menu reset). THE resolver for "does an
    armed instance of this def mean intent, or is it an ambient side
    effect of standing where the player already is" -- the property
    practice.js's pinned-card gate needs, and the property the retired
    `isAmbientlyArmed` (live report 2026-07-30) approximated by checking
    `category === "100 Coin Exit"`, which cannot see the OTHER two families
    sharing the identical shape (spec 2026-07-28-multi-step-segments): a
    Bowser stage's `seg:reds->pipe:<abbrev>` and the legacy exclusive
    `seg:<abbrev>-pipe` pipe-entry trio BOTH arm via the same
    `[level_enter, attempt_anchor]` idiom into their own course and BOTH
    exhibit the identical bug (confirmed by rendering: entering BitDW with
    nothing targeted pins "BitDW -- 8 Red Coins -> Pipe"), yet neither
    carries the "100 Coin Exit" category -- `seg:reds->pipe:*`'s own
    category is `Castle Movement`, same as an ordinary movement. Measured
    against the real bundled corpus rather than assumed: exactly 21 of 84
    seeded defs match (the 15 100-coin exits + 3 reds->pipe + 3 legacy
    pipe-entry), and NEITHER LBLJ (arms entering the CASTLE interior, not a
    course -- `course_for_level` answers None there) NOR any of the 56
    route-scoped movements (none starts on `level_enter`/`attempt_anchor`
    at all -- they start on `level_exit`/`star_grabbed`, per
    `tools/build_defaults_seed.py::_movement_row`) NOR the Bowser fights
    (arm the same way, but auto-select on entry BY DESIGN -- stagebanner.js's
    ArenaRow -- so an ambient pin is not a bug there, it is the point) is
    flagged. The 100-coin family no longer HAS a segment section to gate
    (views.py excludes it entirely -- its star section needs no such flag),
    so this predicate is only ever True on a SECTION for the remaining six."""
    for clause in start_triggers:
        kind = clause.get("type")
        if kind == "level_enter":
            level = clause.get("to")
        elif kind == "attempt_anchor":
            level = clause.get("level")
        else:
            continue
        if level is not None and COURSE_BY_LEVEL.get(level) is not None:
            return True
    return False


def origin_view(node: str | None) -> dict:
    """{key, label, region, region_label} for one origin node — the shape the
    API stamps on a segment row and the UI groups by. None = "Anywhere"."""
    if node is None:
        return {"key": None, "label": ANYWHERE_LABEL,
                "region": None, "region_label": ANYWHERE_LABEL}
    region = region_for_node(node)
    return {"key": node, "label": node_label(node), "region": region,
            "region_label": node_label(region) if region else ANYWHERE_LABEL}


def _place_sort_key(node: str, region: str) -> tuple:
    """Class before id, inside a region (user decision, spec §2): the region's
    own in-area starts, then its Bowser stage and arena, then its secret
    stages, then the main courses. Course id IS gameflow order for the last
    two; level id puts a Bowser course above its arena."""
    if node == region:
        return (0, 0)
    level = int(node.partition(":")[0])
    if level in BOWSER_STAGE_LEVELS:
        return (1, level)
    course = COURSE_BY_LEVEL.get(level)
    if course is None:
        # Defensive default, not a real case today: every node without a
        # COURSE_BY_LEVEL entry is either a Bowser stage (caught above) or
        # the region itself (caught by node == region), so this class is
        # currently unreachable (review M14).
        return (4, level)
    return (2, course) if course >= 19 else (3, course)


def origin_taxonomy() -> list[dict]:
    """The ordered region -> place tree, shipped in vocab() and rendered by
    the library (grouping) and the editor (the override picker).

    Shape is deliberately domain-free — {key, label, children:[{key, label}]} —
    so the categorized picker modal can serve courses/stars the same way and
    reuse the same renderer.
    """
    places: dict[str, list[str]] = {node_key(level, area): []
                                    for level, area in CASTLE_REGION_NODES}
    for node, region in world_regions().items():
        places[region].append(node)
    taxonomy = []
    for level, area in CASTLE_REGION_NODES:
        region = node_key(level, area)
        children = sorted(places[region],
                          key=lambda node: _place_sort_key(node, region))
        taxonomy.append({
            "key": region, "label": node_label(region),
            "children": [
                {"key": node,
                 "label": (f"{node_label(region)} (in-area starts)"
                           if node == region else node_label(node))}
                for node in children]})
    taxonomy.append({"key": None, "label": ANYWHERE_LABEL, "children": []})
    return taxonomy


OTHER_GROUP_LABEL = "Other"


def level_groups() -> list[dict]:
    """Levels grouped by castle region, in the taxonomy's order — so the
    builder's level dropdown reads like the library reads (user request
    2026-07-25: a filtered dropdown should still be categorized).

    Every level appears EXACTLY ONCE. The castle interior has a node in three
    regions (`6:1`/`6:2`/`6:3`), so it takes the first in gameflow order — the
    lobby, which is the same answer `region_for_node` gives a bare `"6"`.
    Anything the topology does not place lands in a trailing Other group rather
    than vanishing from the picker.
    """
    seen: set[int] = set()
    groups: list[dict] = []
    for region in origin_taxonomy():
        if region["key"] is None:
            continue
        levels = []
        for place in region["children"]:
            level = int(place["key"].partition(":")[0])
            if level in seen:
                continue
            seen.add(level)
            levels.append(level)
        if levels:
            groups.append({"key": region["key"], "label": region["label"],
                           "levels": levels})
    leftovers = [level for level in sorted(LEVEL_NAMES) if level not in seen]
    if leftovers:
        groups.append({"key": None, "label": OTHER_GROUP_LABEL,
                       "levels": leftovers})
    return groups


def course_groups() -> list[dict]:
    """The same grouping projected onto COURSE ids, for the course dropdown.

    A course is grouped by the region of its level. Course 0 (the castle
    secret stars) has no level of its own, so it lands in Other — the same
    honesty the "Anywhere" origin group shows.
    """
    groups: list[dict] = []
    grouped: set[int] = set()
    for group in level_groups():
        if group["key"] is None:
            continue
        courses = []
        for level in group["levels"]:
            course = COURSE_BY_LEVEL.get(level)
            if course is None or course in grouped:
                continue
            grouped.add(course)
            courses.append(course)
        if courses:
            groups.append({"key": group["key"], "label": group["label"],
                           "courses": courses})
    leftovers = [course for course in sorted(COURSE_NAMES)
                 if course not in grouped]
    if leftovers:
        groups.append({"key": None, "label": OTHER_GROUP_LABEL,
                       "courses": leftovers})
    return groups


@dataclass(frozen=True)
class GuardType:
    key: str
    label: str
    params: dict
    template: str
    check: Callable[[dict, MatchContext], bool]
    # "arm" gates arming (checked in the engine's arm phase, re-evaluated on
    # every arm/re-arm); "close" rows are DECLARATIVE result filters — never
    # checked here, read by projection's validity-bounds stamp (spec
    # 2026-07-23). Their check is a stub so a stray call can't block arming.
    phase: str = "arm"


GUARDS: dict[str, GuardType] = {g.key: g for g in [
    GuardType("prev_level", "Previous level was",
              {"level": {"kind": "level", "required": True}},
              "{level}",
              lambda p, ctx: ctx.prev_level == p["level"]),
    # Negated companion (user request 2026-07-23): "arm here, but NOT when the
    # player just came from level X" — an LBLJ anchor in the castle lobby must
    # not arm on the practice_reset that follows a Bowser-in-the-Dark-World
    # exit.  Unknown history (prev_level None) PASSES: this guard exists to
    # block a KNOWN source, and failing closed would kill the first arm of
    # every session.  Deliberately the opposite of prev_level / last_star_*,
    # which assert something POSITIVE about history and so must fail closed.
    GuardType("prev_level_not", "Previous level was NOT",
              {"level": {"kind": "level", "required": True}},
              "{level}",
              lambda p, ctx: ctx.prev_level != p["level"]),
    GuardType("star_count_min", "Star count at least",
              {"n": {"kind": "int", "required": True}},
              "{n}",
              # historical events without num_stars conservatively FAIL
              lambda p, ctx: ctx.num_stars is not None
              and ctx.num_stars >= p["n"]),
    GuardType("star_count_max", "Star count at most",
              {"n": {"kind": "int", "required": True}},
              "{n}",
              lambda p, ctx: ctx.num_stars is not None
              and ctx.num_stars <= p["n"]),
    # Close-phase validity bounds (spec 2026-07-23): storage + builder UI for
    # a segment's min/max completion time. `frames` is an INT of game frames
    # (30 fps); the builder edits it in seconds (ParamInput kind "seconds").
    # frames: 0 on min_time = "no minimum" (deliberately below the implicit
    # 0.5 s default — projection.DEFAULT_MIN_FRAMES applies when absent).
    GuardType("min_time", "Takes at least",
              {"frames": {"kind": "seconds", "required": True}},
              "{frames}",
              lambda p, ctx: True, phase="close"),
    GuardType("max_time", "Takes at most",
              {"frames": {"kind": "seconds", "required": True}},
              "{frames}",
              lambda p, ctx: True, phase="close"),
    # Arm-time history gates (spec 2026-07-23): "only arm this segment when
    # the player just came from star X" — e.g. a basement segment that only
    # makes sense right after Watch for Rolling Rocks. star None = any star
    # of the course. Unknown history (None) conservatively fails.
    GuardType("last_star_grabbed", "Last star grabbed was",
              {"course": {"kind": "course", "required": True},
               "star": {"kind": "star", "required": False}},
              "{course}, star {star}",
              lambda p, ctx: ctx.last_star_grabbed is not None
              and ctx.last_star_grabbed[0] == p["course"]
              and (p.get("star") is None
                   or ctx.last_star_grabbed[1] == p["star"])),
    GuardType("last_star_attempted", "Last star attempted was",
              {"course": {"kind": "course", "required": True},
               "star": {"kind": "star", "required": False}},
              "{course}, star {star}",
              lambda p, ctx: ctx.last_star_attempted is not None
              and ctx.last_star_attempted[0] == p["course"]
              and (p.get("star") is None
                   or ctx.last_star_attempted[1] == p["star"])),
    # Arm-gate scoping (spec 2026-07-23-default-routes-foundation): a stub-check
    # guard READ DECLARATIVELY by the engine's arm gate (see the module-level
    # _route_allows), exactly as min_time/max_time are read declaratively by
    # projection — the standard check() never gates arming (it can't see the
    # def id). A def carrying this arms only inside the
    # active route or as the standalone segment target. Opt-in: the 10 existing
    # defs omit it and are unaffected.
    GuardType("in_active_route", "Only in the active route",
              {}, "", lambda p, ctx: True, phase="arm"),
]}

# How forgiving an armed definition is (spec 2026-07-28-multi-step-segments).
# ONE registry, same role TRIGGERS/GUARDS play: it drives validate_definition,
# the editor control through vocab(), AND `SegmentEngine.feed`'s armed-branch
# dispatch — `_feed_strict` / `_feed_waypoint` / `_feed_loose`, selected by
# this key. A third mode ("exclusive", below) turned out to be one row here
# plus one GATED BRANCH inside an existing handler, not a new function —
# `_feed_strict` already ran unconditionally for any non-loose, waypoint-free
# def, so "exclusive" reaches it through the same `else` in feed()'s dispatch
# with zero dispatch-table changes; only a def that ALSO carries waypoints
# needed a look, and `_feed_waypoint` already cancels on a star/key grab as
# part of its own design, so that combination needed nothing either. A fourth
# mode may not be this cheap — check whether the shared handler it would ride
# already does what's needed before writing a new one.
#
# This comment said "does not yet change any matching behaviour" until
# 2026-07-29, describing the one task in the spec where that was true. By then
# loose matching WAS the branch's headline feature and 74 of 84 seeded
# definitions shipped with it. Five tasks and two controller commits touched
# this file after the dispatch landed and none of them corrected the sentence,
# which is how a future session reads live machinery as inert plumbing and
# deletes it.
MATCH_MODES = {
    "loose": {
        "key": "loose",
        "label": "Loose — ends only where I said",
        "description": ("Stays armed through star grabs, key grabs and level "
                        "changes until the end trigger fires. Use this for "
                        "anything that crosses courses or takes several "
                        "steps."),
    },
    "strict": {
        "key": "strict",
        "label": "Strict — cancels if I go off-route",
        # The old text said "use this when a stray star grab means the attempt
        # is over", which was never true of a plain two-point strict def --
        # `_feed_strict` has no star/key branch at all, so it stays armed
        # through one (pinned by test_strict_survives_a_star_grab_that_would_
        # cancel_an_exclusive_def). It IS true of a strict def carrying
        # waypoints, which runs `_feed_waypoint` and cancels on a major action.
        # Harmless prose until 2026-07-29, actively misleading after: it
        # described Exclusive's whole purpose while sitting on Strict, in a
        # control the user reads to choose between the two.
        "description": ("Cancels the moment you go off-route — a level change "
                        "that is not the next expected step, or leaving the "
                        "area it armed in. A multi-step segment also cancels "
                        "on a stray star or key grab; a plain two-point one "
                        "stays armed through those (pick Exclusive if it "
                        "should not)."),
    },
    "exclusive": {
        "key": "exclusive",
        "label": "Exclusive — cancels if I grab a star or key",
        "description": ("Behaves exactly like Strict, but also cancels the "
                        "instant I grab a star or Bowser key that isn't this "
                        "segment's own end trigger — dying or leaving the "
                        "route still ends the attempt the same way Strict "
                        "does. Use this for a segment that only counts if "
                        "grabbing something else along the way means you "
                        "weren't really doing it — like entering a Bowser "
                        "pipe without going for its 8-red-coin star."),
    },
}


def _check_clause(clause: dict, registry: dict, what: str) -> None:
    if not isinstance(clause, dict):
        raise ValueError(f"each clause in {what} must be a dict,"
                         f" got {type(clause).__name__!r}")
    kind = clause.get("type")
    if kind not in registry:
        raise ValueError(f"unknown trigger type {kind!r} in {what}"
                         if registry is TRIGGERS
                         else f"unknown guard type {kind!r} in {what}")
    spec = registry[kind]
    for name, meta in spec.params.items():
        if meta["required"] and clause.get(name) is None:
            raise ValueError(f"{kind}: missing required param {name!r}")
        if clause.get(name) is None:
            continue
        # Every param in this registry was an integer id until 2026-08-05, so
        # this check simply demanded one. A MOMENT kind is a name out of
        # detectors/moment.py's registry instead, so the check dispatches on
        # the param's own declared kind. Deliberately narrow: one declared
        # kind is exempted, and a string level is still the mistake it always
        # was (pinned by test_moment_trigger.py).
        if meta["kind"] == "moment":
            if clause[name] not in _MOMENT_KINDS:
                raise ValueError(
                    f"{kind}: unknown moment {clause[name]!r} — known moments "
                    f"are {sorted(_MOMENT_KINDS)}")
        elif meta["kind"] == "landmark":
            # A catalogue key, written by the recorder (round 12 item 3) —
            # a string, never an id. Content is not validated against the
            # catalogue: an unnamed landmark is a legal pin (the key is the
            # identity; the name is display), and a key for a thing never
            # touched again simply never matches.
            if not isinstance(clause[name], str) or not clause[name].strip():
                raise ValueError(
                    f"{kind}: param {name!r} must be a landmark key string")
        elif not isinstance(clause[name], int):
            raise ValueError(f"{kind}: param {name!r} must be an integer")
    extras = set(clause) - {"type"} - set(spec.params)
    if extras:
        raise ValueError(f"{kind}: unknown params {sorted(extras)}")
    # Impossible-by-construction clauses fail LOUDLY instead of silently
    # never matching (live report 2026-07-23: "enter Castle Inside coming
    # from Castle Inside" was saved, but a within-level move never fires
    # level_changed — only area_changed does).
    if kind in ("level_enter", "level_exit") \
            and clause.get("to") is not None \
            and clause.get("to") == clause.get("from"):
        raise ValueError(
            f"{kind}: 'from' and 'to' are the same level — movement inside "
            "a level never fires a level change; use \"You enter area\" "
            "with 'coming from' instead")
    if kind == "area_enter" and clause.get("area") is not None \
            and clause.get("area") == clause.get("from"):
        raise ValueError("area_enter: 'coming from' and the destination are "
                         "the same subarea — an area crossing always "
                         "changes the area")


def time_bounds(guards: list) -> tuple[int | None, int | None]:
    """(min_frames, max_frames) declared by a def's close-phase time guards,
    None where absent. Later rows win (the chip editor writes at most one of
    each). THE reader for projection's segment validity bounds — keep the
    guard row shape knowledge here, not in projection."""
    lo = hi = None
    for g in guards or []:
        if g.get("type") == "min_time":
            lo = g["frames"]
        elif g.get("type") == "max_time":
            hi = g["frames"]
    return lo, hi


def _route_allows(d, ctx) -> bool:
    """in_active_route gate, read declaratively by the arm phase (the
    standard guard check() can't see the def id — see the guard's own
    comment). Unguarded defs always pass; a guarded def arms inside the
    active route's member set or as the standalone segment target.

    **NO ACTIVE ROUTE MEANS NO RESTRICTION, NOT "NOTHING ARMS"** (Griffin,
    2026-08-02, live report): *"if we're in 'Overall' mode, I would expect to
    see EVERY SINGLE OPTION enabled. That is, I can practice ANYTHING. That
    would be the point... so long as it arms legitimately"*. This gate read an
    empty scope as "no member set, so nobody is a member" until then, which
    made all 56 castle movements silently unpracticable whenever the header's
    scope chip sat on Overall — and nothing on any surface said so, so the
    feature looked broken rather than off. Picking a route is a deliberate
    NARROWING and still narrows; picking none is the absence of a filter.

    The consequence is intended, not overlooked: with no route, one
    `level_exit from=24` arms all seven `WF → X` movements at once. Nothing has
    to guess between them — `armed_segment_ids` is a set, every wrong one is
    cancelled by the topological rules or expires on the staleness budget, and
    whichever end trigger fires is the movement he actually ran."""
    if not any(g.get("type") == "in_active_route" for g in d.guards):
        return True
    scope = ctx.route_segments or frozenset()
    if not scope:
        return True
    return d.id in scope or d.id == ctx.target_segment


def validate_definition(d: dict) -> None:
    """Raises ValueError listing the first problem (API maps it to 409)."""
    if not str(d.get("name", "")).strip():
        raise ValueError("name is required")
    for side in ("start_triggers", "end_triggers"):
        clauses = d.get(side) or []
        if not isinstance(clauses, list):
            raise ValueError(f"{side} must be a list")
        if not clauses:
            raise ValueError(f"{side} needs at least one trigger")
        for c in clauses:
            _check_clause(c, TRIGGERS, side)
    waypoints = d.get("waypoints") or []
    if not isinstance(waypoints, list):
        raise ValueError("waypoints must be a list")
    for step in waypoints:
        if not isinstance(step, list) or not step:
            raise ValueError("each waypoint must be a non-empty list of triggers")
        for clause in step:
            _check_clause(clause, TRIGGERS, "waypoints")
    default_strat = d.get("default_strat")
    if default_strat is not None and (not isinstance(default_strat, str)
                                      or not default_strat.strip()):
        # An empty/blank default is worse than none: it would read as "no
        # strategy" everywhere while still suppressing the blank option in
        # the picker, leaving no way to express either.
        raise ValueError("default_strat must be a non-empty string or absent")
    parent = d.get("parent")
    if parent is not None and (not isinstance(parent, str)
                               or not _PARENT_KEY.match(parent)):
        raise ValueError(
            "parent must be a star, segment or castle-area key like "
            f"'star:2:1', 'segment:7' or 'area:6:1', got {parent!r}")
    mode = d.get("match_mode", "strict")
    if mode not in MATCH_MODES:
        raise ValueError(
            f"unknown match_mode {mode!r}; expected one of "
            f"{sorted(MATCH_MODES)}")
    guards = d.get("guards") or []
    if not isinstance(guards, list):
        raise ValueError("guards must be a list")
    for g in guards:
        _check_clause(g, GUARDS, "guards")
    # Cross-check the resolved time-guard bounds (post-review 2026-07-23):
    # _check_clause only confirmed `frames` is an int, so a segment's
    # min_time/max_time guard rows carried NO range/relation validation —
    # unlike the star-side set_time_filter (service.py), which 409s on the
    # same shape of bad input. The shared chip editor serves both kinds, so
    # a user action that gets rejected for a star silently poisoned a
    # segment's history instead (every success flagged auto-cleared).
    # Wording mirrors set_time_filter's ValueErrors for consistency.
    lo, hi = time_bounds(guards)
    if lo is not None and lo < 0:
        raise ValueError("min_time frames must be >= 0")
    if hi is not None and hi < 1:
        raise ValueError("max_time frames must be >= 1")
    if lo is not None and hi is not None and hi <= lo:
        raise ValueError("max_time must exceed min_time")


def vocab() -> dict:
    """Registry serialized for the builder GUI — the UI renders from this."""
    return {
        "triggers": [{"key": t.key, "label": t.label, "params": t.params,
                      "template": t.template} for t in TRIGGERS.values()],
        "guards": [{"key": g.key, "label": g.label, "params": g.params,
                    "template": g.template, "phase": g.phase}
                   for g in GUARDS.values()],
        # Ordered for the editor control (spec 2026-07-28-multi-step-segments):
        # loose first — it is the default and the one we want read first, and
        # a new definition in the builder seeds match_mode from THIS list's
        # position 0 (ui/components/segments.js), not from any dict order —
        # exclusive is appended last, deliberately, rather than inserted
        # before strict: it's strict PLUS one more cancel rule (a star/key
        # grab), the most specialized of the three, so it reads last and
        # position 0 stays loose.
        "match_modes": [MATCH_MODES["loose"], MATCH_MODES["strict"],
                        MATCH_MODES["exclusive"]],
        # The moment vocabulary a `moment_reached` clause's `kind` selects
        # from. Served rather than hard-coded in the builder for the same
        # reason levels and courses are: detectors/moment.py owns the list,
        # and a new moment must reach the dropdown without a JS edit.
        "moments": [{"key": m.kind, "label": m.label} for m in MOMENTS],
        "levels": {str(k): v for k, v in sorted(LEVEL_NAMES.items())},
        "castle_areas": {str(k): v for k, v in CASTLE_AREA_NAMES.items()},
        "courses": {str(k): v for k, v in COURSE_NAMES.items()},
        # star_id order; star_count/star_name (addresses.py) own the
        # 100-coin-star rule for courses 1-15
        "stars": {str(cid): [star_name(cid, s)
                             for s in range(star_count(cid))]
                  for cid in COURSE_NAMES},
        # world-topology successor map ("6:1"/"22" node -> [level, area|None]
        # destinations) — the builder filters flow-annotated level/subarea
        # dropdowns to world-possible moves (addresses.WORLD_EDGES_*)
        "connections": world_connections(),
        # Ordered region -> place tree for the segment library's grouping and
        # the editor's origin override (spec 2026-07-24-segment-origin-
        # categories). Domain-free shape: {key, label, children:[...]}.
        "origins": origin_taxonomy(),
        # The SAME grouping, projected onto the id spaces the builder's
        # dropdowns actually select from, so a level or course picker reads
        # like the library reads (user request 2026-07-25). Shipped rather than
        # derived in JS: the taxonomy has one home.
        "level_groups": level_groups(),
        "course_groups": course_groups(),
        # Level -> course, so the UI's icon chain can find a level's art
        # without duplicating COURSE_BY_LEVEL in JS. String keys: JSON object
        # keys are strings, and the client indexes with String(level).
        "course_by_level": {str(level): course
                            for level, course in COURSE_BY_LEVEL.items()},
    }


# --- Split & merge: two pure, non-destructive authoring operations --------
# (spec 2026-07-28-multi-step-segments, Task 17; user ask: "in the editor,
# it should really just be broken down into the two splits we would expect
# here... I guess this is a valid option and way to do it and should be
# supported (any combination like this)" -- WF -> SSL should be expressible
# either as one definition or as WF -> Basement + Basement -> SSL.)
#
# Both return plain dicts shaped for tracking/service.py's create_segment
# (a later task wires the API/UI on top of these; nothing here touches a db
# row or a route). NEITHER mutates its inputs, and NEITHER is destructive:
# the original passed to split_definition keeps existing, unedited --
# definitions arm in PARALLEL (SegmentEngine.feed loops every enabled def
# independently, each with its own _Arm), so a whole movement and its two
# halves can all be armed on the same play and all record their own
# attempt. That is the direct answer to "either one split or a series of
# steps": both, at once, with nothing to migrate and no history orphaned by
# the edit.
def split_definition(d: SegmentDef, mid: list[dict],
                     names: tuple[str, str]) -> tuple[dict, dict]:
    """Break `d` into two chained definitions meeting at `mid` -- an any-of
    clause-set the caller supplies as the shared boundary (typically one of
    `d.waypoints`' own steps, promoted to a full stop; e.g. splitting WF ->
    SSL at its one waypoint, "enter the Castle Inside basement", produces
    WF -> Basement and Basement -> SSL).

        first  = d.start_triggers -> mid
        second = mid              -> d.end_triggers

    Both halves ship `waypoints=[]`: every waypoint-bearing definition
    shipped today carries exactly ONE waypoint, which IS the split point, so
    consuming it into the new shared boundary is the whole operation (the
    seeded corpus is 83 defs with none and 1 with one).

    A def carrying SEVERAL waypoints, split at just one of them, is refused
    (`ValueError`) rather than served -- generalizing it now would be
    guessing which side each survivor belongs on, but returning halves with
    the others quietly missing is silent data loss, and nothing caps the
    count: `validate_definition` accepts any-length waypoint lists, so a
    user-authored definition really can reach here. Refusing keeps the YAGNI
    without making the caller pay for it in lost clauses.

    `match_mode` is INHERITED from `d` for both halves, not forced to
    "loose": flattening away the split's one waypoint says nothing by
    itself about how tolerant either half should be of an off-route event --
    a strict `d` split into two plain 2-endpoint defs just runs
    SegmentEngine._feed_strict for each half, exactly the handler a plain
    strict def with no waypoints has always run, so this is a shape
    degradation, not a silent semantics change the author never asked for.

    Neither half carries a `seed_key` -- there is nothing TO inherit
    (`SegmentDef` itself has no such field; only the raw db row dict does),
    which is also the right answer: a definition derived from a seeded one
    is not that seeded row, and a `seed_key` surviving into it would make
    `reconcile_defaults` overwrite it at the next startup
    (tracking/defaults.py).

    guards/default_strat/enabled are inherited unchanged onto both halves --
    a time guard on the original describes the WHOLE movement's duration and
    so is now looser than either half strictly needs, but never wrong in a
    way that could reject a valid completion (a half's rta can only be
    SHORTER than the whole's), unlike merge_definitions below, where keeping
    either input's bound verbatim would misrepresent the combined span in
    the harmful direction (see its own docstring).

    Refuses (`ValueError`) rather than warns when a produced half comes out
    UNFIREABLE -- reusing `tracking.lint.lint_definition`'s own "unfireable"
    rule (never reimplementing the world-topology check it owns) rather than
    lint's advisory RUNTIME posture. A saved definition must keep matching
    whatever a Usamune warp menu invents forever, which is why a lint
    finding never blocks a MATCH -- but this runs at author time, before
    anything is saved, "unfireable" is lint's own "error" severity ("the
    definition CANNOT work"), and merge_definitions below takes the same
    posture for a pair that doesn't meet: a pure constructor should not hand
    back data it already knows is dead on arrival. Checked on BOTH halves,
    not just the one the live report happened to name -- either side of an
    arbitrary split point can collide with its own next required step.
    """
    from sm64_events.tracking.lint import lint_definition  # cycle-free at
    # call time -- lint.py imports THIS module at its own top level, so the
    # reverse import must stay deferred to the function body (same trick
    # feed() uses for tracking.projection.Attempt, a few hundred lines down).

    if len(d.waypoints or []) > 1:
        raise ValueError(
            f"cannot split {d.name!r}: it carries {len(d.waypoints)} "
            "waypoints, and this operation folds ALL of them into the one "
            "shared boundary `mid` -- the rest would be dropped silently. "
            "Nothing caps the count (validate_definition accepts any-length "
            "waypoint lists), so a user-authored definition reaches here even "
            "though the seeded corpus is 83 defs with none and 1 with one. "
            "Refusing beats guessing which side each surviving waypoint "
            "belongs on; implement that when a real definition needs it.")

    mid_clauses = list(mid)
    first_name, second_name = names
    first = {
        "name": first_name,
        "enabled": d.enabled,
        "start_triggers": list(d.start_triggers),
        "end_triggers": list(mid_clauses),
        "waypoints": [],
        "guards": list(d.guards),
        "default_strat": d.default_strat,
        "match_mode": d.match_mode,
        "seed_key": None,
    }
    second = {
        "name": second_name,
        "enabled": d.enabled,
        "start_triggers": list(mid_clauses),
        "end_triggers": list(d.end_triggers),
        "waypoints": [],
        "guards": list(d.guards),
        "default_strat": d.default_strat,
        "match_mode": d.match_mode,
        "seed_key": None,
    }
    for half in (first, second):
        probe = SegmentDef(id=-1, name=half["name"], enabled=half["enabled"],
                           start_triggers=half["start_triggers"],
                           end_triggers=half["end_triggers"],
                           guards=half["guards"], waypoints=half["waypoints"],
                           match_mode=half["match_mode"])
        unfireable = [f for f in lint_definition(probe, [])
                     if f["rule"] == "unfireable"]
        if unfireable:
            raise ValueError(
                f"{half['name']!r} would be unfireable: "
                f"{unfireable[0]['message']}")
    return first, second


def _meeting_levels(triggers: list) -> set[int]:
    """The concrete arm-position levels a clause-set could land you in,
    reusing `start_levels`'s own `arm_level` derivation (never a second
    reading of the same param names) -- empty when every clause's arm
    position is unknowable (e.g. a course exit that omits `to`, which is
    most seeded `level_exit` clauses; see arm_level's own docstring). Shared
    by merge_definitions' "do these two definitions actually meet" check
    below; it is not itself a matcher concept, so it lives beside the
    authoring operation that needs it rather than inside SegmentEngine."""
    return set(start_levels(triggers))


def _pinned_subareas(triggers: list, level: int) -> set[int] | None:
    """The subareas this clause-set pins WITHIN `level`, or None when any
    clause reaching that level pins no subarea at all.

    None means "unknown", never "none of them" -- one area-less clause
    landing in the level is enough to make the subarea question unanswerable
    for the whole set, and an unknown position is permitted everywhere else
    in this module (can_run_from's own convention). Reuses `start_areas`'
    derivation clause by clause rather than reading `area`/`to_subarea`
    again, so a new subarea-bearing trigger type is one edit there."""
    pinned: set[int] = set()
    for trig in triggers:
        if arm_level(trig) != level:
            continue
        areas = [area for lv, area in start_areas([trig]) if lv == level]
        if not areas:
            return None
        pinned.update(areas)
    return pinned or None


def _subareas_can_meet(end_triggers: list, start_triggers: list,
                       level: int) -> bool:
    """Could a player finishing `end_triggers` be standing where
    `start_triggers` needs them, given both agree on `level`?

    The castle interior is ONE level (6) holding three subareas on a line
    (basement 3 <-> lobby 1 <-> upstairs 2), so a level-only answer accepts
    seams that do not exist: the shipped corpus has three definitions ending
    in the basement and one starting Upstairs, and merging any of those pairs
    passed a level-only check while the chain was broken."""
    ends = _pinned_subareas(end_triggers, level)
    starts = _pinned_subareas(start_triggers, level)
    if ends is None or starts is None:
        return True
    return bool(ends & starts)


def merge_definitions(first: SegmentDef, second: SegmentDef,
                      name: str) -> dict:
    """Chain two definitions into one spanning both, with `second`'s start
    clause-set kept as a WAYPOINT in the middle -- so the merged definition
    still requires the route to pass through the seam, not merely to begin
    at `first`'s start and end at `second`'s end (which would match a
    strictly WIDER set of play than either input did, e.g. a direct A->C
    warp that never touched B).

        merged.start_triggers = first.start_triggers
        merged.end_triggers   = second.end_triggers
        merged.waypoints      = first.waypoints + [second.start_triggers]
                                + second.waypoints

    Preserving each input's OWN waypoints (not just the new seam) is what
    keeps this the general inverse of split_definition: merging two
    definitions that are themselves already multi-step chains produces one
    definition visiting every one of their steps in order, not just the two
    that used to be split_definition's mid clause.

    Refuses (`ValueError`, message containing "do not meet") when `first`'s
    end and `second`'s start describe unrelated places -- reusing
    `start_levels`/`arm_level`'s own derivation (never a second reading of
    the same trigger param names) rather than inventing a fresh "where does
    this clause point" concept. An UNKNOWN arm position on either side
    (empty result -- most seeded `level_exit` clauses omit `to`) passes,
    matching the codebase-wide "unknown means yes" convention `can_run_from`
    already takes for the identical question at runtime; only a CONCRETE,
    non-overlapping pair is refused, e.g. `first` ending in the Castle
    Inside basement and `second` starting in the castle courtyard.

    The check runs at SUBAREA resolution too, not level alone. The castle
    interior is one level (6) holding three subareas on a line (basement 3
    <-> lobby 1 <-> Upstairs 2), so "same level" is not a seam there -- and
    the shipped corpus reaches that case by itself: three seeded definitions
    end at `area_enter(6, 3)` and one starts at `area_enter(6, 2)`, a pair a
    merge button would happily offer. Refused only when BOTH sides pin a
    subarea in every shared level and none coincide; one area-less clause
    reaching that level (`level_enter to=6` pins nothing) makes the question
    unanswerable and so permits it, same convention as above.

    `match_mode`: inherited when both inputs agree; when they DISAGREE,
    "loose" -- not "first wins", which would silently apply a stricter
    handler to the OTHER half's own steps than that half's own author chose,
    an accident just as real as forgetting the seam. "loose" is the mode
    built for "anything that crosses courses or takes several steps"
    (MATCH_MODES' own description) -- exactly the shape a merge always
    produces, no matter which input contributed which end.

    `default_strat`: inherited when both agree, else `None` -- a merged
    movement spanning two different practiced techniques has no single
    obvious default, and `None` ("no default") is already the codebase-wide
    meaning of "not decided", not a new state.

    `enabled`: both must be enabled, or the merge is not -- a merge should
    not silently resurrect a definition its author deliberately disabled.

    `guards=[]` -- deliberately dropped, not unioned. A `min_time`/`max_time`
    guard describes ITS OWN input's duration; concatenating them (or letting
    "later wins", `time_bounds`'s own rule, pick one) would apply a bound
    that is now too TIGHT for the combined span (unlike split_definition's
    inherited guards, which can only end up too LOOSE -- see its docstring
    for why that direction is harmless and this one is not). The merged
    whole's actual bounds are a question only the author can answer, not one
    this pure function should guess at.

    Never carries a `seed_key`, for the same reason as split_definition: a
    merged definition is a brand-new row, never either input, so it always
    gets `seed_key=None`.
    """
    first_end_levels = _meeting_levels(first.end_triggers)
    second_start_levels = _meeting_levels(second.start_triggers)
    if first_end_levels and second_start_levels \
            and not (first_end_levels & second_start_levels):
        raise ValueError(
            f"{first.name!r} and {second.name!r} do not meet: "
            f"{first.name!r} ends at level(s) {sorted(first_end_levels)}, "
            f"{second.name!r} starts at level(s) "
            f"{sorted(second_start_levels)}")
    shared_levels = first_end_levels & second_start_levels
    if shared_levels and not any(
            _subareas_can_meet(first.end_triggers, second.start_triggers,
                               level)
            for level in shared_levels):
        detail = "; ".join(
            f"level {level}: ends in subarea(s) "
            f"{sorted(_pinned_subareas(first.end_triggers, level) or [])}, "
            f"starts in "
            f"{sorted(_pinned_subareas(second.start_triggers, level) or [])}"
            for level in sorted(shared_levels))
        raise ValueError(
            f"{first.name!r} and {second.name!r} do not meet: they share "
            f"level(s) {sorted(shared_levels)} but no subarea within them "
            f"({detail}) -- the castle interior is one level holding "
            "basement, lobby and Upstairs, so sharing it is not a seam")
    match_mode = (first.match_mode if first.match_mode == second.match_mode
                 else "loose")
    default_strat = (first.default_strat
                     if first.default_strat == second.default_strat
                     else None)
    return {
        "name": name,
        "enabled": first.enabled and second.enabled,
        "start_triggers": list(first.start_triggers),
        "end_triggers": list(second.end_triggers),
        "waypoints": (list(first.waypoints) + [list(second.start_triggers)]
                     + list(second.waypoints)),
        "guards": [],
        "default_strat": default_strat,
        "match_mode": match_mode,
        "seed_key": None,
    }


@dataclass(frozen=True)
class _FrameOnly:
    """The clock with no event attached — what `SegmentEngine.settle` passes to
    `_flush_move`, whose only read is `.frame`. Deliberately NOT a real Event:
    nothing journaled this, nothing may broadcast it, and giving it a type and
    an empty payload keeps it honest if `_flush_move` ever reaches for more."""
    frame: int
    type: str = "frame_settled"
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _Arm:
    jid: int            # journal id of the arming event -> attempt id
    start_frame: int
    started_utc: str
    anchor_type: str    # the arming event's type
    session_id: int
    # MatchContext position when armed = the segment's start position.
    # level_changed arms record a stale ctx.area; the co-frame establishing
    # area_changed overwrites it (see feed). None = unknown (legacy
    # journals) — position checks treat None as a wildcard.
    level: int | None = None
    area: int | None = None
    # Set on a DEFERRED destination-subarea entry held in SegmentEngine._pending:
    # the required interior area. The entry's .area is re-pinned to the settling
    # co-frame area_changed; it arms iff area == required_area once the frame
    # advances. Always None on a live _armed entry (cleared when it resolves).
    required_area: int | None = None
    # Waypoint sequence position (spec 2026-07-23-default-routes-foundation):
    # index of the next d.waypoints[] step to match; == len(d.waypoints) means
    # every waypoint is consumed and the def is awaiting its end trigger. 0 for
    # every non-waypoint def (empty d.waypoints never reads this field).
    progress: int = 0
    # Cursor into `path_nodes(d)` — the next declared PLACE this run still owes
    # (spec 2026-08-02-strict-path-segments). Distinct from `progress`, which
    # indexes `d.waypoints` and counts steps of ANY trigger type: a star grab
    # advances progress and names no place at all. Advanced ONLY by
    # `_flush_move`, from the settled position, of which there is exactly one
    # per frame — which is why an index is safe here where `declared_nodes`
    # needed a set (see its docstring).
    path_index: int = 0
    # Frame at which a LOOSE arm is presumed abandoned (spec
    # 2026-07-28-multi-step-segments). None for a strict arm, which is bounded
    # by its cancel rules instead. Shipped to the view so the UI reads expiry
    # from the SAME number the matcher does — the engine only notices on the
    # next event, and a card must not keep saying "Running" until one arrives.
    deadline_frame: int | None = None


def _at_arm_position(arm: _Arm, ctx: MatchContext) -> bool:
    """True when the tracked position matches where the segment armed.
    None on either side = unknown → match, so legacy journals (no
    level/area events) keep the unconditional continuation behavior."""
    return ((arm.level is None or ctx.level is None or ctx.level == arm.level)
            and (arm.area is None or ctx.area is None or ctx.area == arm.area))


_MAJOR_EVENT_TYPES = ("star_collected", "key_grabbed")


def _is_major_action(ev) -> bool:
    """Off-sequence events that CANCEL a waypoint segment (spec
    2026-07-23-default-routes-foundation): a task switch (grabbing a star or
    key) or a misroute (a real level crossing that isn't the next waypoint).
    A minor event (area_changed, warp_entered, spawned) stays transparent —
    only these two shapes are treated as "the player left the route"."""
    return (ev.type in _MAJOR_EVENT_TYPES
            or (ev.type == "level_changed" and _real_edge(ev)))


# How far a zeroing event may sit from an arm and still describe the SAME
# load. One, measured: a savestate reload emits `spawned` then `practice_reset`
# on consecutive frames (31 of 31 of his door runs), and the two are one event
# as far as Usamune's counter is concerned.
IGT_ARM_SKEW_FRAMES = 1


def _zeroes_usamune_igt(ev) -> bool:
    """Did this event put Usamune's overall IGT counter back to zero?

    Usamune resets it on every level load, every area load, every practice
    reset / savestate load, and every console reset — which is the whole
    reason the anchor detector exists and the whole reason the load-echo
    shapes in this module's docstring exist. `SegmentEngine._last_igt_zero_
    frame` is that frame, and `_close` compares it against the arm to decide
    whether a closing event's `igt_frames` measures THIS segment (see there).

    Deliberately blind to whether the anchor was an ECHO: a door crossing is
    invisible to the matcher because the player did not choose it, but
    Usamune zeroed its counter all the same, and the counter is what this
    answers about. `spawned` is absent because it is not itself a reset — the
    load or anchor that produced it already fired, on its own frame."""
    if ev.type in _ANCHOR_TYPES or ev.type == "game_reset":
        return True
    return ev.type in ("level_changed", "area_changed") and _real_edge(ev)


class SegmentEngine:
    """One IDLE<->ARMED FSM per enabled definition. Pure over journal
    events + MatchContext: same code path live and in replay."""

    def __init__(self, defs: list[SegmentDef]):
        self._defs = [d for d in defs if d.enabled]
        self._def_by_id = {d.id: d for d in self._defs}
        self._armed: dict[int, _Arm] = {}
        # Deferred destination-subarea entries (see _Arm.required_area): a
        # level edge into Castle Inside matched the level+from, but the
        # destination interior area only settles a poll later (the lobby loads
        # first). These hold until the frame advances, then arm iff the settled
        # area matches. Kept OUT of _armed so the closure/echo logic never sees
        # an unconfirmed entry. Live report 2026-06-13.
        self._pending: dict[int, _Arm] = {}
        # Updated to ev.frame on every level_changed / area_changed BEFORE the
        # per-def loop.  Transition events always journal before their same-tick
        # synthetic practice_reset (detector order in main.py guarantees it),
        # so this is always set when the echo arrives.
        self._last_transition_frame: int | None = None
        # Frame of the last REAL-EDGE area_changed (Mario crossed into a new
        # castle area). A co-frame anchor is then a cross-area RELOCATION (warp
        # landing), which may arm an IDLE destination segment even when its
        # spawn action looks like a door echo (live report 2026-06-13: warping
        # to the lobby lands in ACT_WARP_DOOR_SPAWN, so the attempt_anchor reset
        # was door-echo-suppressed and LBLJ never re-armed).
        self._last_area_edge_frame: int | None = None
        # Frame on which Usamune's overall IGT counter was last put back to
        # zero (_zeroes_usamune_igt). THE precondition for reading a closing
        # event's own igt_frames as the segment's time — see _close. None
        # until the first such event, which conservatively means "no segment
        # may claim an IGT basis yet"; reset at a session boundary, since
        # global_timer restarts there and a stale frame number could otherwise
        # collide with a fresh arm's.
        self._last_igt_zero_frame: int | None = None
        # Best successful rta per definition, as seen SO FAR in this feed
        # (spec 2026-07-28-multi-step-segments). Deterministic under replay
        # (same journal -> same answer) and monotonically improving, which is
        # what makes budget_frames stable. A MINIMUM, so an implausibly slow
        # success can never inflate a loose def's budget; only an implausibly
        # fast one could shrink it, and MIN_BUDGET_FRAMES is the floor for
        # exactly that (the projector may later auto-clear an out-of-range
        # success the engine counted here — harmless for the same reason).
        self._best_success: dict[int, int] = {}
        # THE topological rules' view of where Mario is (spec
        # 2026-08-01-topological-segment-validity). `_settled_node` is the last
        # node he demonstrably DWELT in; `_pending_move` is (frame, node) — the
        # candidate this frame proposes, judged only once the frame ADVANCES.
        #
        # The defer is not caution, it is required. Every castle entry loads
        # the lobby for one poll before warping to the real area, all on ONE
        # game frame (detectors/level.py), so a judgement taken on the raw
        # event sees "SSL -> Lobby" (not an edge at all — SSL's only neighbour
        # is the basement) and then "Lobby -> Basement" (a hop AWAY from any
        # upstairs destination), for a move that never happened. Taking the
        # LAST candidate of a frame is the same per-frame collapse the design's
        # own measurement used, so the number that justified this feature and
        # the code that implements it cannot drift apart. Same one-frame shape
        # `_pending` above already uses for deferred destination subareas.
        self._settled_node: str | None = None
        self._pending_move: tuple[int, str | None] | None = None
        # Definitions the topological rules cancelled, as {def id: (_Arm,
        # expiry frame)} — the arm as it stood when it was killed, so a real
        # anchor AT THAT POSITION can bring it back (Griffin's ruling
        # 2026-08-01, from the measurement in tools/measure_topology_cancels.py).
        #
        # The case: armed by a Bowser 1 exit into the lobby, he warped to BitDW
        # for 7 s, came back to the lobby, pressed reset AT THE ARM POSITION and
        # ran lobby -> WF in 16 s (journal ids 17926-17940). `_feed_loose`
        # already treats an anchor at the arm position as a genuine retry, and
        # that IS how a castle movement is re-run — redoing the `level_exit
        # from=30` start trigger means redoing the whole fight. Without this the
        # rules would take that retry loop away.
        #
        # EXPIRY is not optional: a cancelled arm has no cancel rules left to
        # bound it, so without a clock a movement killed hours ago would re-arm
        # the next time he happened to reset in the same room — the ambient-
        # arming class of bug. It gets the same measured staleness budget a
        # loose arm gets (budget_frames), applied to every mode for that reason.
        self._cancelled: dict[int, tuple] = {}

    def armed_ids(self) -> set[int]:
        return set(self._armed)

    def armed_items(self) -> dict[int, _Arm]:
        """Currently-armed defs with their live `_Arm` (spec 2026-07-28-
        multi-step-segments): a COPY, like armed_ids() — a caller must never
        be able to mutate engine-private state through it. The projector's
        armed_arms() is the one consumer, for the view's progress/deadline
        detail."""
        return dict(self._armed)

    def definition(self, sid: int) -> SegmentDef | None:
        """The def for an id, or None (a deleted or never-loaded definition —
        callers must not assume every armed/pending id still has one)."""
        return self._def_by_id.get(sid)

    def settle(self, frame: int) -> list[dict]:
        """Judge a pending position change on the CLOCK, with no event to carry
        it, and return the notices that came out (live report 2026-08-02).

        `_flush_move` defers a verdict by one frame on purpose, but until this
        existed a frame only advanced when the journal happened to get another
        event — so inside a course where nothing is journaled the verdict waited
        for whatever the player did next. Measured on his own session: entering
        Bowser in the Sky from Upstairs cancelled `Bowser 2 → WDW` correctly and
        the screen kept offering it for **832 frames (27.7 s)**, which reads as
        a missing rule rather than a late one. Earlier sightings were 96, 116
        and 56 frames.

        A liveness fix, not a correctness one: the verdict is identical either
        way, and a topological cancel writes no attempt row, so a REPLAY (which
        has only events, and therefore settles at the next one) reaches the same
        state. The only difference a replay can see is the resurrection entry's
        expiry frame, off by however long the wait was.
        """
        notices: list[dict] = []
        self._flush_move(_FrameOnly(frame), notices)
        return notices

    def feed(self, ev, ctx: MatchContext):
        """Returns (closed raw Attempts, notices). Closures before arming."""
        from sm64_events.tracking.projection import Attempt  # cycle-free at call time
        closed, notices = [], []
        # Where every armed def stood BEFORE this event, so the one place at
        # the bottom of this method can tell the browser about any step that
        # moved. See `_progress_notices` for why it is a diff and not an
        # append at each of the four sites that can move a cursor.
        progress_before = {sid: arm.progress for sid, arm in self._armed.items()}
        # Drop spent deferred destination-subarea entries (_pending): once an
        # event at a LATER frame arrives, the entry frame's co-frame area_changed
        # burst is over. Arming/retraction already happened LIVE on those co-frame
        # events (see the area_changed block); here we just retire the entry. An
        # entry that never reached its required area simply never armed.
        for did in list(self._pending):
            if self._pending[did].start_frame < ev.frame:
                del self._pending[did]
        # Judge the position change an EARLIER frame proposed, then record this
        # event's own candidate (spec 2026-08-01-topological-segment-validity).
        # Runs BEFORE the per-def loop so a cancelled def is not also fed this
        # event, and before the arm phase so arriving somewhere by warp still
        # arms what lives there — closures before arming, as everywhere else.
        self._flush_move(ev, notices)
        if ev.type in ("session_started", "game_reset"):
            # global_timer restarts at a session boundary and game_reset
            # carries a boot-range frame, so a remembered node would be
            # compared against a frame number from a different epoch.
            self._settled_node = None
            self._pending_move = None
            self._cancelled.clear()
        elif ev.type == "area_changed":
            # area_changed, never level_changed: this payload names the level
            # AND the settled area outright, where `ctx.area` during a
            # level_changed is still the OLD level's (the area detector
            # establishes one event later on the same tick — see below). Every
            # real level crossing emits a co-frame area_changed, so recording
            # only here misses nothing.
            #
            # `.get`, not `[...]`: an area_changed whose payload omits `level`
            # resolves to node None = position UNKNOWN, and `_flush_move`
            # declines to judge an unknown — the unknown-means-yes convention,
            # reached here rather than by raising. A bare subscript was the
            # first version and it took the whole engine down on the first
            # payload that did not carry the key.
            self._pending_move = (ev.frame,
                                  topology.node_for(ev.payload.get("level"),
                                                    ev.payload.get("to")))
        # Track the most recent level/area transition frame BEFORE per-def
        # processing so the echo guard below can test both echo shapes.
        if ev.type in ("level_changed", "area_changed"):
            self._last_transition_frame = ev.frame
        # Same discipline for Usamune's own clock origin: _close reads this to
        # decide whether a closing event's igt_frames measures the segment, so
        # it has to be current for the closures that run below on THIS event.
        # Every event that both zeroes the IGT and closes an attempt does so
        # with a payload carrying no igt_frames (a reset/level edge), so
        # updating first can never let an event validate its own close.
        if ev.type == "session_started":
            self._last_igt_zero_frame = None
        elif _zeroes_usamune_igt(ev):
            self._last_igt_zero_frame = ev.frame
        if ev.type == "area_changed":
            if _real_edge(ev):
                self._last_area_edge_frame = ev.frame  # cross-area relocation
            # Pin arm positions: a def armed by THIS tick's level_changed
            # recorded a stale ctx.area (the area detector establishes the
            # new level's area one event later, same frame — main.py order).
            # The co-frame establishing/corrective area event owns the truth.
            for did, stale in self._armed.items():
                if stale.start_frame == ev.frame:
                    self._armed[did] = replace(stale, area=ev.payload["to"])
            # Deferred destination-subarea entries resolve LIVE here, so the chip
            # tracks Mario in real time: the castle loads the lobby (1) then warps
            # to the real area, all on this frame across several polls. Each
            # co-frame area updates the entry; the instant it equals the required
            # interior area we arm, and the instant a LATER co-frame moves away
            # (the transient lobby before a basement/upstairs settle) we retract.
            # This makes a Lobby destination (whose only co-frame is the
            # establishing 1->1) arm on ENTRY, not at the next unrelated event —
            # the LBLJ grounds->lobby regression (live report 2026-06-13). The
            # entry stays in _pending until the frame advances (drop above), so a
            # later co-frame can still retract it.
            for did in list(self._pending):
                stale = self._pending[did]
                if stale.start_frame != ev.frame:
                    continue
                p = replace(stale, area=ev.payload["to"])
                self._pending[did] = p
                live = self._armed.get(did)
                if p.area == p.required_area and live is None:
                    self._armed[did] = replace(p, required_area=None)
                    notices.append({"event": "segment_armed",
                                    "segment_id": did,
                                    "name": self._def_by_id[did].name,
                                    "frame": p.start_frame})
                elif p.area != p.required_area and live is not None \
                        and live.start_frame == p.start_frame:
                    self._disarm(self._def_by_id[did], ev, notices)
        # Event-level echo classification — shapes (2a)/(2b)/(3)/(4)/(5) depend
        # only on the event payload + _last_transition_frame, never on a
        # per-def arm, so classify ONCE before the loop.  An echo anchor is
        # involuntary — it must be INVISIBLE to the engine entirely: no
        # closure, no continuation re-arm, no arm-phase arm/re-arm, for
        # every def (live regression 2026-06-12: the lobby door's section
        # reset matched LBLJ's attempt_anchor start trigger in the ARM phase
        # and rebased start_frame to the door).  Extracted to _anchor_echo
        # (spec 2026-07-23-default-routes-foundation) so the waypoint matcher
        # (_feed_waypoint) reuses the SAME echo definition instead of a
        # second copy drifting out of sync; full shape taxonomy in the
        # module docstring and the method's own docstring.
        anchor_is_echo = self._anchor_echo(ev)
        for d in self._defs:
            arm = self._armed.get(d.id)
            start_clause = self._first_match(d.start_triggers, ev, ctx)
            starts = start_clause is not None
            if arm is not None:
                # Armed-branch dispatch (spec 2026-07-28-multi-step-segments).
                # A loose def owns its own waypoint progression, so it takes
                # _feed_loose whether or not it carries waypoints; a strict
                # def splits on waypoints exactly as it did before.
                if d.match_mode == "loose":
                    handler = self._feed_loose
                elif d.waypoints:
                    handler = self._feed_waypoint
                else:
                    handler = self._feed_strict
                closed.extend(handler(Attempt, d, arm, ev, ctx, notices,
                                      anchor_is_echo, starts))
            # arm / re-arm — guards re-evaluated every time (spec).
            # Echo anchors are INVISIBLE here too: an involuntary door/load
            # echo matching an attempt_anchor start trigger must neither arm
            # an idle def nor rebase an armed one (live regression
            # 2026-06-12: the lobby door's section-reset echo rebased LBLJ's
            # start_frame/started_utc to the door, so replay and rta began
            # at the door instead of the segment start).  The arm-frame
            # check is the per-def belt for shape (1) — `arm` is the
            # pre-closure value, unchanged for echoes.  REAL anchors already
            # took the continuation re-arm in the closure phase above; for
            # those the attempt_anchor replace here remains idempotent
            # (identical _Arm values).  The spec's "re-arm on start trigger
            # refire" applies to player actions only.
            echo_invisible = ev.type in _ANCHOR_TYPES and (
                anchor_is_echo
                or (arm is not None and ev.frame == arm.start_frame))
            # EXCEPTION — cross-area relocation arm (live report 2026-06-13): an
            # anchor co-frame with a real area edge is a WARP LANDING in a new
            # area. An IDLE destination segment must still arm there even though
            # the landing spawns in ACT_WARP_DOOR_SPAWN (door-echo-classified) —
            # else warping to the lobby never re-arms LBLJ. Scoped to idle defs
            # so it never REBASES an armed one (the 2026-06-12 regression: only
            # an already-armed def must be echo-protected from rebasing).
            relocation_arm = (ev.type in _ANCHOR_TYPES
                              and ev.frame == self._last_area_edge_frame
                              and d.id not in self._armed)
            # A waypoint-bearing def that is STILL ARMED owns its own
            # progression via _feed_waypoint's `progress` counter (spec
            # 2026-07-23-default-routes-foundation) — a start-clause refire
            # (e.g. the "exit SL" waypoint doubling as SL->HMC's own start
            # trigger) must not fall through to this generic re-arm and reset
            # progress back to 0. Only gates while armed: once _feed_waypoint
            # has disarmed the def (major-action cancel), a fresh start-clause
            # match here re-arms normally, same as any other def.
            # ARM-POSITION gate (live report 2026-07-27): a start trigger
            # fired, but a Usamune menu warp can leave Mario somewhere the
            # segment cannot be run from — see can_run_from's section comment.
            # A game_reset is exempt: the projector keeps the pre-reset level
            # until the next level_changed, so ctx.level names where the player
            # WAS, not where the reset put them.
            arm_position_possible = starts and (
                ev.type == "game_reset"
                or can_run_from(d, start_clause, ctx.level))
            if starts and (not echo_invisible or relocation_arm) \
                    and not (d.waypoints and d.id in self._armed) \
                    and _route_allows(d, ctx) \
                    and arm_position_possible \
                    and all(GUARDS[g["type"]].check(g, ctx)
                            for g in d.guards
                            if GUARDS[g["type"]].phase == "arm"):
                # A destination-subarea level trigger can't be confirmed yet
                # (the castle lobby loads before the warp settles) — DEFER it
                # into _pending keyed on the required interior area, to be
                # resolved when the co-frame area_changed burst is over. The
                # source subarea (from_subarea) is already in the lambda, so a
                # plain match here arms immediately as before.
                # A normal arm supersedes any remembered cancellation:
                # the def is live again by its own start condition, so the
                # resurrection memory would only be a stale second door.
                self._cancelled.pop(d.id, None)
                req = (start_clause.get("to_subarea")
                       if ev.type == "level_changed" else None)
                if req is not None:
                    self._pending[d.id] = _Arm(
                        jid=ev.id, start_frame=ev.frame,
                        started_utc=ev.wall_time_utc, anchor_type=ev.type,
                        session_id=ev.session_id, level=ctx.level,
                        area=ctx.area, required_area=req,
                        deadline_frame=self._deadline_for(d, ev))
                else:
                    fresh = d.id not in self._armed
                    # A plain LOOSE def's own start trigger can genuinely
                    # refire while it is still armed and mid-route (live
                    # audit 2026-07-29: 13 refires in the user's real
                    # session, restarting Bowser 2/1 movements that had gone
                    # stale under an earlier abandoned exit). The restart is
                    # the honest read — the start condition genuinely
                    # happened again, so the fresh arm times the real
                    # attempt, and the old in-flight arm's elapsed time was
                    # never a completed attempt worth a row. But `fresh` is
                    # False here (d.id was already armed), so this used to be
                    # completely SILENT — no notice, no trace, same class of
                    # defect as the anchor-relocation bug this def's docstring
                    # describes, just structurally adjacent to _feed_loose
                    # rather than inside it. Surface it with the ordinary
                    # disarm+arm notice pair instead. `not d.waypoints` is
                    # belt-and-suspenders here, not load-bearing: the outer
                    # `not (d.waypoints and d.id in self._armed)` guard above
                    # already makes `d.waypoints` false whenever `not fresh`
                    # is true (a waypoint-bearing def that's already armed
                    # never reaches this branch at all — see the AUTHORING
                    # CAVEAT in _feed_waypoint's docstring), so this can never
                    # widen to the waypoint case; it names the invariant for
                    # a reader who doesn't want to re-derive it.
                    loose_plain_refire = (not fresh and d.match_mode == "loose"
                                          and not d.waypoints)
                    if loose_plain_refire:
                        self._disarm(d, ev, notices)  # visible: no row for
                        # the discarded partial, same as every other no-row
                        # disarm on this branch
                    self._armed[d.id] = _Arm(jid=ev.id, start_frame=ev.frame,
                                             started_utc=ev.wall_time_utc,
                                             anchor_type=ev.type,
                                             session_id=ev.session_id,
                                             level=ctx.level, area=ctx.area,
                                             deadline_frame=self._deadline_for(d, ev))
                    if fresh or loose_plain_refire:
                        notices.append({"event": "segment_armed",
                                        "segment_id": d.id, "name": d.name,
                                        "frame": ev.frame})
            # RESURRECTION (Griffin's ruling 2026-08-01, from the measurement
            # in tools/measure_topology_cancels.py): a REAL anchor at the
            # position a topologically-cancelled arm stood in is the retry
            # loop, not a new event. `_feed_loose` already reads an anchor at
            # the arm position that way for a LIVE arm; this is the same
            # reading for one the topological rules killed, and without it a
            # movement whose start trigger cannot be re-fired without redoing
            # a whole Bowser fight would simply become unpractisable after any
            # detour. Runs AFTER the ordinary arm branch and is gated on the
            # def still being idle, so it can never rebase a live arm.
            remembered = self._cancelled.get(d.id)
            if remembered is not None:
                cancelled_arm, expires = remembered
                if ev.frame >= expires:
                    del self._cancelled[d.id]   # see _cancelled: no clock, no bound
                elif (ev.type in _ANCHOR_TYPES and not anchor_is_echo
                      and not _at_arm_position(cancelled_arm, ctx)):
                    # FORFEIT (Griffin, 2026-08-01): a real reset SOMEWHERE
                    # ELSE ends the retry, permanently — "if... in the middle
                    # of lobby -> wf, I decided to reset to bitdw, I think
                    # that's a genuine kill of the segment, because we've now
                    # gone out of order in a way that doesn't make sense for
                    # practicing... until I get back to Bowser 1 and trigger
                    # it from the beginning again". Without this the memory
                    # would survive the relocation and the NEXT reset back at
                    # the start would resurrect a run he had abandoned twice
                    # over. Mirrors _feed_strict's existing reading of an
                    # anchor away from the arm position as a relocation.
                    del self._cancelled[d.id]
                elif (ev.type in _ANCHOR_TYPES and not anchor_is_echo
                      and d.id not in self._armed
                      and _at_arm_position(cancelled_arm, ctx)):
                    del self._cancelled[d.id]
                    self._armed[d.id] = _Arm(
                        jid=ev.id, start_frame=ev.frame,
                        started_utc=ev.wall_time_utc, anchor_type=ev.type,
                        session_id=ev.session_id,
                        level=(ctx.level if ctx.level is not None
                               else cancelled_arm.level),
                        area=(ctx.area if ctx.area is not None
                              else cancelled_arm.area),
                        deadline_frame=self._deadline_for(d, ev))
                    notices.append({"event": "segment_armed",
                                    "segment_id": d.id, "name": d.name,
                                    "frame": ev.frame})
        # THE GRAND STAR ENDS THE RUN, so nothing may still be running after
        # it. Griffin, 2026-08-05, on the credits screen with "CCM -> BBH"
        # still showing a live timer: "at the end of the game (i.e., after
        # grabbing the final star and finishing the Bowser 3 segment), there
        # should be absolutely no segments still running (the game is
        # literally over)... It also seems to persist to new areas of the
        # map?"
        #
        # `key_grabbed` with `which == "grand"` is the game's own end (level
        # 34; the grand star never fires `star_collected`, which is why
        # `key.py` stamps this instead -- see the trigger table above). It is
        # already journalled, so this needs no new memory read and applies
        # retroactively on replay like every other projection rule.
        #
        # LAST, deliberately: Bowser 3's own definition ENDS on this event, so
        # running after the closures above is what lets it record its success
        # and only then clears whatever else was left over.
        #
        # SILENT, via the same helper an off-route move uses: "a movement that
        # never happened must not bank a failure" (`_cancel_topologically`).
        # A movement interrupted by winning the game did not happen either,
        # and banking failures here would be exactly the misattribution the
        # untargeted-reset rule exists to stop (projection.py). Cancelled
        # rather than hard-disarmed so the ordinary "return to the start and
        # press reset" recovery still brings it back.
        if ev.type == "key_grabbed" and ev.payload.get("which") == "grand":
            for d in [d for d in self._defs if d.id in self._armed]:
                self._cancel_topologically(d, ev, notices)
        self._progress_notices(progress_before, ev.frame, notices)
        return closed, notices

    def _progress_notices(self, before: dict, frame: int,
                          notices: list) -> None:
        """Say so whenever an armed def's step cursor MOVED.

        THE BUG (live report 2026-08-02): he walked into the Basement, the
        engine advanced `WF → SSL` to step 2 on that exact frame — proved by
        replaying his own journal — and the card read "Step 1 of 2 · Waiting
        for Enter Castle Inside Basement" for the next 77 seconds, until an
        unrelated event happened to force a view refetch. `armed_detail` is
        re-derived from this arm on every `/api/session` fetch and is
        therefore always correct WHEN ASKED; nothing was asking. A cursor
        move journals no event of its own and `area_changed` is not in the
        browser's `REFRESH_ON` set, so the one state change the whole
        multi-step display exists to show was the one change with no way to
        reach the screen.

        A DIFF rather than an append at each site, deliberately: four
        branches move a cursor (`_feed_waypoint`'s advance and its anchor
        rewind to 0, and the same pair in `_feed_loose`), a fifth would be
        added by the next mode, and a notice missing from one of them looks
        exactly like this bug again — a card frozen on a step the player has
        already passed. Comparing the arm before and after cannot be
        forgotten by a branch that does not know it exists.

        Broadcast-only, never journaled — same rule the arm/disarm notices
        follow (`tracking/service.py`): the projector re-derives arm state
        from the journal on replay, so writing a derived row back would make
        replay non-idempotent.
        """
        for sid, arm in self._armed.items():
            was = before.get(sid)
            if was is None or was == arm.progress:
                continue
            d = self._def_by_id.get(sid)
            notices.append({"event": "segment_progress", "segment_id": sid,
                            "name": d.name if d else "", "frame": frame,
                            "progress": arm.progress,
                            "total": len(d.waypoints) if d else 0})

    def _matches(self, triggers, ev, ctx) -> bool:
        return any(TRIGGERS[t["type"]].match(t, ev, ctx) for t in triggers)

    def _first_match(self, triggers, ev, ctx):
        """The first start clause that matches ev (its dict — so the engine can
        read to_subarea), or None. Mirrors _matches' any()-semantics."""
        for t in triggers:
            if TRIGGERS[t["type"]].match(t, ev, ctx):
                return t
        return None

    def _anchor_echo(self, ev) -> bool:
        """True when a practice_reset/state_loaded is an INVOLUNTARY IGT-reset
        echo — a door crossing, the post-star save prompt, a textbox/cutscene
        time-stop, a paused-briefly transition co-frame, or an in-level
        teleporter — rather than a real player reset. Moved verbatim out of `feed`'s per-event
        `anchor_is_echo` local (spec 2026-07-23-default-routes-foundation) so
        the waypoint matcher (`_feed_waypoint`) shares the SAME echo
        definition instead of a second copy that could drift; the full
        shape-by-shape rationale lives in the module docstring's "load-echo
        rule" section. Shapes (2a)/(2b)/(3)/(4)/(5)/(6) depend only on the event
        payload + `_last_transition_frame` (an instance attribute), never on
        a per-def arm — shape (1), the arm-frame echo, is checked separately
        per def by its callers (`ev.frame == arm.start_frame`)."""
        return ev.type in _ANCHOR_TYPES and (
            # (2a) intra-area door echo: prev_action authoritative when
            # present (door anim ran on the previous tick); fallback to
            # action for events journaled before prev_action existed.
            ev.payload.get("prev_action",
                           ev.payload.get("action")) in DOOR_ACTIONS
            # (2b) non-warp door recency echo: IGT reset lands 1-5 frames
            # after the door action ends; frames_since_door bridges the gap.
            or (ev.payload.get("frames_since_door") is not None
                and 0 <= ev.payload["frames_since_door"]
                <= _DOOR_ECHO_WINDOW)
            # (3) transition co-frame echo, pause-gated: menu warps are
            # co-frame too but carry paused_frames_before 13-890 (live
            # logs) — they fail the gate and stay REAL attempt boundaries.
            or (ev.frame == self._last_transition_frame
                and ev.payload.get("paused_frames_before", 0)
                <= _MENU_PAUSE_FRAMES)
            # (4) save-prompt echo: the post-star "SAVE & CONTINUE?" course-
            # complete screen reloads on confirm, resetting Usamune's IGT.
            # save_pending means the anchor detector saw the save menu this
            # period — an involuntary reload, not a player reset.  Like the
            # door shapes it feeds echo_invisible too (an attempt_anchor-armed
            # segment must not rebase its start_frame onto the save reload).
            or ev.payload.get("save_pending", False)
            # (5) dialogue/cutscene echo: a textbox/intro-cutscene time-stop
            # re-initialises Usamune's IGT a frame or two after control is
            # regained — an involuntary reset that closed the just-armed Lakitu
            # Skip segment (live journal 2026-06-14).  frames_since_dialog
            # bridges the gap exactly as frames_since_door does (2b); we never
            # split timing on a textbox in any level (user rule 2026-06-14).
            or (ev.payload.get("frames_since_dialog") is not None
                and 0 <= ev.payload["frames_since_dialog"]
                <= _DIALOG_ECHO_WINDOW)
            # (6) in-level teleporter echo: the CCM broken bridge and the WDW
            # corner warps relocate Mario inside the SAME area, so no
            # transition fires for shape (3) to catch, and Usamune zeroes its
            # counter anyway. The player took a route the level provides —
            # "these should not trigger resets, because they are a legitimate
            # part of the level" (2026-08-03) — so a segment running through
            # one must not rewind, re-arm or bank a row. Still an ANCHOR, so
            # `_zeroes_usamune_igt` moves the IGT basis and the segment's time
            # falls back to the rta delta rather than measuring from the warp.
            or ev.payload.get("teleport", False))

    def _arrived_by_a_real_move(self, ev) -> bool:
        """True when this anchor landed on the same frame as a LEGITIMATE
        world move — a door, or a PAUSE EXIT — rather than on a reset the
        player chose.

        `_anchor_echo`'s shape (3) already catches a transition co-frame, but
        gates it on a SHORT pause, deliberately: a Usamune menu warp is
        co-frame too and carries `paused_frames_before` 13-890, and a menu
        warp really is a new attempt boundary. A PAUSE EXIT carries a long
        pause for the same reason (the pause menu was open), so it fell
        through that gate — and rewound a multi-step arm to step 1 on the very
        move that had just advanced it. Live report 2026-08-03: "it briefly
        flashed step 3 of 3, then it reset", followed by nothing recorded when
        he reached WF, because a rewound cursor can never reach its end.

        The discriminator is the WORLD GRAPH, which is rule 1's premise read
        for a second purpose: a menu warp fabricates an edge, a pause exit
        walks one. Measured over both journals, restricted to co-frame anchors
        with a long pause: 73 land on real edges (`30->17`, `17->6:1`,
        `21->6:2`, `7->6:1` — doors and pause exits) and 193 do not
        (`22->17`, `8->17`, `16->34`, and `17->6:2`, which is the Upstairs
        menu warp rather than BitDW's own exit into the Lobby). No overlap in
        kind.

        A hypothesis this replaces, recorded so nobody spends the evening on
        it again: `frames_since_warp_op` does NOT separate them. It reads 0
        for an ordinary door and stale for a pause exit, but menu warps sit on
        both sides of it.

        Deliberately NARROW — it gates only the waypoint matcher's rewind, not
        `_anchor_echo` itself. Widening the echo would make these anchors
        invisible to EVERY definition and to attempt boundaries generally,
        which is a much larger claim about his recorded history than this
        report supports. That widening is owed, with this measurement attached.
        """
        if ev.frame != self._last_transition_frame:
            return False
        pending = self._pending_move
        if pending is None or pending[0] != ev.frame or pending[1] is None:
            return False
        return (self._settled_node is not None
                and self._settled_node != pending[1]
                and topology.is_legal_move(self._settled_node, pending[1]))

    def _feed_strict(self, Attempt, d, arm: _Arm, ev, ctx, notices,
                     anchor_is_echo: bool, starts: bool) -> list:
        """Today's armed-branch chain, extracted verbatim from feed() so the
        armed branch can dispatch on SegmentDef.match_mode (spec
        2026-07-28-multi-step-segments). Behaviour for a STRICT def is
        unchanged — the module docstring's closure/anchor/echo invariants all
        describe THIS method. `anchor_is_echo` is computed once per EVENT in
        feed() (before the per-def loop) and passed down; `starts` is
        computed once per (event, definition) INSIDE that loop, from
        `d.start_triggers`, and passed down too — both for the uniform
        handler signature the dispatch table (Task 2) calls through, not
        because both are event-level facts.

        Also handles EXCLUSIVE defs (the third match_mode, same spec, one
        gated branch below): a plain waypoint-free def reaches this same
        method through the same `else` in feed()'s dispatch (only "loose"
        and "carries waypoints" divert elsewhere), so the shared chain above
        — end/relocation/echo/anchor/death/game_reset/off-route level — is
        identical for both modes; EXCLUSIVE adds exactly one more way to
        cancel: a star or Bowser-key grab that isn't the end trigger."""
        closed = []
        if self._matches(d.end_triggers, ev, ctx):
            a = self._close(Attempt, d, arm, ev, "success", None)
            if a:
                closed.append(a)
            self._disarm(d, ev, notices)
        elif ev.type == "area_changed" \
                and not _at_arm_position(arm, ctx):
            # RELOCATION via area change (live report 2026-06-13): Mario
            # moved to a DIFFERENT castle area than where this segment
            # armed (the lobby<->upstairs star door, a basement door, a
            # warp), so its start position no longer holds — disarm with
            # NO row, exactly as a warp/savestate to another area does.
            # Without this a lobby segment stays armed after crossing to
            # the upstairs and double-arms with the upstairs segment; the
            # co-frame load echo that WOULD relocate it is suppressed
            # (anchor_is_echo). A segment armed by THIS tick's level entry
            # was re-pinned to ctx.area above, and a same-area door fires
            # no area_changed at all (intra-area echo, still armed), so
            # neither is touched. Supersedes the 2026-06-12 "stay armed
            # through a cross-area door" behaviour.
            self._disarm(d, ev, notices)
        elif ev.type in _ANCHOR_TYPES \
                and ev.frame == arm.start_frame:
            # Shape (1) — arm-frame echo: the level_changed that armed
            # this segment and the synthetic anchor it triggers share
            # the same global-timer tick.  Suppressed UNCONDITIONALLY:
            # the player may have been paused on the grounds for
            # minutes before entering the lobby — a large
            # paused_frames_before here is normal and must not
            # reclassify this as a real reset.  Per-def (depends on
            # the arm), unlike the event-level shapes below.
            # (live gate 2026-06-12, seq 40-45)
            pass
        elif ev.type in _ANCHOR_TYPES and anchor_is_echo:
            # Shapes (2a)/(2b)/(3) — event-level echoes, classified
            # once before the loop (see anchor_is_echo above; full
            # taxonomy in the module docstring).  No closure, no row,
            # no disarm — and the arm phase below skips echoes too,
            # so the _Arm is untouched.
            pass
        elif ev.type in _ANCHOR_TYPES \
                and not _at_arm_position(arm, ctx):
            # RELOCATION (live report 2026-06-12): a real warp/load
            # landed outside this segment's start position — the
            # Usamune menu warp to another area is the player MOVING,
            # not a failed attempt, so no reset row. The start
            # conditions no longer hold → disarm (notice); defs
            # anchored at the destination arm in the arm phase below
            # (segment swap).
            self._disarm(d, ev, notices)
        elif ev.type in _ANCHOR_TYPES:
            # AFK (>= 150 paused frames) and no-op closures (Mario
            # never acted since the last anchor — warp/reset spam,
            # live feedback 2026-06-12) discard the row; both still
            # re-arm below.  acted_tracking-gated: historical events
            # without the flag keep recording (mirrors the star-side
            # discard in projection._close_by_reset).
            afk = ev.payload.get("paused_frames_before", 0) \
                >= _AFK_PAUSE_FRAMES
            unacted = ev.payload.get("acted_tracking", False) \
                and not ev.payload.get("mario_acted", False)
            if not afk and not unacted:
                a = self._close(Attempt, d, arm, ev, "reset", None)
                if a:
                    closed.append(a)
            # Re-arm in place at the anchor frame instead of disarming.
            # A Usamune L-reset respawns Mario at the level's last entrance
            # — which IS the segment's start position in the practice loop
            # (lobby door for LBLJ, HMC exit for MIPS). Timing from this
            # anchor is equivalent to a fresh start-trigger arm.
            # The segment never stops being armed; no armed/disarmed
            # notices are emitted (attempt boundary, not a state change).
            # For defs with attempt_anchor start triggers the arm phase
            # below will replace this _Arm with identical values
            # (fresh=False → no duplicate notice) — idempotent.
            # Position carries over (ctx wins, arm fills unknowns) so
            # the gate above keeps working across continuations.
            self._armed[d.id] = _Arm(
                jid=ev.id, start_frame=ev.frame,
                started_utc=ev.wall_time_utc,
                anchor_type=ev.type,
                session_id=ev.session_id,
                level=ctx.level if ctx.level is not None else arm.level,
                area=ctx.area if ctx.area is not None else arm.area,
            )
        elif ev.type == "death":
            a = self._close(Attempt, d, arm, ev, "death",
                            ev.payload.get("cause"))
            if a:
                closed.append(a)
            self._disarm(d, ev, notices)
        elif ev.type == "game_reset":
            a = self._close(Attempt, d, arm, ev, "hard_reset", None)
            if a:
                closed.append(a)
            self._disarm(d, ev, notices)
        elif d.match_mode == "exclusive" and ev.type in _MAJOR_EVENT_TYPES:
            # EXCLUSIVE's one addition over Strict (third match_mode, spec
            # 2026-07-28-multi-step-segments): a star or Bowser-key grab that
            # isn't this def's own end trigger (already checked at the top of
            # this chain) means the attempt wasn't exclusively this segment —
            # cancel silently, no row, same as every other abandon above.
            # Gated on match_mode so a plain STRICT def is untouched: today it
            # falls through this whole chain on a star/key grab and stays
            # armed (no branch here matches ev.type in _MAJOR_EVENT_TYPES) —
            # see this module's `_feed_waypoint`, which already cancels a
            # WAYPOINT-bearing def on the same star/key grab; a plain def had
            # no way to express that until this branch.
            # `level_changed` real-edge crossings are deliberately NOT
            # included here (unlike `_is_major_action`, which folds them in
            # for the waypoint matcher): the elif below already disarms an
            # off-route level crossing for every mode, including this one,
            # gated on `not starts` so a refire of this def's OWN start
            # trigger keeps re-arming instead of cancelling. Reusing
            # `_is_major_action` here, unconditional on `starts`, would take
            # that refire exemption away from exclusive-mode defs only —
            # nothing about "exclusive" needs a level crossing to behave any
            # differently than Strict already does.
            self._disarm(d, ev, notices)
        elif ev.type in ("level_changed", "session_started") \
                and not starts:
            self._disarm(d, ev, notices)   # silent: no row
        return closed

    def _feed_waypoint(self, Attempt, d, arm: _Arm, ev, ctx, notices,
                       anchor_is_echo, starts) -> list:
        """Ordered-sequence matcher for a waypoint-bearing def (spec
        2026-07-23-default-routes-foundation) — the armed-branch counterpart
        to the plain success/relocation/anchor/death chain above, taken for
        any def carrying d.waypoints instead. Precedence (first match wins):
        end (only once every waypoint is consumed) > death/game_reset >
        session_started (mirrors the plain chain: a session boundary disarms
        silently, no row, regardless of progress — an armed segment must not
        survive across sessions) > echo (invisible, exactly like the plain
        chain) > real anchor (rewinds the sequence to its first waypoint and
        re-arms IN PLACE at the anchor — the practice-retry loop — AND
        records a RESET row for the attempt that ends there, exactly like
        the plain chain's own anchor-refire reset, subject to the SAME AFK/
        unacted discard. This used to record no row at all, flagged as a
        live-gate VERIFY item ("precise relocation-vs-continuation nuance");
        the user has since settled it (round 2, live report 2026-07-30) — he
        expects the reset row, since the practice log is how he sees his own
        retries and a whole class of segment silently omitting them makes it
        lie about what he did. The rewind-in-place relocation itself was
        never in question, only the missing row) > next waypoint (advance
        `progress`) >
        major action (a star/key grab or a real level crossing that ISN'T
        the next waypoint — the player switched tasks or misrouted — silent
        cancel, no row, mirrors the plain chain's silent level_changed
        disarm) > transparent (anything else changes nothing, e.g.
        area_changed/warp_entered/spawned mid-sequence).

        AUTHORING CAVEAT (route design, not a code defect): the major-action
        cancel above pops this def from self._armed; the SAME event is then
        re-evaluated by feed()'s arm/re-arm phase against d.start_triggers.
        If a def's start trigger is LOOSER than (or equal to) a waypoint
        clause it could collide with — e.g. a start trigger that doesn't
        pin a destination while a waypoint does — the cancelling event can
        satisfy the start trigger and re-arm in the same tick (a
        segment_disarmed+segment_armed notice pair instead of a clean
        abandon), exactly as an ordinary re-arm-on-start-trigger-refire
        would. This is the existing "re-firing a start trigger while armed
        re-arms" convention (module docstring), not new behavior — but a
        route's start trigger should be written at least as specific as
        every waypoint clause it could be confused with, or a misroute can
        silently resume instead of truly cancelling."""
        _ = (anchor_is_echo, starts)  # uniform dispatch signature (Task 2,
        # spec 2026-07-28-multi-step-segments) — this matcher derives its
        # own echo classification (self._anchor_echo) and never disarms on
        # a bare level_changed/session_started, so it needs neither.
        closed = []
        complete = arm.progress >= len(d.waypoints)
        if complete and self._matches(d.end_triggers, ev, ctx):
            a = self._close(Attempt, d, arm, ev, "success", None)
            if a:
                closed.append(a)
            self._disarm(d, ev, notices)
            return closed
        if ev.type == "death":
            a = self._close(Attempt, d, arm, ev, "death", ev.payload.get("cause"))
            if a:
                closed.append(a)
            self._disarm(d, ev, notices)
            return closed
        if ev.type == "game_reset":
            a = self._close(Attempt, d, arm, ev, "hard_reset", None)
            if a:
                closed.append(a)
            self._disarm(d, ev, notices)
            return closed
        if ev.type == "session_started":
            self._disarm(d, ev, notices)   # silent: no row (session boundary)
            return closed
        if ev.type in _ANCHOR_TYPES:
            # echo (arm-frame or event-level) is invisible; a real anchor
            # rewinds the sequence and re-arms IN PLACE (retry loop) --
            # recording a RESET row for the attempt that ends here, exactly
            # like the plain chain's own anchor-refire reset (round 2, live
            # report 2026-07-30: this branch recorded NO row at all, so the
            # practice log silently omitted every retry of a waypoint-
            # bearing segment -- this settles the "live-gate VERIFY item"
            # this method's own docstring used to name; the rewind-in-place
            # relocation itself was never in question, only the missing
            # row). Same AFK/unacted discard the plain chain applies
            # (mirrors the star-side discard in projection.py's
            # _close_by_reset) -- a true no-op anchor refire still records
            # nothing.
            if (ev.frame == arm.start_frame or self._anchor_echo(ev)
                    or self._arrived_by_a_real_move(ev)):
                return closed
            afk = ev.payload.get("paused_frames_before", 0) \
                >= _AFK_PAUSE_FRAMES
            unacted = ev.payload.get("acted_tracking", False) \
                and not ev.payload.get("mario_acted", False)
            if not afk and not unacted:
                a = self._close(Attempt, d, arm, ev, "reset", None)
                if a:
                    closed.append(a)
            self._armed[d.id] = replace(
                arm, progress=0, start_frame=ev.frame,
                started_utc=ev.wall_time_utc, jid=ev.id,
                anchor_type=ev.type, session_id=ev.session_id,
                level=ctx.level if ctx.level is not None else arm.level,
                area=ctx.area if ctx.area is not None else arm.area)
            return closed
        if not complete and self._matches(d.waypoints[arm.progress], ev, ctx):
            self._armed[d.id] = replace(arm, progress=arm.progress + 1)
            return closed
        if _is_major_action(ev):
            self._disarm(d, ev, notices)   # silent cancel, no row
            return closed
        return closed   # transparent

    def _deadline_for(self, d, ev) -> int | None:
        """The staleness deadline a freshly-armed (or re-armed) _Arm should
        carry (spec 2026-07-28-multi-step-segments): None for a strict def,
        bounded by its cancel rules instead; ev.frame + budget_frames(this
        def's best success so far) for a loose one.

        ONE call site for every place an _Arm enters self._armed/self._pending
        for a loose def — the pending->armed promotion inherits whatever this
        returned through `replace()`, for free, so a future arm site that
        forgets to call this ships a visibly missing call instead of a silent
        None (the bug this exists to prevent: the deferred destination-
        subarea path into self._pending was the one the brief for this task
        missed, and it is where a large share of the seeded castle movements
        arm)."""
        if d.match_mode != "loose":
            return None
        return ev.frame + budget_frames(self._best_success.get(d.id))

    def _feed_loose(self, Attempt, d, arm: _Arm, ev, ctx, notices,
                    anchor_is_echo, starts) -> list:
        """Armed-branch matcher for a LOOSE definition (spec
        2026-07-28-multi-step-segments): the player says where a segment
        starts and where it ends, and nothing in between is described.

        Precedence, first match wins:
          staleness deadline > end (once every waypoint is consumed) >
          death > game_reset > session_started > echo anchor (invisible) >
          real anchor at the arm position (reset row, re-arm in place) >
          real anchor elsewhere (transparent — arm untouched) >
          next waypoint (advance) > EVERYTHING ELSE IS TRANSPARENT.

        That last line is (most of) the whole feature: a star grab, a key
        grab, an area change and an off-route level crossing all pass
        straight through, where _feed_strict/_feed_waypoint would cancel or
        disarm.

        The DEADLINE IS CHECKED FIRST, ahead of both the end trigger and the
        death/reset rows. An arm that has outlived its budget is dead; a
        success or a failure recorded through it would be a claim about a run
        the player walked away from.

        A real anchor NOT at the arm position is transparent too (live report
        2026-07-28, fixed from the `_feed_strict`-inherited relocation disarm
        this method used to carry): a loose def describes only its start and
        end, so its route is free to cross through positions it didn't start
        at, and often MUST — "Bowser 2 -> Upstairs" arms in the basement, and
        reaching Upstairs requires walking back through the lobby, so the
        first practice_reset anywhere along the way used to kill the attempt
        with no row and no notice (the split just vanished mid-run). That is
        different from _feed_strict/_feed_waypoint, where a route is fully
        described and an anchor off it genuinely means "the player moved
        somewhere the described route never goes" — for a loose def there is
        no described route to have left, so nothing here justifies a silent
        kill. Only an anchor AT the arm position still means something: the
        player deliberately returned to the start and pressed reset/reload,
        which is a genuine retry (reset row, re-arm in place, fresh
        deadline). Everything that ends a loose attempt is exactly what the
        module docstring's LOOSE bullet already promises: death, game_reset,
        session_started, and the staleness deadline — not a relocated
        anchor."""
        _ = (anchor_is_echo, starts)   # uniform handler signature (Task 2)
        closed = []
        if arm.deadline_frame is not None and ev.frame >= arm.deadline_frame:
            self._disarm(d, ev, notices)   # silent: no row, stats stay clean
            return closed
        complete = arm.progress >= len(d.waypoints)
        if complete and self._matches(d.end_triggers, ev, ctx):
            a = self._close(Attempt, d, arm, ev, "success", None)
            if a:
                closed.append(a)
            self._disarm(d, ev, notices)
            return closed
        if ev.type == "death":
            a = self._close(Attempt, d, arm, ev, "death",
                            ev.payload.get("cause"))
            if a:
                closed.append(a)
            self._disarm(d, ev, notices)
            return closed
        if ev.type == "game_reset":
            a = self._close(Attempt, d, arm, ev, "hard_reset", None)
            if a:
                closed.append(a)
            self._disarm(d, ev, notices)
            return closed
        if ev.type == "session_started":
            self._disarm(d, ev, notices)
            return closed
        if ev.type in _ANCHOR_TYPES:
            if (ev.frame == arm.start_frame or self._anchor_echo(ev)
                    or self._arrived_by_a_real_move(ev)):
                return closed          # echo: invisible
            if not _at_arm_position(arm, ctx):
                # TRANSPARENT, not a relocation disarm (live report 2026-07-28:
                # Bowser 2 -> Upstairs vanished mid-run — see this method's
                # docstring). Unlike a strict/waypoint def, a loose def's own
                # route is guaranteed to cross positions it didn't start at
                # (Bowser 2's exit lands in the basement; reaching Upstairs
                # requires passing back through the lobby), so the FIRST
                # anchor anywhere along that route used to kill the attempt
                # with no row and no notice. The arm is left completely
                # untouched — position, start_frame, deadline — exactly like
                # every other mid-route event this matcher already lets pass.
                return closed
            a = self._close(Attempt, d, arm, ev, "reset", None)
            if a:
                closed.append(a)
            self._armed[d.id] = replace(
                arm, progress=0, start_frame=ev.frame,
                started_utc=ev.wall_time_utc, jid=ev.id,
                anchor_type=ev.type, session_id=ev.session_id,
                deadline_frame=self._deadline_for(d, ev),
                level=ctx.level if ctx.level is not None else arm.level,
                area=ctx.area if ctx.area is not None else arm.area)
            return closed
        if not complete and self._matches(d.waypoints[arm.progress], ev, ctx):
            self._armed[d.id] = replace(arm, progress=arm.progress + 1)
            return closed
        return closed   # transparent — the whole feature

    def _flush_move(self, ev, notices) -> None:
        """Judge the position change an EARLIER frame proposed, now that the
        frame has advanced and any co-frame supersession has landed (spec
        2026-08-01-topological-segment-validity).

        Advances `_settled_node` to the last node Mario demonstrably dwelt in
        — not every node an event mentioned, which is what the transient lobby
        would otherwise make it.
        """
        pending = self._pending_move
        if pending is None or pending[0] >= ev.frame:
            return
        frame, node = pending
        self._pending_move = None
        previous, self._settled_node = self._settled_node, node
        if previous is None or node is None or node == previous:
            return
        # An arm that began AT OR AFTER this move cannot have diverged from it
        # — you did not leave a route you had not started. Without this, the
        # one-frame defer would let a warp into a Bowser arena cancel the fight
        # it just armed, and warping somewhere to practise IS the loop.
        candidates = [d for d in self._defs
                      if (arm := self._armed.get(d.id)) is not None
                      and arm.start_frame < frame]
        if not topology.is_legal_move(previous, node):
            # The Usamune warp menu (or a savestate) fabricated this edge, so
            # every movement under way was abandoned rather than run. SILENT:
            # no attempt row, matching the off-route cancel _feed_waypoint
            # already takes — a movement that never happened must not bank a
            # failure. Arming at the DESTINATION is untouched (closures run
            # before arming), which is what keeps the practice loop working.
            for d in candidates:
                self._cancel_topologically(d, ev, notices)
            return
        # Rule 2 splits on match_mode (spec 2026-08-02-strict-path-segments).
        #
        # LOOSE keeps the hop arithmetic below, byte for byte. Anything else
        # takes the PATH CURSOR, because hop arithmetic cannot express the
        # thing Griffin actually asked for: a deliberate shortcut and a
        # runner's mistake are observationally equivalent — entering BitFS
        # during `Bowser 2 -> Upstairs` is the same move whether it is the
        # fastest route or a wrong turn, and that leg survives today only
        # because BitFS happens to sit the same distance from Upstairs as the
        # Basement does. No measurement separates the two; only a DECLARATION
        # does. "That is a fixed path, and there are no other options. I want
        # it to be very strict" (2026-08-02).
        for d in candidates:
            arm = self._armed[d.id]
            if d.match_mode != "loose":
                path = path_nodes(d)
                if arm.path_index >= len(path):
                    # The definition has said everything it is going to say
                    # about places, so it constrains nothing further. An EMPTY
                    # path is the same answer from the start, which is what
                    # keeps the 100-coin family and the reds->pipe defs out of
                    # this rule with no exemption list — the same "two ways to
                    # be unconstrained" property _next_step_hops relies on.
                    continue
                if node == path[arm.path_index]:
                    self._armed[d.id] = replace(arm,
                                                path_index=arm.path_index + 1)
                else:
                    # Not the next step: silently void, through the same door
                    # as every other topological cancel, so a real anchor at
                    # the arm position can still bring it back.
                    self._cancel_topologically(d, ev, notices)
                continue
            # Rule 2 — a LEGAL move that takes the player FURTHER from what the
            # segment needs next. Basement -> LLL is a real edge, so Rule 1
            # waves it through; what makes it a wrong turn is that HMC went
            # from 1 hop away to 2 (live report 2026-08-01). Strict increase
            # only: equal is sideways and tolerated, so a route with two
            # shortest paths is never punished for picking either.
            if node in declared_nodes(d):
                continue      # see declared_nodes: the route says it goes here
            before = self._next_step_hops(d, arm, previous)
            after = self._next_step_hops(d, arm, node)
            if before is not None and after is not None and after > before:
                self._cancel_topologically(d, ev, notices)

    def _cancel_topologically(self, d, ev, notices) -> None:
        """Disarm for a topological reason, REMEMBERING where the arm stood so
        a real anchor there can bring it back (see `self._cancelled`). Every
        other disarm in this engine is final; this one is the only kind the
        player can undo by returning to the start and pressing reset."""
        arm = self._armed.get(d.id)
        if arm is not None:
            self._cancelled[d.id] = (
                arm, ev.frame + budget_frames(self._best_success.get(d.id)))
        self._disarm(d, ev, notices)

    def _next_step_hops(self, d, arm: _Arm, node: str | None) -> int | None:
        """Fewest legal moves from `node` to whatever this definition needs
        NEXT — its next unconsumed waypoint, or its end trigger once every
        waypoint is consumed.

        None means UNCONSTRAINED, and it is the answer that keeps whole
        families of segment out of Rule 2 rather than a list of exemptions
        somebody has to maintain: a step naming no place (`key_grabbed`,
        `warp_entered`, `star_grabbed`, `reset_game`, an unpinned
        `level_exit`) and a place with no directed path both land here.

        A clause-set is an ANY-OF list, so the MINIMUM across its members is
        the distance — but a single member naming no place makes the whole
        step unconstrained, since the player may be heading for that one.
        """
        step = (d.waypoints[arm.progress] if arm.progress < len(d.waypoints)
                else d.end_triggers)
        distances = []
        for clause in step:
            target = step_node(clause)
            if target is None:
                return None
            distance = topology.hops(node, target)
            if distance is None:
                return None
            distances.append(distance)
        return min(distances) if distances else None

    def _disarm(self, d, ev, notices) -> None:
        if self._armed.pop(d.id, None) is not None:
            notices.append({"event": "segment_disarmed", "segment_id": d.id,
                            "name": d.name, "frame": ev.frame})

    def _close(self, Attempt, d, arm: _Arm, ev, outcome, detail):
        # A close event carrying Usamune's own IGT (star/key grab, pipe touch,
        # death) is used verbatim — pause-safe, display-tick aligned, and free
        # of the arm-frame alignment error the wall-frame delta carries; see
        # the module docstring's rta_frames clause. VALID ONLY when Usamune's
        # counter was zeroed on the very frame this segment armed and has not
        # been zeroed since, which is exactly what _last_igt_zero_frame ==
        # arm.start_frame says: otherwise that number counts from a load the
        # segment does not begin at (a def armed mid-level, or a BBH door
        # crossed mid-run), and the wall-frame delta — which at least spans
        # the right two moments — is the honest fallback.
        #
        # WITHIN ONE FRAME, not exactly equal (2026-08-05). A savestate reload
        # emits `spawned` and then its own `practice_reset` on CONSECUTIVE
        # frames -- one load, two events -- so a definition armed by the spawn
        # (Lakitu Skip, and every subsection that starts on becoming
        # controllable) missed this test by exactly one and banked the delta
        # forever. Measured over 31 of his door-ended runs: the anchor lands at
        # arm+1 in 31 of 31, and Usamune's own derived zero sits ON the arm
        # frame in all 31. One frame is the poll's own skew, not slack: a zero
        # from a DIFFERENT load is hundreds of frames away, so nothing this
        # rule exists to reject gets in.
        igt = ev.payload.get("igt_frames")
        if igt is not None and self._last_igt_zero_frame is not None and abs(
                self._last_igt_zero_frame - arm.start_frame) <= IGT_ARM_SKEW_FRAMES:
            rta, timed_by = igt, "igt"
        else:
            # Which branch ran is itself a fact the display needs (ruling 6):
            # a delta counts paused frames and starts a frame off, so it runs
            # ~1-2 frames CHEAP and an identical igt-timed run cannot beat it.
            # Recorded rather than inferred later, because nothing downstream
            # can reconstruct which of the two conditions failed.
            rta, timed_by = ev.frame - arm.start_frame, "delta"
            if rta < 0:
                if outcome == "success":
                    return None  # genuine anomaly: end before arm (self-heal)
                rta = None       # backward jump (game_reset boot frame, earlier savestate): row counts, time unknowable
        if outcome == "success" and rta is not None:
            # Feeds a loose def's staleness budget (spec 2026-07-28-multi-
            # step-segments, see _deadline_for/budget_frames): a MINIMUM, so
            # only ever-faster successes move it, never slower ones.
            prev = self._best_success.get(d.id)
            if prev is None or rta < prev:
                self._best_success[d.id] = rta
        return Attempt(
            id=arm.jid + SEGMENT_ATTEMPT_OFFSET * d.id,
            session_id=arm.session_id, course_id=None, star_id=None,
            strat_tag=None,  # projector fills from its strat memory
            anchor_type=arm.anchor_type, anchor_frame=arm.start_frame,
            outcome=outcome, outcome_detail=detail,
            igt_frames=None, rta_frames=rta,
            started_utc=arm.started_utc, ended_utc=ev.wall_time_utc,
            cleared=False, cleared_reason=None, segment_id=d.id,
            timed_by=timed_by, closed_by=ev.type)
