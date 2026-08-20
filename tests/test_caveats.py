"""tracking/caveats.py — does this saved time mean what the rank implies?

The predicates and the precedence are tested SEPARATELY on purpose. They have
failed separately: ruling 6's own framing was wrong about how many rows
`old_clock` covers in one direction, and my generalization of it wrong in the
other, and only a reprojection against the real journal could say so. A test
that only ever asks "what does the badge say" cannot tell those apart.
"""
from dataclasses import replace

import pytest

from sm64_events.tracking.caveats import (CAVEAT_SEVERITY, attempt_caveat,
                                          PB_GATE_REASONS, caveat_for,
                                          caveats_for, pb_action,
                                          pb_blocked_by, pb_strat_gate)
from sm64_events.tracking.projection import Attempt

BASE = Attempt(
    id=1, session_id=1, course_id=8, star_id=1, strat_tag="Standard",
    anchor_type="practice_reset", anchor_frame=0, outcome="success",
    outcome_detail=None, igt_frames=300, rta_frames=300,
    started_utc="2026-08-01T00:00:00Z", ended_utc="2026-08-01T00:00:10Z",
    cleared=False, cleared_reason=None,
)


def pb(strat_tag="Standard", attempt_id=1):
    """A pb row as db.pbs() hands it over — only the two fields this module
    reads are pinned, so a sibling feature widening the row cannot turn this
    red at merge time."""
    return {"strat_tag": strat_tag, "attempt_id": attempt_id, "frames": 300}


# --- the predicates, one at a time -----------------------------------------

def test_a_clean_igt_timed_star_pb_carries_nothing():
    assert caveats_for(pb(), replace(BASE, timed_by="igt", timed_at="xcam")) == []
    assert caveat_for(pb(), replace(BASE, timed_by="igt", timed_at="xcam")) is None


def test_a_pb_with_no_strategy_is_unattributed_even_with_no_attempt_behind_it():
    """A pb row outlives its attempt (db.py keeps the row for its own
    `frames`), and an unclaimable PB is still unclaimable with nothing behind
    it — which is also the case the practice card was already getting right
    and the quick-select cell was not."""
    assert caveats_for(pb(strat_tag=None), None) == ["unattributed"]
    assert caveat_for(pb(strat_tag=None), None) == "unattributed"


def test_a_grab_timed_star_is_marked_and_an_xcam_timed_one_is_not():
    assert caveats_for(pb(), replace(BASE, timed_at="grab")) == ["grab_timed"]
    assert caveats_for(pb(), replace(BASE, timed_at="xcam")) == []


def test_old_clock_needs_all_three_clauses():
    """Delta-timed, igt-bearing closer, AND the entity's history holding an
    igt-timed attempt. The second clause was the round-3 finding (570 of 626
    segment attempts are delta-timed and MOST ARE DELTA FOREVER — a castle
    movement closing on a `level_changed` has no Usamune number to be given).
    The third is round 17 item 2: task 0081 moved 55 movements' closers onto
    the igt-bearing entrance touch, so the closer's TYPE stopped implying a
    fresh run would time differently — LBLJ, a trigger-clock definition whose
    every attempt banks the same delta, wore "not comparable to a fresh run"
    for runs comparable by construction ("I don't understand why this LBLJ
    timer has a caveat. Feels like it shouldn't."). Two clocks must provably
    coexist in the entity's own record before the mark may draw."""
    delta_but_untimeable = replace(BASE, segment_id=4, course_id=None, star_id=None,
                                   timed_by="delta", closed_by="level_changed")
    assert caveats_for(pb(), delta_but_untimeable, igt_seen=True) == []

    delta_and_timeable = replace(delta_but_untimeable, closed_by="warp_entered")
    assert caveats_for(pb(), delta_and_timeable, igt_seen=True) == ["old_clock"]

    # LBLJ's exact shape: delta PB, igt-bearing closer, and an all-delta
    # history — every run of it measures alike, so there is nothing to warn
    # about. This is the clause whose absence produced his report.
    assert caveats_for(pb(), delta_and_timeable, igt_seen=False) == []

    # An igt-timed row closed by the same event type is fine: the fallback
    # never ran, so there is nothing to explain.
    assert caveats_for(pb(), replace(delta_and_timeable, timed_by="igt"),
                       igt_seen=True) == []


