"""What may this practice-log row DO about a personal best -- save one, undo
one, or nothing, and when nothing, why.

THE action-column resolver (2026-08-20). The practice log draws a row's
Save/Undo button from it and `tracking/service.py::save_pb`/`undo_pb` refuse
on it, so the door and the decoration cannot disagree -- the browser
combining two predicates itself would be a second copy of the precedence,
and drift there is a button offering what the server rejects.

Two predicates, two questions, one precedence:

  * `caveats.pb_blocked_by` -- is this TIME a legal quantity (a grab-timed
    star is not, whatever you retag it to);
  * `pb_strat_gate` -- does this row belong to the strategy being PRACTISED.
    A personal best has always been per (target, strategy) -- the glossary
    says so and `views.current_pbs_by_strat` stores it that way -- but the
    card offered Save on every row regardless of which strategy the run was
    tagged with, so a Standard time could be banked while 3x LJ was active
    and then grade nothing the player was looking at. His report
    (2026-08-15): "I cannot save a PB for a different strategy to the one
    I'm working on". An UNTAGGED row is foreign too, not a special case: it
    belongs to no strategy at all, so it cannot be the one on screen.
    Retagging it through the row's own strategy picker is the way back in
    ("The user should be able to reclassify the entry, and then the button
    is re-enabled").

STRATEGY outranks quantity (flipped 2026-08-22): "is this row yours to act
on" is answered before "is its time legal", because his ruling that day is
that a row of another strategy draws NOTHING -- not a delta, not a chip, and
not the disabled grab-timed button either ("hidden entirely for a strategy
that isn't the one selected"). The order changes only which reason is
STATED; an illegal quantity stays illegal whatever you retag the row to, and
shows its disabled button the moment the row is the active strategy's. The
consequence he should know: with no strategy selected nothing can be saved as
a PB at all, which permanently closes the untagged-PB class (live report
2026-07-31, the `unattributed` caveat's own origin) -- you can no longer mint
a PB that no ladder can grade.

Its own module rather than a corner of `caveats.py` (2026-08-22): a caveat
answers "does this saved time mean what the rank beside it implies", and a
gate answers "is this row yours to act on". The likely iterations -- gate
Undo or not, allow a no-strategy save, change which rows are gated -- all
land here and only here. The browser draws NOTHING for a gate reason (his
2026-08-22 ruling), so there is no JS vocabulary to keep in step -- the
reasons exist for the API's refusal message and for any surface that later
wants to explain the absence; `docs/api.md` names them.
"""
from sm64_events.tracking.caveats import pb_blocked_by

# Why the PB action is unavailable for a reason about the STRATEGY rather
# than about the time. Not caveat keys and deliberately not in
# CAVEAT_SEVERITY: a caveat says a saved time does not mean what the rank
# beside it implies (and draws a mark), while these say the row is fine and
# simply is not what you are practising right now (and draw nothing).
PB_GATE_REASONS = ("no_active_strat", "foreign_strat")


def pb_strat_gate(attempt, active_strat: str | None) -> str | None:
    """Whether this row's PB action belongs to the strategy being practised:
    a `PB_GATE_REASONS` key, or None when it does."""
    if not active_strat:
        return "no_active_strat"
    return None if attempt.strat_tag == active_strat else "foreign_strat"


def pb_action(attempt, active_strat: str | None,
              owns_strat_pb: bool) -> tuple[str | None, dict | None]:
    """`("save" | "undo" | None, {"reason", "strat"} | None)` for one row.

    Pure, so `views._attempt_json` can call it per row from the maps the
    session view already holds; `TrackerService.pb_action` is the same
    question asked of the DATABASE for one attempt, and is what the commands
    go through. `owns_strat_pb` = this attempt holds the current PB for its
    OWN strategy on the clock in question.

    `(None, None)` -- no action and nothing to explain -- covers the rows that
    have never carried a button: failures, cleared rows, and an attempt with
    no entity at all (the unassigned list, `course_id is None` and no
    segment). There is nothing for such a row's PB to be a PB OF, and the
    save path would previously have written one keyed on nothing.
    """
    if attempt.outcome != "success" or attempt.cleared:
        return None, None
    if attempt.course_id is None and attempt.segment_id is None:
        return None, None
    gate = pb_strat_gate(attempt, active_strat)
    if gate is not None:
        return None, {"reason": gate, "strat": active_strat}
    blocked = pb_blocked_by(attempt)
    if blocked is not None:
        return None, {"reason": blocked, "strat": None}
    return ("undo" if owns_strat_pb else "save"), None
