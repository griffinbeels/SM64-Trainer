# src/sm64_events/tracking/synthesize.py
"""What just happened -> a trigger clause (spec 2026-07-28-multi-step-
segments, Task 12). Task 10 (`eventlabel.py`) turns one journal row into a
sentence a human can point at; Task 11 serves those rows to a picker. This
module is the hinge: it turns the TWO rows a user pointed at (a start moment,
an end moment) back into the clause pair `SegmentDef.start_triggers`/
`end_triggers` actually store, so a segment can be defined by DOING it
instead of by hand-authoring TRIGGERS clauses in the builder.

`clause_for(row, role)` reads one `EventRow` (storage/db.py) plus which end of
the definition it is being asked to fill ("start" or "end") and returns a
TRIGGERS-shaped clause dict, or None when this row's type carries no
synthesis rule for that role, or a well-typed but underfilled payload (a
legacy row, an establishing/corrective bookkeeping event). `synthesize`
composes both ends and additionally refuses a pair that would be unfireable.

THE ASYMMETRY (the one case this module MUST get backwards-proof, per
segments.py's documented COROLLARY: a def whose start and end are satisfied
by the SAME event arms on the event that should close it and can never record
an attempt). A single `level_changed {from, to}` journal event means a
DIFFERENT clause depending which end of the definition it is filling:
  - as a START it becomes `level_exit{from}` with `to` DELIBERATELY UNPINNED
    -- 52 of the 53 seeded `level_exit` clauses omit `to` because every real
    course exit lands in the castle, and pinning it here would make a
    recorded movement match only the one landing this particular exit
    happened to have.
  - as an END it becomes `level_enter{to}`, pinned.
Getting this backwards (or symmetric) reproduces the shape that shipped
broken 2026-07-24: `level_exit{from:A}` (unpinned) + `level_enter{to:B}`
(pinned) both satisfied by ONE `level_changed{from:A,to:B}` event whenever
A->B is a direct edge.

THE ID-SPACE TRAP applies here exactly as `eventlabel.py`'s docstring
describes it (read that module first) -- `star_collected` carries a COURSE
id, everything else here carries a LEVEL id, and the two numbering systems
only coincidentally agree at id 15 (Rainbow Ride). `clause_for` never needs
to NAME anything (it copies raw ids straight out of the payload), but
`suggest_name` does, through the same canonical accessors
(LEVEL_NAMES/CASTLE_AREA_NAMES/course_name/star_name) `eventlabel.py` uses --
never COURSE_NAMES for a level id or vice versa.

WHY `attempt_anchor` HAS NO SYNTHESIS ROW. `TRIGGERS["attempt_anchor"]`
requires a `level` (and optionally an `area`), but `practice_reset`/
`state_loaded` payloads carry no level/course at all -- segments.py's own
docstring on those event types says the matcher resolves WHERE an
attempt_anchor fires from LIVE MatchContext state, not from the event's own
payload. `clause_for` only ever sees one row in isolation (same limitation
`label_event` documents), so it has no way to recover that position. This is
a real gap in what recording-a-boundary can do, not an oversight: filed in
`_NOT_SYNTHESIZABLE` rather than a row that would need to invent a level.
`reset_game` looks like it should share this problem (its journal source,
`game_reset`, ALSO carries no level) but `TRIGGERS["reset_game"]` takes NO
params at all -- `{"type": "reset_game"}` needs nothing from the payload, so
it synthesizes trivially.

`_SYNTH_PARAMS` is a table with one row per synthesizable TriggerType key,
read as data -- the same registry shape as `_ORIGIN_PARAMS`/
`_PRECONDITION_PARAM` in segments.py, so a new trigger type is one row here
or an explicit addition to `_NOT_SYNTHESIZABLE`.
`test_every_trigger_type_has_a_synthesis_row_or_is_explicitly_unreachable`
keeps the two sets covering `TRIGGERS` exactly.
"""
from sm64_events.memory.addresses import (CASTLE_AREA_NAMES, LEVEL_NAMES,
                                          course_name, node_short_label,
                                          star_name)