# --- the precedence, once the predicates are settled ------------------------

def test_the_worst_caveat_wins_and_severity_covers_every_key():
    """One badge draws one thing. A wrong QUANTITY outranks an ungradeable
    one, because a reader can still act on a number that is merely unranked
    and cannot act on one that measures the wrong span."""
    both = replace(BASE, timed_at="grab")
    assert sorted(caveats_for(pb(strat_tag=None), both)) == sorted(
        ["grab_timed", "unattributed"])
    assert caveat_for(pb(strat_tag=None), both) == "grab_timed"

    assert caveat_for(pb(strat_tag=None),
                      replace(BASE, segment_id=4, timed_by="delta",
                              closed_by="star_collected"),
                      igt_seen=True) == "old_clock"


@pytest.mark.parametrize("key", CAVEAT_SEVERITY)
def test_every_ranked_key_is_reachable_by_some_real_input(key):
    """A key in the severity tuple that no predicate can ever produce is a
    mark that will never draw — and it would still pass the cross-language
    key-set check, which compares vocabularies rather than behaviour."""
    inputs = {
        "grab_timed": (pb(), replace(BASE, timed_at="grab"), False),
        "old_clock": (pb(), replace(BASE, timed_by="delta",
                                    closed_by="star_collected"), True),
        "unattributed": (pb(strat_tag=None), replace(BASE, timed_at="xcam"),
                         False),
    }
    assert key in inputs, f"{key} is ranked but this test names no input for it"
    assert key in caveats_for(*inputs[key])


# --- the PRACTICE LOG's own mark, which asks about the ROW, not about a PB ---

def test_a_proven_grab_timed_row_is_marked_in_the_practice_log():
    """The row he pointed at: reset mid-backflip after the grab, so the x-cam
    never happened and the payload says so (2026-08-02, attempt 22829 in his
    own journal)."""
    assert attempt_caveat(replace(BASE, timed_at="grab")) == "grab_timed"


def test_an_unknown_row_is_NOT_marked_in_the_practice_log_though_its_pb_is():
    """The alarm-fatigue clause, and the one thing that could quietly ruin
    this feature. Of his 837 star successes, 3 carry `"grab"` and 670 carry
    `None` — marking the unknowns would put a warning on four fifths of the
    log. The PB badge still covers them, because that surface asserts a GRADE
    and an unverifiable time cannot back one."""
    legacy = replace(BASE, timed_at=None)
    assert attempt_caveat(legacy) is None
    assert caveats_for(pb(), legacy) == ["grab_timed"]


def test_an_xcam_row_and_a_segment_row_are_never_marked():
    assert attempt_caveat(replace(BASE, timed_at="xcam")) is None
    # A segment has no x-cam to be legal about; its timed_at is None by
    # construction, so this guards the shape rather than a reachable state.
    assert attempt_caveat(replace(BASE, segment_id=4, course_id=None,
                                  star_id=None, timed_at="grab")) is None
    assert attempt_caveat(None) is None


@pytest.mark.parametrize("timed_at", ["grab", "xcam", None])
def test_the_row_mark_and_the_save_refusal_can_never_disagree(timed_at):
    """Two questions, ONE predicate. A row marked wrong-quantity while its
    Save-as-PB button still offers the save is the exact drift these share a
    door to prevent — and drawing the mark from `pb_blocked_by` instead would
    have tied it to the save rule forever."""
    row = replace(BASE, timed_at=timed_at)
    assert (attempt_caveat(row) is None) == (pb_blocked_by(row) is None)


def test_no_pb_means_no_caveat():
    """Every consumer passes the entity's current PB straight through, and it
    is legitimately None for anything never practised."""
    assert caveats_for(None, replace(BASE, timed_at="grab")) == []
    assert caveat_for(None, replace(BASE, timed_at="grab")) is None


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
