# src/sm64_events/tracking/eventlabel.py
"""One journal event -> one sentence a human can point at (spec
2026-07-28-multi-step-segments, Task 10). This is the first piece of the
feature the user actually asked for: define a segment by pointing at what
you just did, instead of hand-authoring trigger clauses. `label_event(row)`
turns a single `EventRow` (storage/db.py) into that sentence, or `None` when
the event is not something a human would ever pick as a segment boundary —
the timeline (a later task) filters `None` rows out entirely rather than
rendering them.

The journal carries 30+ distinct event types and most of them are per-tick
telemetry (`rollout`, `jump`, `mario_acted` — fired on every first non-passive
action, carrying only the inert action id) or derived bookkeeping (`attempt_completed`,
`target_changed`, `session_started`) that nobody would ever call "the step I
just did". `label_event` only recognizes the types this module's sibling
registry (`tracking/segments.py::TRIGGERS`) can actually turn into a segment
clause — see `test_every_trigger_type_has_a_labellable_event_shape` in
test_eventlabel.py, which fails the day a new `TriggerType` lands whose
underlying journal event this module has no rule for (a trigger the timeline
cannot produce is a segment the user cannot record).

`ui/components/feed.js` stays a raw diagnostics view over the same journal
and is DELIBERATELY NOT migrated onto this module. Showing every raw event
including the ones this module calls noise is exactly feed.js's job as a
developer diagnostic; a segment-boundary picker has the opposite job.

THE ID-SPACE TRAP (measured against the shipped tables, ledger 2026-07-28
— read this before touching any formatter below). A *level* id and a
*course* id are DIFFERENT NUMBERING SYSTEMS that happen to agree at id 15
(LEVEL_NAMES[15] == COURSE_NAMES[15] == "Rainbow Ride") and disagree
everywhere else that matters — e.g. LEVEL_NAMES[9] is "Bob-omb Battlefield"
but COURSE_NAMES[9] is "Dire, Dire Docks". Swapping the tables produces a
PLAUSIBLE WRONG PLACE NAME, never a crash and never an empty string, so a
spot check on Rainbow Ride (id 15) would pass either way.
`level_changed`/`area_changed`/`warp_entered`/`spawned`/`key_grabbed` all
carry LEVEL ids (`detectors/level.py`, `area.py`, `warp.py`, `spawn.py`,
`key.py`) and are named ONLY through `LEVEL_NAMES`/`CASTLE_AREA_NAMES`.
`star_collected` carries a COURSE id and is named ONLY through the canonical
`course_name(course_id)`/`star_name(course_id, star_id)` accessors — never by
indexing `COURSE_NAMES`/`STAR_NAMES` directly (`STAR_NAMES[course]` is a
TUPLE positioned by star_id, not a dict; this has already cost two wrong
assumptions elsewhere in the codebase). `test_level_id_and_course_id_
are_different_number_spaces` pins id 9, where the two tables disagree, so a
table swap fails loudly rather than merely being wrong on a place nobody
spot-checks.

THE LABELLING-VOLUME DECISION (the judgement call this task's brief
deliberately left open, quantified against the real journal — 18,656 events,
2026-07-28). Of the 9 journal event types this module CAN label, three
dominate by raw volume: `practice_reset` (2,829), `area_changed` (1,678),
`spawned` (1,164) — 76% of the "capable" set. The four types a human would
call "a step I just performed" total only 1,780 (`level_changed` 925 +
`star_collected` 635 + `warp_entered` 199 + `key_grabbed` 21).

This module labels ALL NINE anyway, regardless of volume — DECISION (b) from
the brief's three sketched shapes, "label everything and let the timeline's
default view narrow it". The alternative of dropping the high-volume types
here was never viable: `practice_reset`/`state_loaded` back the seeded LBLJ
definition's `attempt_anchor` start trigger, and `area_changed`/`spawned`
back real `area_enter`/`spawned` start triggers in the same corpus — making
any of them unlabellable would make an existing definition's start
un-recordable through this feature, which is a correctness regression dressed
up as a display preference. `label_event`'s job is "CAN this be a boundary",
not "should the default view show it" — that is a volume/recency/grouping
question for whichever later task builds the timeline endpoint (candidate
shapes already on the table: a recency cap, a step-subset default with the
rest reachable, or `practice_reset` rendered as a separator rather than a
pickable row). Baking a volume opinion into this pure function would make
that later decision harder to change, not easier, and none of the three
candidate shapes need this module to withhold a label — they all operate on
the labelled STREAM, deciding what to show, not what CAN be shown.
"""
from collections.abc import Callable