from sm64_events.tracking import topology
from sm64_events.tracking.segments import (_ORIGIN_PARAMS, _real_edge,
                                           step_node)

# --- per-journal-type param builders -----------------------------------
# Each takes the row's PAYLOAD only (the registry below already picked the
# right role) and returns the clause's params, or None when the payload
# lacks what this shape needs -- a legacy row, or (for level/area) an
# establishing/corrective bookkeeping event (from == to; same reasoning
# eventlabel.py's _level_changed/_area_changed guard against).

def _level_changed_start(payload: dict) -> dict | None:
    from_id, to_id = payload.get("from"), payload.get("to")
    if from_id is None or to_id is None or from_id == to_id:
        return None
    return {"from": from_id}  # `to` stays unpinned -- see module docstring.


def _level_changed_end(payload: dict) -> dict | None:
    from_id, to_id = payload.get("from"), payload.get("to")
    if from_id is None or to_id is None or from_id == to_id:
        return None
    return {"to": to_id}


def _area_changed_params(payload: dict) -> dict | None:
    level, to_area, from_area = (payload.get("level"), payload.get("to"),
                                  payload.get("from"))
    if level is None or to_area is None or from_area == to_area:
        return None
    params = {"level": level, "area": to_area}
    # A transient source (every castle entry passes through the lobby
    # transiently before settling -- detectors/area.py) is exactly what
    # area_enter's own match lambda unconditionally rejects when `from` is
    # pinned (segments.py). Pinning one here would record a clause that can
    # never match the very shape it came from.
    if from_area is not None and not payload.get("from_transient", False):
        params["from"] = from_area
    return params


def _star_collected_params(payload: dict) -> dict | None:
    course_id, star_id = payload.get("course_id"), payload.get("star_id")
    if course_id is None or star_id is None:
        return None
    return {"course": course_id, "star": star_id}


def _level_field_params(payload: dict) -> dict | None:
    # warp_entered / key_grabbed / spawned all pin the SAME single field --
    # TRIGGERS gives each of them exactly one param, named "level".
    level = payload.get("level")
    if level is None:
        return None
    return {"level": level}


def _no_params(payload: dict) -> dict:
    return {}  # TRIGGERS["reset_game"] takes none.


# trig key -> {journal_type, role, build}. `role` is None when the clause is
# equally valid at either end (a castle secret star can open a movement OR
# close one; see tools/corpus_movements.py's own "may start on a star_grabbed
# but must never end on one" convention -- that is an AUTHORING choice for
# the seeded corpus, not a structural restriction SegmentDef enforces, so
# this registry does not bake it in). Only level_changed is role-restricted,
# because it is the one journal type that means two DIFFERENT trigger types
# depending which end is being filled -- see the ASYMMETRY section above.
_SYNTH_PARAMS: dict[str, dict] = {
    "level_exit": {"journal_type": "level_changed", "role": "start",
                   "build": _level_changed_start},
    "level_enter": {"journal_type": "level_changed", "role": "end",
                    "build": _level_changed_end},
    "area_enter": {"journal_type": "area_changed", "role": None,
                   "build": _area_changed_params},
    "warp_entered": {"journal_type": "warp_entered", "role": None,
                     "build": _level_field_params},
    "key_grabbed": {"journal_type": "key_grabbed", "role": None,
                    "build": _level_field_params},
    "star_grabbed": {"journal_type": "star_collected", "role": None,
                     "build": _star_collected_params},
    "spawned": {"journal_type": "spawned", "role": None,
                "build": _level_field_params},
    "reset_game": {"journal_type": "game_reset", "role": None,
                   "build": _no_params},
}

# attempt_anchor's required `level` lives in live MatchContext, not in a
# practice_reset/state_loaded payload -- see the module docstring section
# above. The completeness test
# (test_every_trigger_type_has_a_synthesis_row_or_is_explicitly_unreachable)
# is what keeps this set honest against a NEW TriggerType landing with
# neither a row here nor an entry below.
_NOT_SYNTHESIZABLE = frozenset({"attempt_anchor"})


