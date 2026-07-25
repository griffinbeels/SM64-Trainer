"""Segment trigger vocabulary + matcher engine (spec 2026-06-11).

ONE registry: TRIGGERS/GUARDS drive (a) definition validation at the API
boundary, (b) the matcher, (c) GET /api/segments/vocab that renders the
builder GUI. Adding a trigger type = one TriggerType row here (label +
params + the sentence template the builder renders).

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
- anchor closures are POSITION-GATED (segment swap, live report 2026-06-12).
  Each _Arm remembers the MatchContext (level, area) where it armed — the
  segment's start position; a co-frame establishing area_changed pins the
  area for level_changed arms (ctx.area is stale during the level event —
  the area detector establishes one event later on the same tick).
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
  rule — echo invisibility)
- level_changed matching neither start nor end disarms silently (no row);
  area_changed and session_started never record rows
- WAYPOINT-BEARING defs (SegmentDef.waypoints non-empty, spec
  2026-07-23-default-routes-foundation) replace this whole armed-branch chain
  with an ordered-sequence matcher (SegmentEngine._feed_waypoint — see its
  docstring for the full precedence): the def's own start-trigger refire is
  suppressed while armed (progress owns re-arming, not the generic re-arm
  path); a real anchor mid-sequence REWINDS progress to 0 and re-arms in
  place (no row, unlike this chain's reset row); an off-sequence star/key
  grab or wrong-destination level crossing silently cancels (disarm, no
  row) instead of the plain silent level_changed disarm above
- failure rows only on practice_reset/state_loaded (reset), death,
  game_reset (hard_reset); AFK closures (paused >= 150 frames) discard, and
  so do no-op closures (acted_tracking true, mario_acted false — warp/reset
  spam where Mario never moved; mirrors the star-side discard)
- rta_frames = close.frame - start_frame; a would-be-negative value on a
  SUCCESS discards the attempt (end before arm is a genuine anomaly —
  self-heal, domain rule 4), but failure closures record the row with
  rta_frames=None (game_reset's boot-range frame makes this the ONLY way
  hard_reset rows exist).  EXCEPTION — grab closes carry Usamune's IGT: a
  close event with an authoritative igt_frames in its payload (key_grabbed /
  star_collected) records THAT as the time instead of the wall-frame delta,
  so a fight segment matches Usamune's display exactly and stays pause-safe
  (the delta is one display-tick short and counts paused frames; live report
  2026-06-12, Bowser 3 read 0'46"23 vs Usamune 0'46"26).  The grand star
  never fires star_collected (detectors/key.py) — key.py stamps the igt via
  the shared clock (detectors/igt_clock.py).  Valid because every grab-closed
  segment today arms at the level/area load where Usamune resets IGT, so its
  igt IS the segment elapsed; a segment armed mid-level and closed on a grab
  would record Usamune's since-load time, not the since-arm delta (none
  exists; revisit if one is created).  igt_frames on the Attempt stays None —
  segments remain RTA-only to the UI/PB layer; only the rta VALUE changes.
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
  Shapes (1)/(3) are detected by frame equality.  Shape (2) is detected by
  prev_action/action in DOOR_ACTIONS (falling back through the chain) or
  frames_since_door.  Shape (5) is detected by frames_since_dialog recency.
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
from dataclasses import dataclass, field, replace
from typing import Callable

from sm64_events.memory.addresses import (AREA_LOBBY, BOWSER_STAGE_LEVELS,
                                          CASTLE_AREA_NAMES,
                                          CASTLE_REGION_LEVELS,
                                          CASTLE_REGION_NODES,
                                          CASTLE_SECRET_STAR_AREAS,
                                          COURSE_BY_LEVEL, COURSE_NAMES,
                                          DOOR_ACTIONS, LEVEL_CASTLE_INSIDE,
                                          LEVEL_NAMES, node_key, node_label,
                                          region_for_node, star_count,
                                          star_name, world_connections,
                                          world_regions)

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


@dataclass(frozen=True)
class TriggerType:
    key: str
    label: str
    params: dict  # name -> {"kind": "level"|"area"|"course"|"star"|"int", "required": bool}
    template: str  # sentence after the type label: "{to} coming from {from}"
    match: Callable[[dict, object, MatchContext], bool]


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
    TriggerType("level_enter", "You enter level",
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
    TriggerType("level_exit", "You exit level",
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
    TriggerType("area_enter", "You enter area",
                {"level": {"kind": "level", "required": True,
                           "enum": list(CASTLE_REGION_LEVELS)},
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
    TriggerType("warp_entered", "You enter a warp/pipe",
                {"level": {"kind": "level", "required": True}},
                "in {level}",
                lambda p, ev, ctx: ev.type == "warp_entered"
                and ev.payload["level"] == p["level"]),
    TriggerType("key_grabbed", "You grab a Bowser key / grand star",
                # key_grabbed claims all three fight-ending grabs: the Bowser
                # 1/2 keys AND the Bowser 3 grand star (which='grand', level
                # 34) — the grand star never fires star_collected, so a
                # "beat Bowser 3" segment ends HERE, not on star_grabbed.
                # See detectors/key.py.
                {"level": {"kind": "level", "required": False}},
                "in {level}",
                lambda p, ev, ctx: ev.type == "key_grabbed"
                and (p.get("level") is None
                     or ev.payload["level"] == p["level"])),
    TriggerType("star_grabbed", "You grab a star",
                {"course": {"kind": "course", "required": False},
                 "star": {"kind": "star", "required": False}},
                "in {course}, star {star}",
                lambda p, ev, ctx: ev.type == "star_collected"
                and (p.get("course") is None
                     or ev.payload["course_id"] == p["course"])
                and (p.get("star") is None
                     or ev.payload["star_id"] == p["star"])),
    TriggerType("spawned", "You spawn into the game",
                {"level": {"kind": "level", "required": False}},
                "in {level}",
                lambda p, ev, ctx: ev.type == "spawned"
                and (p.get("level") is None
                     or ev.payload["level"] == p["level"])),
    TriggerType("attempt_anchor", "Practice reset / savestate load",
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
                {}, "on F1 or console reset",
                lambda p, ev, ctx: ev.type == "game_reset"),
]}


def arm_level(trig: dict) -> int | None:
    """Level Mario stands in the moment this START trigger arms, or None
    when the trigger carries no (or an unknowable) arm location — reads the
    same param NAMES as the registry rows above, decoupled from the match
    lambdas. Shared by views.py's quick-select banner helpers and the
    projector's segment-target retirement (2026-07-23)."""
    kind = trig.get("type")
    if kind in ("area_enter", "attempt_anchor", "spawned"):
        return trig.get("level")
    if kind in ("level_enter", "level_exit"):
        return trig.get("to")   # level_exit: Mario ends up at the DESTINATION
    return None


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