from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.moment import MOMENTS
from sm64_events.memory.addresses import (CASTLE_AREA_NAMES, LEVEL_NAMES,
                                          course_name, star_name)


def _level_name(level_id) -> str:
    return LEVEL_NAMES.get(level_id, f"Level {level_id}")


def _area_name(area_id) -> str:
    return CASTLE_AREA_NAMES.get(area_id, f"Area {area_id}")


def _level_changed(payload: dict) -> str | None:
    from_id, to_id = payload.get("from"), payload.get("to")
    if from_id is None or to_id is None or from_id == to_id:
        # from == to is an establishing/corrective bookkeeping event
        # (detectors/level.py) — never real movement, never a boundary.
        return None
    return f"Exited {_level_name(from_id)} into {_level_name(to_id)}"


def _area_changed(payload: dict) -> str | None:
    from_area, to_area = payload.get("from"), payload.get("to")
    if to_area is None or from_area == to_area:
        # same reasoning as _level_changed: from == to is bookkeeping.
        return None
    return f"Moved into the {_area_name(to_area)}"


def _star_collected(payload: dict) -> str | None:
    # course_id/star_id, not the payload's own course_name/star_name strings:
    # this reads through the SAME canonical accessors detectors/star_grab.py
    # used to produce those strings in the first place, so it can never
    # disagree with a well-formed payload, and it degrades gracefully
    # (star_name(99, 99) -> "Star 100") rather than depending on a field a
    # legacy journal might not carry.
    course_id, star_id = payload.get("course_id"), payload.get("star_id")
    if course_id is None or star_id is None:
        return None
    return f"Grabbed {star_name(course_id, star_id)} in {course_name(course_id)}"


def _warp_entered(payload: dict) -> str | None:
    level = payload.get("level")
    if level is None:
        return None
    return f"Entered the pipe in {_level_name(level)}"


# detectors/key.py's FIGHT_END_LEVELS values -> the human-facing object of
# "Grabbed ___ in ___". An unrecognised `which` (should never happen — this
# set mirrors FIGHT_END_LEVELS exactly) falls back to a generic phrase rather
# than raising, matching this module's degrade-gracefully convention.
_KEY_WHICH_LABELS = {
    "bitdw": "the Bowser 1 key",
    "bitfs": "the Bowser 2 key",
    "grand": "the Grand Star",
}


def _key_grabbed(payload: dict) -> str | None:
    level, which = payload.get("level"), payload.get("which")
    if level is None or which is None:
        return None
    return f"Grabbed {_KEY_WHICH_LABELS.get(which, 'a key')} in {_level_name(level)}"


_MOMENT_LABELS = {m.kind: m.label for m in MOMENTS}


def _moment_reached(payload: dict) -> str | None:
    """"Open a door (#5) in Big Boo's Haunt" — the sentence the builder's
    picker shows for a moment you can point at.

    The ORDINAL is printed only past the first, because most subsections have
    one unambiguous boundary and "#1" on every row is noise. Where it matters
    it matters a lot: the fifth door in Big Boo's Haunt is a different pick
    from the fourth, and the number is the only thing distinguishing them.
    """
    kind = payload.get("kind")
    if kind is None:
        return None
    what = _MOMENT_LABELS.get(kind, kind)
    where = _level_name(payload.get("level"))
    ordinal = payload.get("ordinal")
    if ordinal and ordinal > 1:
        return f"{what} (#{ordinal}) in {where}"
    return f"{what} in {where}"


def _spawned(payload: dict) -> str | None:
    level = payload.get("level")
    if level is None:
        return None
    if payload.get("kind") == "intro":
        return f"Started the file in {_level_name(level)}"
    return f"Spawned into {_level_name(level)}"


