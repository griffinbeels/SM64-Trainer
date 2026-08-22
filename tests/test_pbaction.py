"""tracking/pbaction.py — what may this row DO about a PB, and why not.

The strategy gate and the precedence that combines it with `caveats.
pb_blocked_by`. Pure-function tests; the DATABASE-side entry
(`TrackerService.pb_action`) and the commands that refuse on it are driven
in tests/test_tracker_service.py.
"""
from dataclasses import replace

from sm64_events.tracking.pbaction import (PB_GATE_REASONS, pb_action,
                                           pb_strat_gate)
from sm64_events.tracking.projection import Attempt

BASE = Attempt(
    id=1, session_id=1, course_id=8, star_id=1, strat_tag="Standard",
    anchor_type="practice_reset", anchor_frame=0, outcome="success",
    outcome_detail=None, igt_frames=300, rta_frames=300,
    started_utc="2026-08-01T00:00:00Z", ended_utc="2026-08-01T00:00:10Z",
    cleared=False, cleared_reason=None,
)


# --- the strategy gate, and what it does NOT decide ------------------------

def test_the_strategy_gate_asks_only_whether_the_row_is_this_strategys():
    """A PB is per (target, strategy) -- the glossary and
    `views.current_pbs_by_strat` both already said so -- so the button may
    only offer to bank a run under the strategy it was RUN with, and only
    while that strategy is the one being practised (2026-08-15: "I cannot save
    a PB for a different strategy to the one I'm working on").

    An untagged row is foreign rather than a special case: it belongs to no
    strategy, so it cannot be the one on screen."""
    assert pb_strat_gate(BASE, "Standard") is None
    assert pb_strat_gate(BASE, "3x LJ") == "foreign_strat"
    assert pb_strat_gate(replace(BASE, strat_tag=None), "Standard") \
        == "foreign_strat"
    assert pb_strat_gate(BASE, None) == "no_active_strat"
    assert pb_strat_gate(replace(BASE, strat_tag=None), None) \
        == "no_active_strat"


def test_every_gate_reason_can_be_drawn():
    """The reasons this module can send are exactly the ones marks.js knows
    how to print. The cross-language test owns the JS half; this is the
    Python-side half of the same claim, so a reason added here without a key
    goes red even with node unavailable."""
    reasons = {pb_strat_gate(BASE, "3x LJ"), pb_strat_gate(BASE, None)}
    assert reasons == set(PB_GATE_REASONS)


def test_an_illegal_quantity_outranks_the_strategy_gate():
    """Retagging a grab-timed row does not make its number legal, so the
    caveat reason has to survive putting the strategy in play -- otherwise
    the fix for one bug ("save it under the right strategy") silently reopens
    the other ("these fake PBs just shouldn't be allowed", 2026-08-02)."""
    grabbed = replace(BASE, timed_at="grab")
    assert pb_action(grabbed, "Standard", False) == (
        None, {"reason": "grab_timed", "strat": None})
    assert pb_action(grabbed, "3x LJ", False) == (
        None, {"reason": "grab_timed", "strat": None})


def test_the_action_names_the_strategy_that_would_accept_the_row():
    """The chip has to say WHICH strategy, or "only" names nothing. The
    server sends the name because it is the server that knows which strategy
    is active -- a browser re-deriving it is the drift the one resolver
    exists to prevent."""
    assert pb_action(BASE, "3x LJ", False) == (
        None, {"reason": "foreign_strat", "strat": "3x LJ"})
    assert pb_action(BASE, None, False) == (
        None, {"reason": "no_active_strat", "strat": None})


def test_save_and_undo_are_the_same_gate_with_a_different_verb():
    """`owns_strat_pb` is the ONLY thing separating them, so the two can never
    be gated differently by accident -- which is what made a Standard PB
    un-undoable once a 3x LJ save landed after it."""
    assert pb_action(BASE, "Standard", False) == ("save", None)
    assert pb_action(BASE, "Standard", True) == ("undo", None)


def test_a_row_with_nothing_to_save_offers_nothing_and_explains_nothing():
    """Failures, cleared rows and attempts with no entity have never carried a
    button, and must not grow a chip either: there is no strategy question to
    answer, because there is nothing for the PB to be a PB OF. The last case
    also closes a real hole -- an unassigned attempt could be saved as a PB
    keyed on (None, None)."""
    assert pb_action(replace(BASE, outcome="reset"), "Standard", False) \
        == (None, None)
    assert pb_action(replace(BASE, cleared=True), "Standard", False) \
        == (None, None)
    assert pb_action(replace(BASE, course_id=None, star_id=None),
                     "Standard", False) == (None, None)