def clause_for(row, role: str) -> dict | None:
    """row: an EventRow (storage/db.py) -- .type and .payload are read.
    role: "start" or "end" -- which end of the definition this clause fills.

    Returns a TRIGGERS-shaped clause dict, or None when row.type has no
    synthesis rule for this role (including every type in
    _NOT_SYNTHESIZABLE) or its payload lacks the field(s) a well-formed
    event of that type would carry.
    """
    for trig_key, spec in _SYNTH_PARAMS.items():
        if spec["journal_type"] != row.type:
            continue
        if spec["role"] is not None and spec["role"] != role:
            continue
        params = spec["build"](row.payload)
        if params is None:
            return None
        return {"type": trig_key, **params}
    return None


def _step_clause(node: str, via_level_edge: bool) -> dict:
    """The clause that requires standing at `node`, in the form that can
    actually ARM there.

    This is the authoring trap the strict-path spec named, absorbed rather
    than explained: a castle subarea reached by a LEVEL edge must be a
    subarea-pinned `level_enter` and NOT an `area_enter`, because
    `segments.can_run_from` rule (A) refuses to arm a definition whose next
    step is an `area_enter` while Mario stands outside the castle. Two clause
    types that describe the same room, one of which silently never arms —
    exactly the distinction a person authoring by hand has no way to know
    they got wrong, and exactly the one the journal answers for free (did a
    `level_changed` land on this frame?).
    """
    level_str, _, area_str = node.partition(":")
    level = int(level_str)
    if not area_str:
        return {"type": "level_enter", "to": level}
    if via_level_edge:
        return {"type": "level_enter", "to": level, "to_subarea": int(area_str)}
    return {"type": "area_enter", "level": level, "area": int(area_str)}


def walked_steps(rows, start_row, end_row) -> list[dict]:
    """The places actually walked through between two picked journal rows —
    the candidate STEPS of the definition those two rows would define.

    The path is already recorded; the recorder just was not reading it. Every
    settled position between the two ends is in the journal, so a movement can
    be defined by DOING it rather than by hand-authoring an ordered clause list
    against a trigger vocabulary — which is the difference between the user
    being able to make `WF → SSL` himself and not (his own bar, in the spec:
    "It needs to be easy for me to have, theoretically, made the WF→SSL
    segment on my own as a user").

    Reads `area_changed` and NOT `level_changed`, and takes the LAST candidate
    per frame — both borrowed deliberately from `SegmentEngine.feed`, whose own
    comments carry the evidence: an area payload names the level AND the
    settled area outright where a level payload's context is still the old
    level's, and every castle entry loads the Lobby for one poll before warping
    to the real area, ALL ON ONE FRAME. Judged raw, that transient Lobby is a
    stop the player never made. This is the one-frame defer, applied to
    authoring instead of to matching, so what gets written down and what gets
    enforced collapse the same walk the same way.

    The arm position is dropped unconditionally — the first settled node is
    where the start trigger left you, the spec's "the start is implicit". The
    END is dropped by IDENTITY, never by position: a `level_changed` end row
    sits one id BEFORE its own co-frame `area_changed`, so the destination is
    outside the span entirely and the last walked node is a real step. Dropping
    a trailing node blindly ate the Basement out of `WF → SSL` — the one route
    this whole feature is measured against — while leaving four-step arena
    routes looking perfect, which is the shape of a bug that ships. Compared
    through `segments.step_node`, the same reader the matcher judges positions
    with, so "is this the end" has one answer.

    What remains is the intermediate stops, which is precisely what
    `SegmentDef.waypoints` holds.

    Returns one dict per step: `node` (world key), `label` (its short form,
    the same one the practice card's step track draws), and `clause`. The
    CALLER decides which are required — a walk contains detours and rooms you
    merely crossed, and only the person who did it knows which was which.
    """
    # Sorted by id here rather than trusting the caller: ORDER is the entire
    # content of the answer, and a caller handing over a dict's values or a
    # filtered list is one refactor away from silently reordering the route.
    span = sorted((row for row in rows
                   if start_row.id <= row.id <= end_row.id),
                  key=lambda row: row.id)
    level_frames = {row.frame for row in span
                    if row.type == "level_changed" and _real_edge(row)}
    settled: list[tuple[int, str]] = []
    for row in span:
        if row.type != "area_changed":
            continue
        node = topology.node_for(row.payload.get("level"),
                                 row.payload.get("to"))
        if node is None:                      # position unknown; cannot judge
            continue
        if settled and settled[-1][0] == row.frame:
            settled[-1] = (row.frame, node)   # last candidate of the frame wins
        else:
            settled.append((row.frame, node))
    walk = [entry for index, entry in enumerate(settled)
            if index == 0 or entry[1] != settled[index - 1][1]][1:]
    end_clause = clause_for(end_row, "end")
    end_node = step_node(end_clause) if end_clause else None
    while walk and end_node is not None and walk[-1][1] == end_node:
        walk.pop()
    return [{"node": node,
             "label": node_short_label(node),
             "clause": _step_clause(node, frame in level_frames)}
            for frame, node in walk]