# practice_reset/state_loaded/game_reset carry no level/course at all
# (detectors/anchors.py, lifecycle.py) — the matcher resolves WHERE an
# attempt_anchor/reset_game trigger fires from live MatchContext state, not
# from the event's own payload, and label_event only ever sees one row in
# isolation. These three always produce a sentence (never None): "a reset
# happened" is itself the recognisable moment, even with no place attached.
def _practice_reset(payload: dict) -> str:
    igt = payload.get("igt_frames_before")
    if igt is None:
        return "Reset the level"
    return f"Reset the level after {format_igt(igt)}"


def _state_loaded(payload: dict) -> str:
    igt = payload.get("igt_frames_restored")
    if igt is None:
        return "Loaded a savestate"
    return f"Loaded a savestate at {format_igt(igt)}"


def _game_reset(payload: dict) -> str:
    return "Reset the game"


_LABELERS: dict[str, Callable[[dict], str | None]] = {
    "level_changed": _level_changed,
    "area_changed": _area_changed,
    "star_collected": _star_collected,
    "warp_entered": _warp_entered,
    "key_grabbed": _key_grabbed,
    "spawned": _spawned,
    "moment_reached": _moment_reached,
    "practice_reset": _practice_reset,
    "state_loaded": _state_loaded,
    "game_reset": _game_reset,
}

# The journal event types this module can turn into a sentence at all — read
# by test_eventlabel.py's completeness guard against tracking/segments.py's
# TRIGGERS registry. NOT the set of types the timeline should default to
# showing (see the labelling-volume decision above) — a type being in here
# says nothing about how a later task chooses to display it.
#
# THE GUARD'S BLIND SPOT: test_every_trigger_type_has_a_labellable_event_shape
# fails the day a NEW TriggerType lands with no entry in its trigger->journal-
# type mapping, but it reads that mapping as a hand-written literal, not from
# TRIGGERS' own match lambdas — so if an EXISTING trigger's match lambda were
# edited to dispatch on a different journal type, the guard's literal mapping
# would go stale silently and keep passing. Living with this rather than
# building lambda introspection to close it.
LABELLABLE_TYPES = frozenset(_LABELERS)

# Maps each TriggerType KEY (segments.py's matcher vocabulary: level_enter/
# level_exit/area_enter/...) to the journal event TYPE(S) that can actually
# fire it — read off each TriggerType's own match lambda in TRIGGERS by hand
# (see THE GUARD'S BLIND SPOT above: this is that hand-written literal).
# Trigger KEYS and journal event TYPES are two different vocabularies (the
# matcher vocabulary vs. the wire format); label_event never dispatches on a
# string like "level_enter" or "reset_game", only on real event types like
# "level_changed" or "game_reset". ONE DOOR — test_eventlabel.py's
# completeness guard and test_api.py's default-view sole-route test both
# read this same constant rather than each carrying their own copy (a second
# hand-written copy is exactly how the task-11 revision's wrong `attempt_
# anchor` count first got in: it was hand-copied from a docstring instead of
# re-derived).
TRIGGER_JOURNAL_TYPES: dict[str, frozenset[str]] = {
    "level_enter": frozenset({"level_changed"}),
    "level_exit": frozenset({"level_changed"}),
    "area_enter": frozenset({"area_changed"}),
    "warp_entered": frozenset({"warp_entered"}),
    # Two conditions, ONE journal event: `warp_entered` names where you are,
    # `entrance_touched` names where the entrance leads (task 0081).
    "entrance_touched": frozenset({"warp_entered"}),
    "key_grabbed": frozenset({"key_grabbed"}),
    "star_grabbed": frozenset({"star_collected"}),
    "spawned": frozenset({"spawned"}),
    "moment_reached": frozenset({"moment_reached"}),
    "attempt_anchor": frozenset({"practice_reset", "state_loaded"}),
    "reset_game": frozenset({"game_reset"}),
}


def label_event(row) -> str | None:
    """row: an EventRow (storage/db.py) — .type and .payload are read; .id,
    .frame, .wall_time_utc are not needed for the sentence itself. Returns
    None for a non-boundary type, or for a boundary-capable type whose
    payload lacks the field(s) needed to name what happened (an
    establishing/corrective bookkeeping event, or a legacy journal row from
    before a field existed)."""
    labeler = _LABELERS.get(row.type)
    if labeler is None:
        return None
    return labeler(row.payload)