# --- Segment ORIGIN: where a definition can start (spec 2026-07-24) --------
# Per-trigger source of the arm POSITION, as (level param, subarea param) —
# read as data, exactly like arm_level reads the registry's param names rather
# than its match lambdas. Adding a trigger type to TRIGGERS means adding one
# row here, or accepting None ("Anywhere") by default.
#
# NB this is NOT arm_level's mapping: a level_exit ARMS at its destination but
# ORIGINATES at its source. "SSL -> LLL" is filed under SSL because that is
# what the rule keys on (50 of the 51 seeded exits omit `to`; the one that
# carries it, MIPS Clip, is still filed by its source, which is the point).
_ORIGIN_PARAMS: dict[str, tuple[str, str | None]] = {
    "level_exit": ("from", "from_subarea"),
    "level_enter": ("to", "to_subarea"),
    "area_enter": ("level", "area"),
    "attempt_anchor": ("level", "area"),
    "spawned": ("level", None),
    "warp_entered": ("level", None),
    "key_grabbed": ("level", None),
}

# course id -> its level, for star_grabbed clauses. COURSE_BY_LEVEL is 1:1.
_LEVEL_BY_COURSE = {course: level for level, course in COURSE_BY_LEVEL.items()}

ANYWHERE_LABEL = "Anywhere"


def _star_origin(trig: dict) -> str | None:
    """A star grab places a segment when the star's course does. Course 0
    (castle secret stars) has no level of its own — only the MIPS catches are
    known (CASTLE_SECRET_STAR_AREAS); anything else stays unplaced."""
    course = trig.get("course")
    if course is None:
        return None
    if course == 0:
        area = CASTLE_SECRET_STAR_AREAS.get(trig.get("star"))
        return node_key(LEVEL_CASTLE_INSIDE, area) if area is not None else None
    level = _LEVEL_BY_COURSE.get(course)
    return node_key(level) if level is not None else None


