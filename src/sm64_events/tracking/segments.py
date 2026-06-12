"""Segment trigger vocabulary + matcher engine (spec 2026-06-11).

ONE registry: TRIGGERS/GUARDS drive (a) definition validation at the API
boundary, (b) the matcher, (c) GET /api/segments/vocab that renders the
builder GUI. Adding a trigger type = one TriggerType row here.

Matcher invariants (spec §Matcher semantics — tests are the contract):
- closures (success/failure) process BEFORE arming; one event may close an
  attempt AND re-arm the next (practice_reset in an attempt_anchor segment)
- anchor closures are attempt BOUNDARIES, not state changes: a real
  practice_reset/state_loaded closes the current attempt AND re-arms the
  same segment at the anchor frame (practice-loop continuation — Usamune
  respawns at the level's last entrance, which is the segment's start
  position; live-gate amendment 2026-06-12). The segment never stops being
  armed; the UI chip stays lit.
- guards re-evaluate on EVERY arm and re-arm
- re-firing a start trigger while armed re-arms (timer restarts, no row);
  a refire whose guards FAIL leaves the existing arm untouched (the old
  start_frame keeps running)
- level_changed matching neither start nor end disarms silently (no row);
  area_changed and session_started never record rows
- failure rows only on practice_reset/state_loaded (reset), death,
  game_reset (hard_reset); AFK closures (paused >= 150 frames) discard
- rta_frames = close.frame - start_frame; a would-be-negative value on a
  SUCCESS discards the attempt (end before arm is a genuine anomaly —
  self-heal, domain rule 4), but failure closures record the row with
  rta_frames=None (game_reset's boot-range frame makes this the ONLY way
  hard_reset rows exist)
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
             but Usamune IGT resets -> anchor fires in DOOR_ACTION
             (0x1320/0x1321/0x1322, inputs locked, never a player reset).
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
  Shapes (1)/(3) are detected by frame equality.  Shape (2) is detected by
  prev_action/action in DOOR_ACTIONS (falling back through the chain) or
  frames_since_door.  Historical events (no prev_action, no frames_since_door):
  .get() returns None -> conservative close behaviour preserved.
  KNOWN EDGE (no code): a savestate load INTO A DIFFERENT AREA emits a
  corrective area_changed co-frame with state_loaded; that state_loaded will
  be classified as a co-frame echo if paused_frames_before <= 5.  The
  negative-rta self-heal covers the time-jump consequences.  Acceptable: door
  echoes are constant, this edge is rare.
"""
from dataclasses import dataclass
from typing import Callable

from sm64_events.memory.addresses import CASTLE_AREA_NAMES, DOOR_ACTIONS, LEVEL_NAMES

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
    match: Callable[[dict, object, MatchContext], bool]


def _real_edge(ev) -> bool:
    # establishing/corrective level & area events may carry from == to;
    # those are bookkeeping, not movement — never an anchor.
    return ev.payload.get("from") != ev.payload.get("to")