def synthesize(start_row, end_row) -> tuple[dict, dict] | None:
    """Turn two picked journal rows into a (start_clause, end_clause) pair,
    or None when either row can't be turned into a clause for its end, OR
    when the pair would be UNFIREABLE.

    The unfireable check reads the two rows' own journal ids rather than
    reproducing test_defaults_corpus.py's BFS world-graph walk (appropriate
    there for hand-authored corpus rows, and explicitly documented as
    untestable against anything but the topology table it came from). A
    RECORDED definition has better ground truth available: if the start row
    and the end row are literally the same journal event, the def would arm
    and close on the identical tick -- segments.py's documented COROLLARY --
    and that is directly observable as `start_row.id == end_row.id`, no
    topology needed. This catches the literal degenerate selection (picking
    one row for both ends); it does not attempt to prove every OTHER
    unfireable pair a direct world edge could produce, which is what the
    corpus-side BFS check exists for and what SegmentDef must keep tolerating
    regardless (a stored def has to keep matching fabricated warp-menu edges,
    per segments.py's own `can_run_from` docstring).
    """
    if start_row.id == end_row.id:
        return None
    start_clause = clause_for(start_row, "start")
    end_clause = clause_for(end_row, "end")
    if start_clause is None or end_clause is None:
        return None
    return start_clause, end_clause


# --- suggest_name --------------------------------------------------------
# A recognisable starting point for the definition's name -- the user renames
# it, so this only has to read like the movement, not describe it precisely.

_NO_PLACE_LABEL = "Anywhere"


def _place_name(clause: dict) -> str:
    """The place a single clause names, through the SAME per-type param
    registry (_ORIGIN_PARAMS) segments.py's start_origin reads to answer
    "where does this clause happen" -- reusing it here rather than
    redefining the trig-key -> (level_param, area_param) mapping a second
    time, which is exactly the kind of quiet divergence risk the ID-SPACE
    TRAP has already cost this codebase twice. star_grabbed and reset_game
    are handled separately because _ORIGIN_PARAMS does not cover them
    (star_grabbed's place is course-shaped, not level-shaped; reset_game
    names no place at all).
    """
    kind = clause.get("type")
    if kind == "star_grabbed":
        course = clause.get("course")
        if course is None:
            return "a star"
        name = course_name(course)
        star = clause.get("star")
        return f"{name} ({star_name(course, star)})" if star is not None \
            else name
    if kind == "reset_game":
        return "Reset"
    params = _ORIGIN_PARAMS.get(kind)
    if params is None:
        return _NO_PLACE_LABEL
    level_param, area_param = params
    level = clause.get(level_param)
    if level is None:
        return _NO_PLACE_LABEL
    name = LEVEL_NAMES.get(level, f"Level {level}")
    area = clause.get(area_param) if area_param else None
    if area is not None:
        name = f"{name} ({CASTLE_AREA_NAMES.get(area, f'Area {area}')})"
    return name


def suggest_name(start_clause: dict, end_clause: dict) -> str:
    return f"{_place_name(start_clause)} → {_place_name(end_clause)}"