def _clause_origin(trig: dict) -> str | None:
    kind = trig.get("type")
    if kind == "star_grabbed":
        return _star_origin(trig)
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
        if clause.get(name) is not None and not isinstance(clause[name], int):
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
    comment). Unguarded defs always pass; a guarded def arms only inside the
    active route's member set or as the standalone segment target."""
    if not any(g.get("type") == "in_active_route" for g in d.guards):
        return True
    return (d.id in (ctx.route_segments or frozenset())
            or d.id == ctx.target_segment)


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
    }


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

    def armed_ids(self) -> set[int]:
        return set(self._armed)

    def feed(self, ev, ctx: MatchContext):
        """Returns (closed raw Attempts, notices). Closures before arming."""
        from sm64_events.tracking.projection import Attempt  # cycle-free at call time
        closed, notices = [], []
        # Drop spent deferred destination-subarea entries (_pending): once an
        # event at a LATER frame arrives, the entry frame's co-frame area_changed
        # burst is over. Arming/retraction already happened LIVE on those co-frame
        # events (see the area_changed block); here we just retire the entry. An
        # entry that never reached its required area simply never armed.
        for did in list(self._pending):
            if self._pending[did].start_frame < ev.frame:
                del self._pending[did]
        # Track the most recent level/area transition frame BEFORE per-def
        # processing so the echo guard below can test both echo shapes.
        if ev.type in ("level_changed", "area_changed"):
            self._last_transition_frame = ev.frame
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
            if arm is not None and d.waypoints:
                closed.extend(
                    self._feed_waypoint(Attempt, d, arm, ev, ctx, notices))
            elif arm is not None:
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
                elif ev.type in ("level_changed", "session_started") \
                        and not starts:
                    self._disarm(d, ev, notices)   # silent: no row
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
            if starts and (not echo_invisible or relocation_arm) \
                    and not (d.waypoints and d.id in self._armed) \
                    and _route_allows(d, ctx) \
                    and all(GUARDS[g["type"]].check(g, ctx)
                            for g in d.guards
                            if GUARDS[g["type"]].phase == "arm"):
                # A destination-subarea level trigger can't be confirmed yet
                # (the castle lobby loads before the warp settles) — DEFER it
                # into _pending keyed on the required interior area, to be
                # resolved when the co-frame area_changed burst is over. The
                # source subarea (from_subarea) is already in the lambda, so a
                # plain match here arms immediately as before.
                req = (start_clause.get("to_subarea")
                       if ev.type == "level_changed" else None)
                if req is not None:
                    self._pending[d.id] = _Arm(
                        jid=ev.id, start_frame=ev.frame,
                        started_utc=ev.wall_time_utc, anchor_type=ev.type,
                        session_id=ev.session_id, level=ctx.level,
                        area=ctx.area, required_area=req)
                else:
                    fresh = d.id not in self._armed
                    self._armed[d.id] = _Arm(jid=ev.id, start_frame=ev.frame,
                                             started_utc=ev.wall_time_utc,
                                             anchor_type=ev.type,
                                             session_id=ev.session_id,
                                             level=ctx.level, area=ctx.area)
                    if fresh:
                        notices.append({"event": "segment_armed",
                                        "segment_id": d.id, "name": d.name,
                                        "frame": ev.frame})
        return closed, notices

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
        time-stop, or a paused-briefly transition co-frame — rather than a
        real player reset. Moved verbatim out of `feed`'s per-event
        `anchor_is_echo` local (spec 2026-07-23-default-routes-foundation) so
        the waypoint matcher (`_feed_waypoint`) shares the SAME echo
        definition instead of a second copy that could drift; the full
        shape-by-shape rationale lives in the module docstring's "load-echo
        rule" section. Shapes (2a)/(2b)/(3)/(4)/(5) depend only on the event
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
                <= _DIALOG_ECHO_WINDOW))

    def _feed_waypoint(self, Attempt, d, arm: _Arm, ev, ctx, notices) -> list:
        """Ordered-sequence matcher for a waypoint-bearing def (spec
        2026-07-23-default-routes-foundation) — the armed-branch counterpart
        to the plain success/relocation/anchor/death chain above, taken for
        any def carrying d.waypoints instead. Precedence (first match wins):
        end (only once every waypoint is consumed) > death/game_reset >
        session_started (mirrors the plain chain: a session boundary disarms
        silently, no row, regardless of progress — an armed segment must not
        survive across sessions) > echo (invisible, exactly like the plain
        chain) > real anchor (rewinds the sequence to its first waypoint and
        re-arms IN PLACE at the anchor — the practice-retry loop; no row,
        unlike the plain chain's reset row — precise relocation-vs-
        continuation nuance is a live-gate VERIFY item, rewind-in-place is
        the conservative default) > next waypoint (advance `progress`) >
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
            # rewinds the sequence and re-arms in place (retry loop).
            if ev.frame == arm.start_frame or self._anchor_echo(ev):
                return closed
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

    def _disarm(self, d, ev, notices) -> None:
        if self._armed.pop(d.id, None) is not None:
            notices.append({"event": "segment_disarmed", "segment_id": d.id,
                            "name": d.name, "frame": ev.frame})

    def _close(self, Attempt, d, arm: _Arm, ev, outcome, detail):
        # A grab close carries Usamune's authoritative IGT — use it verbatim
        # (pause-safe, display-tick aligned; see the module docstring's
        # rta_frames clause). Non-grab closes (level/warp/reset/death) have no
        # igt_frames -> the wall-frame delta with its negative self-heal.
        igt = ev.payload.get("igt_frames")
        if igt is not None:
            rta = igt
        else:
            rta = ev.frame - arm.start_frame
            if rta < 0:
                if outcome == "success":
                    return None  # genuine anomaly: end before arm (self-heal)
                rta = None       # backward jump (game_reset boot frame, earlier savestate): row counts, time unknowable
        return Attempt(
            id=arm.jid + SEGMENT_ATTEMPT_OFFSET * d.id,
            session_id=arm.session_id, course_id=None, star_id=None,
            strat_tag=None,  # projector fills from its strat memory
            anchor_type=arm.anchor_type, anchor_frame=arm.start_frame,
            outcome=outcome, outcome_detail=detail,
            igt_frames=None, rta_frames=rta,
            started_utc=arm.started_utc, ended_utc=ev.wall_time_utc,
            cleared=False, cleared_reason=None, segment_id=d.id)