TRIGGERS: dict[str, TriggerType] = {t.key: t for t in [
    TriggerType("level_enter", "You enter level",
                {"to": {"kind": "level", "required": True},
                 "from": {"kind": "level", "required": False}},
                lambda p, ev, ctx: ev.type == "level_changed" and _real_edge(ev)
                and ev.payload["to"] == p["to"]
                and (p.get("from") is None or ev.payload["from"] == p["from"])),
    TriggerType("level_exit", "You exit level",
                {"from": {"kind": "level", "required": True},
                 "to": {"kind": "level", "required": False}},
                lambda p, ev, ctx: ev.type == "level_changed" and _real_edge(ev)
                and ev.payload["from"] == p["from"]
                and (p.get("to") is None or ev.payload["to"] == p["to"])),
    TriggerType("area_enter", "You enter area",
                {"level": {"kind": "level", "required": True},
                 "area": {"kind": "area", "required": True}},
                lambda p, ev, ctx: ev.type == "area_changed" and _real_edge(ev)
                and ev.payload["level"] == p["level"]
                and ev.payload["to"] == p["area"]),
    TriggerType("warp_entered", "You enter a warp/pipe",
                {"level": {"kind": "level", "required": True}},
                lambda p, ev, ctx: ev.type == "warp_entered"
                and ev.payload["level"] == p["level"]),
    TriggerType("key_grabbed", "You grab a Bowser key",
                {"level": {"kind": "level", "required": False}},
                lambda p, ev, ctx: ev.type == "key_grabbed"
                and (p.get("level") is None
                     or ev.payload["level"] == p["level"])),
    TriggerType("star_grabbed", "You grab a star",
                {"course": {"kind": "course", "required": False},
                 "star": {"kind": "star", "required": False}},
                lambda p, ev, ctx: ev.type == "star_collected"
                and (p.get("course") is None
                     or ev.payload["course_id"] == p["course"])
                and (p.get("star") is None
                     or ev.payload["star_id"] == p["star"])),
    TriggerType("spawned", "You spawn into the game",
                {"level": {"kind": "level", "required": False}},
                lambda p, ev, ctx: ev.type == "spawned"
                and (p.get("level") is None
                     or ev.payload["level"] == p["level"])),
    TriggerType("attempt_anchor", "Practice reset / savestate load in level",
                {"level": {"kind": "level", "required": True},
                 "area": {"kind": "area", "required": False}},
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
    check: Callable[[dict, MatchContext], bool]


GUARDS: dict[str, GuardType] = {g.key: g for g in [
    GuardType("prev_level", "Previous level was",
              {"level": {"kind": "level", "required": True}},
              lambda p, ctx: ctx.prev_level == p["level"]),
    GuardType("star_count_min", "Star count at least",
              {"n": {"kind": "int", "required": True}},
              # historical events without num_stars conservatively FAIL
              lambda p, ctx: ctx.num_stars is not None
              and ctx.num_stars >= p["n"]),
    GuardType("star_count_max", "Star count at most",
              {"n": {"kind": "int", "required": True}},
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
        "triggers": [{"key": t.key, "label": t.label, "params": t.params}
                     for t in TRIGGERS.values()],
        "guards": [{"key": g.key, "label": g.label, "params": g.params}
                   for g in GUARDS.values()],
        "levels": {str(k): v for k, v in sorted(LEVEL_NAMES.items())},
        "castle_areas": {str(k): v for k, v in CASTLE_AREA_NAMES.items()},
    }


@dataclass(frozen=True)
class _Arm:
    jid: int            # journal id of the arming event -> attempt id
    start_frame: int
    started_utc: str
    anchor_type: str    # the arming event's type
    session_id: int


class SegmentEngine:
    """One IDLE<->ARMED FSM per enabled definition. Pure over journal
    events + MatchContext: same code path live and in replay."""

    def __init__(self, defs: list[SegmentDef]):
        self._defs = [d for d in defs if d.enabled]
        self._armed: dict[int, _Arm] = {}
        # Updated to ev.frame on every level_changed / area_changed BEFORE the
        # per-def loop.  Transition events always journal before their same-tick
        # synthetic practice_reset (detector order in main.py guarantees it),
        # so this is always set when the echo arrives.
        self._last_transition_frame: int | None = None

    def armed_ids(self) -> set[int]:
        return set(self._armed)

    def feed(self, ev, ctx: MatchContext):
        """Returns (closed raw Attempts, notices). Closures before arming."""
        from sm64_events.tracking.projection import Attempt  # cycle-free at call time
        closed, notices = [], []
        # Track the most recent level/area transition frame BEFORE per-def
        # processing so the echo guard below can test both echo shapes.
        if ev.type in ("level_changed", "area_changed"):
            self._last_transition_frame = ev.frame
        for d in self._defs:
            arm = self._armed.get(d.id)
            starts = self._matches(d.start_triggers, ev, ctx)
            if arm is not None:
                if self._matches(d.end_triggers, ev, ctx):
                    a = self._close(Attempt, d, arm, ev, "success", None)
                    if a:
                        closed.append(a)
                    self._disarm(d, ev, notices)
                elif ev.type in ("practice_reset", "state_loaded") \
                        and ev.frame == arm.start_frame:
                    # Shape (1) — arm-frame echo: the level_changed that armed
                    # this segment and the synthetic anchor it triggers share
                    # the same global-timer tick.  Suppressed UNCONDITIONALLY:
                    # the player may have been paused on the grounds for
                    # minutes before entering the lobby — a large
                    # paused_frames_before here is normal and must not
                    # reclassify this as a real reset.
                    # (live gate 2026-06-12, seq 40-45)
                    pass
                elif ev.type in ("practice_reset", "state_loaded") \
                        and ev.frame == self._last_transition_frame \
                        and ev.payload.get("paused_frames_before", 0) \
                        <= _MENU_PAUSE_FRAMES:
                    # Shape (3) — transition co-frame echo (walked area door):
                    # area_changed mid-segment at frame F, then anchor at F.
                    # Suppressed only when paused_frames_before <= 5.
                    # Menu warps (06-01-00, etc.) are co-frame BUT pass through
                    # the pause menu: paused_frames_before 13-890 observed
                    # in live logs (2026-06-12).  A co-frame anchor with a
                    # large pause is a deliberate attempt boundary, not a
                    # load echo — falls through to the real-reset path below.
                    # (live report 2026-06-12: LBLJ armed in castle lobby
                    # closed-as-reset when crossing the basement-stairs door)
                    pass
                elif ev.type in ("practice_reset", "state_loaded") \
                        and ev.payload.get(
                            "prev_action",
                            ev.payload.get("action")) in DOOR_ACTIONS:
                    # Intra-area door echo (shape c): no area_changed fires
                    # (same area on both sides of the door), but Usamune still
                    # resets IGT → anchor fires with Mario in a door action
                    # (0x1320/0x1321/0x1322). Inputs are locked during door
                    # animations, so this can never be a player reset.
                    # prev_action is authoritative when present: the door open
                    # animation must have been running on the PREVIOUS tick too
                    # (a real L-reset has prev_action = gameplay, not a door
                    # action — even if curr is 0x1322 due to poll-tick race).
                    # Fall back to action for events without prev_action
                    # (journaled before this field was added). Missing both
                    # fields → .get() chain returns None → not in DOOR_ACTIONS
                    # → conservative close behaviour preserved.
                    pass
                elif ev.type in ("practice_reset", "state_loaded") \
                        and ev.payload.get("frames_since_door") is not None \
                        and 0 <= ev.payload["frames_since_door"] \
                        <= _DOOR_ECHO_WINDOW:
                    # Non-warp door recency echo (shape d): ACT_PULLING/
                    # PUSHING_DOOR ends the section AFTER the animation —
                    # IGT resets 1-5 frames later with Mario already idle/
                    # landing, so action and prev_action carry no door
                    # context.  frames_since_door bridges the gap.
                    # Historical events (no frames_since_door key): .get()
                    # returns None → falls through to conservative close.
                    pass
                elif ev.type in ("practice_reset", "state_loaded"):
                    if ev.payload.get("paused_frames_before", 0) \
                            < _AFK_PAUSE_FRAMES:
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
                    self._armed[d.id] = _Arm(
                        jid=ev.id, start_frame=ev.frame,
                        started_utc=ev.wall_time_utc,
                        anchor_type=ev.type,
                        session_id=ev.session_id,
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
            # arm / re-arm — guards re-evaluated every time (spec)
            if starts and all(GUARDS[g["type"]].check(g, ctx)
                              for g in d.guards):
                fresh = d.id not in self._armed
                self._armed[d.id] = _Arm(jid=ev.id, start_frame=ev.frame,
                                         started_utc=ev.wall_time_utc,
                                         anchor_type=ev.type,
                                         session_id=ev.session_id)
                if fresh:
                    notices.append({"event": "segment_armed",
                                    "segment_id": d.id, "name": d.name,
                                    "frame": ev.frame})
        return closed, notices

    def _matches(self, triggers, ev, ctx) -> bool:
        return any(TRIGGERS[t["type"]].match(t, ev, ctx) for t in triggers)

    def _disarm(self, d, ev, notices) -> None:
        if self._armed.pop(d.id, None) is not None:
            notices.append({"event": "segment_disarmed", "segment_id": d.id,
                            "name": d.name, "frame": ev.frame})

    def _close(self, Attempt, d, arm: _Arm, ev, outcome, detail):
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
