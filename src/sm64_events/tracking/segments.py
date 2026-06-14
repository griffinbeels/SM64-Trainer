"""Segment trigger vocabulary + matcher engine (spec 2026-06-11).

ONE registry: TRIGGERS/GUARDS drive (a) definition validation at the API
boundary, (b) the matcher, (c) GET /api/segments/vocab that renders the
builder GUI. Adding a trigger type = one TriggerType row here (label +
params + the sentence template the builder renders).

Matcher invariants (spec §Matcher semantics — tests are the contract):
- closures (success/failure) process BEFORE arming; one event may close an
  attempt AND re-arm the next (practice_reset in an attempt_anchor segment)
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
  Shapes (1)/(3) are detected by frame equality.  Shape (2) is detected by
  prev_action/action in DOOR_ACTIONS (falling back through the chain) or
  frames_since_door.  Historical events (no prev_action, no frames_since_door):
  .get() returns None -> conservative close behaviour preserved.
  ECHO INVISIBILITY (live regression 2026-06-12): an echo anchor is
  involuntary -- it is INVISIBLE to the engine entirely: no closure, no
  continuation re-arm, no arm-phase arm/re-arm, for every def.  Without
  this, an echo matching an attempt_anchor start trigger rebased the _Arm
  in the arm phase (LBLJ's lobby-door section reset rebased
  start_frame/started_utc to the door, so replay + rta began at the door).
  Shapes (2a)/(2b)/(3) depend only on the event, so they are classified
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
from dataclasses import dataclass, replace
from typing import Callable

from sm64_events.memory.addresses import (CASTLE_AREA_NAMES,
                                          CASTLE_REGION_LEVELS, COURSE_NAMES,
                                          DOOR_ACTIONS, LEVEL_CASTLE_INSIDE,
                                          LEVEL_NAMES, star_count, star_name)

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


@dataclass(frozen=True)
class SegmentDef:
    id: int
    name: str
    enabled: bool
    start_triggers: list
    end_triggers: list
    guards: list


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
                {"to": {"kind": "level", "required": True},
                 "to_subarea": {"kind": "subarea", "required": False,
                                "only_when": _only_castle("to")},
                 "from": {"kind": "level", "required": False},
                 "from_subarea": {"kind": "subarea", "required": False,
                                  "only_when": _only_castle("from")}},
                "{to} {to_subarea} coming from {from} {from_subarea}",
                lambda p, ev, ctx: ev.type == "level_changed" and _real_edge(ev)
                and ev.payload["to"] == p["to"]
                and (p.get("from") is None or ev.payload["from"] == p["from"])
                and (p.get("from_subarea") is None
                     or ev.payload.get("from_area") == p["from_subarea"])),
    TriggerType("level_exit", "You exit level",
                {"from": {"kind": "level", "required": True},
                 "from_subarea": {"kind": "subarea", "required": False,
                                  "only_when": _only_castle("from")},
                 "to": {"kind": "level", "required": False},
                 "to_subarea": {"kind": "subarea", "required": False,
                                "only_when": _only_castle("to")}},
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
    TriggerType("area_enter", "You enter area",
                {"level": {"kind": "level", "required": True,
                           "enum": list(CASTLE_REGION_LEVELS)},
                 "area": {"kind": "subarea", "required": False,
                          "only_when": _only_castle("level")}},
                "{level} {area}",
                lambda p, ev, ctx: ev.type == "area_changed" and _real_edge(ev)
                and ev.payload["level"] == p["level"]
                and (p.get("area") is None or ev.payload["to"] == p["area"])),
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
]}


@dataclass(frozen=True)
class GuardType:
    key: str
    label: str
    params: dict
    template: str
    check: Callable[[dict, MatchContext], bool]


GUARDS: dict[str, GuardType] = {g.key: g for g in [
    GuardType("prev_level", "Previous level was",
              {"level": {"kind": "level", "required": True}},
              "{level}",
              lambda p, ctx: ctx.prev_level == p["level"]),
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
    guards = d.get("guards") or []
    if not isinstance(guards, list):
        raise ValueError("guards must be a list")
    for g in guards:
        _check_clause(g, GUARDS, "guards")


def vocab() -> dict:
    """Registry serialized for the builder GUI — the UI renders from this."""
    return {
        "triggers": [{"key": t.key, "label": t.label, "params": t.params,
                      "template": t.template} for t in TRIGGERS.values()],
        "guards": [{"key": g.key, "label": g.label, "params": g.params,
                    "template": g.template} for g in GUARDS.values()],
        "levels": {str(k): v for k, v in sorted(LEVEL_NAMES.items())},
        "castle_areas": {str(k): v for k, v in CASTLE_AREA_NAMES.items()},
        "courses": {str(k): v for k, v in COURSE_NAMES.items()},
        # star_id order; star_count/star_name (addresses.py) own the
        # 100-coin-star rule for courses 1-15
        "stars": {str(cid): [star_name(cid, s)
                             for s in range(star_count(cid))]
                  for cid in COURSE_NAMES},
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


def _at_arm_position(arm: _Arm, ctx: MatchContext) -> bool:
    """True when the tracked position matches where the segment armed.
    None on either side = unknown → match, so legacy journals (no
    level/area events) keep the unconditional continuation behavior."""
    return ((arm.level is None or ctx.level is None or ctx.level == arm.level)
            and (arm.area is None or ctx.area is None or ctx.area == arm.area))


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
        # Event-level echo classification — shapes (2a)/(2b)/(3) depend only
        # on the event payload + _last_transition_frame, never on a per-def
        # arm, so classify ONCE before the loop.  An echo anchor is
        # involuntary — it must be INVISIBLE to the engine entirely: no
        # closure, no continuation re-arm, no arm-phase arm/re-arm, for
        # every def (live regression 2026-06-12: the lobby door's section
        # reset matched LBLJ's attempt_anchor start trigger in the ARM phase
        # and rebased start_frame to the door).  Boolean OR — door evidence
        # and the pause-gated co-frame shape are independent, so order is
        # irrelevant here; the docstring taxonomy keys each shape.
        anchor_is_echo = ev.type in _ANCHOR_TYPES and (
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
            or ev.payload.get("save_pending", False))
        for d in self._defs:
            arm = self._armed.get(d.id)
            start_clause = self._first_match(d.start_triggers, ev, ctx)
            starts = start_clause is not None
            if arm is not None:
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
            if starts and (not echo_invisible or relocation_arm) \
                    and all(GUARDS[g["type"]].check(g, ctx)
                            for g in d.guards):
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
