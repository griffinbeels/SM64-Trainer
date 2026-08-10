"""Does this saved time mean what the rank beside it implies?

THE answer, computed once and read by every surface that shows a PB. Three
findings converged on that one sentence and are deliberately answered together,
because two surfaces honestly computing the same fact and wording it
differently is the divergent-duplication class this repo has a rule about:

  * `unattributed` — the PB carries no `strat_tag`, so `current_pbs_by_strat`
    can never find it and NO strategy can claim it, whichever one is active.
    Live report 2026-07-31: "Bowser 1 shows PB 0'26"30, but the rank display
    clearly shows Capless 5... this should never happen." The practice card
    already refuses to floor this (`_section_banner`'s own sentinel); the
    quick-select cell did not, which is round-4 item 2.

  * `old_clock` — the PB was timed by a wall-frame delta while the SAME
    entity's history also holds an igt-timed attempt: two clocks provably
    coexist, so this number is not comparable to the entity's own fresh runs.
    THREE clauses now, and the third (`igt_seen`, round 17 item 2,
    2026-08-08) exists because the closer-TYPE clause stopped being a valid
    proxy for it: it was measured when castle movements closed on
    `level_changed` (never igt-bearing), and task 0081 re-pointed 55 of them
    onto the entrance TOUCH (`warp_entered`, igt-bearing) — so LBLJ, a
    trigger-clock definition whose every one of 15 attempts banks the same
    delta, wore "not comparable to a fresh run" for runs that are comparable
    by construction. His report: "I don't understand why this LBLJ timer has
    a caveat. Feels like it shouldn't. I didn't save state at all, just
    played normally." Measured on his live snapshot before the change: the
    old predicate marked 3 of 8 current PBs, all LBLJ, all noise; requiring
    a mixed-clock history marks 0 today and re-marks exactly when a fresh
    igt-timed run lands beside a delta PB — the moment comparability
    actually breaks. The earlier two clauses still hold (round-3 ruling 6:
    570 of 626 attempts are delta-timed, most delta FOREVER, and marking
    them all would put a warning on nearly every movement PB he owns).

  * `grab_timed` — a star whose time is the GRAB quantity rather than the
    x-cam quantity a leaderboard accepts (round-4 items 3/4). `Attempt.
    timed_at` carries it, stamped from the closing event's own payload, so it
    re-derives on every reproject with no backfill and no list of ids.

The severity ORDER lives here rather than in the browser, so the server sends
one key and the client only has to draw it. `ui/components/marks.js` holds the
glyph/wording/floor rule for each key and never has to choose between two;
`tests/test_cross_language_parity.py` pins the two key sets equal, because a
key this file can send and that file cannot draw renders silently as nothing.
"""
from sm64_events.core.events import IGT_BEARING_EVENT_TYPES

# Worst first. A row can legitimately carry more than one — a grab-timed star
# whose PB is also untagged — and one 16px badge draws exactly one thing. The
# order is by what the caveat CHANGES: a wrong quantity outranks an ungradeable
# one, because a reader can still act on a number that is merely unranked and
# cannot act on one that measures the wrong span.
CAVEAT_SEVERITY = ("grab_timed", "old_clock", "unattributed")


def igt_seen_in(history) -> bool:
    """Does this entity's history hold ANY igt-timed attempt? THE third
    old_clock clause: a delta PB is provably on the other clock only when
    the same entity has banked Usamune's number at least once. Callers pass
    the entity's FULL attempt list, never a scope-filtered slice — the
    question is about the record, not about what is on screen."""
    return any(a.timed_by == "igt" for a in history)


def caveats_for(pb_row, attempt, igt_seen: bool = False) -> list[str]:
    """Every caveat true of this PB, unordered. Split out from `caveat_for`
    so a test can assert the PREDICATES independently of the precedence — the
    two have failed separately (ruling 6's own framing was wrong about the
    size of `old_clock` in one direction and my generalization of it wrong in
    the other, and only a reprojection could say so).

    `igt_seen` = igt_seen_in(the entity's attempts). Defaults False, which is
    the direction that cannot cry wolf: a caller that does not know says
    nothing rather than warning about a clock mismatch it never checked."""
    if pb_row is None:
        return []
    found = []
    if attempt is not None:
        # Two different rows wear this one mark, and keeping them apart is
        # what stopped a refusal from being wrong 669 times (2026-08-02):
        #   "grab"  — PROVEN: the x-cam never happened and the payload says so.
        #   None on a STAR — UNKNOWN: recorded before `igt_timed_at` existed,
        #     so it may be either. Legacy rows overwhelmingly took Usamune's
        #     own stored number (`igt_source: "result"`, 669 of 670), which is
        #     the x-cam value under STOP=Xcam and on any ground grab and the
        #     grab value under GrabX with a real fall — and nothing journaled
        #     says which.
        # Both are marked, because a caveat's job is "this may not mean what
        # the rank implies" and an unverifiable time qualifies. Only the
        # PROVEN one blocks a save (pb_blocked_by) — the segment guard is what
        # keeps every segment row, whose timed_at is None by construction, out
        # of a mark about a moment segments do not have.
        if attempt.segment_id is None and attempt.timed_at in ("grab", None):
            found.append("grab_timed")
        if (attempt.timed_by == "delta"
                and attempt.closed_by in IGT_BEARING_EVENT_TYPES
                and igt_seen):
            found.append("old_clock")
    # Deliberately outside the `attempt is not None` guard: a PB whose saving
    # attempt has been wiped still shows, and it is still unclaimable.
    if pb_row["strat_tag"] is None:
        found.append("unattributed")
    return found


def caveat_for(pb_row, attempt, igt_seen: bool = False) -> str | None:
    """The ONE caveat a surface draws for this PB, worst first, or None."""
    found = caveats_for(pb_row, attempt, igt_seen)
    return next((key for key in CAVEAT_SEVERITY if key in found), None)


def _proven_grab_timed(attempt) -> bool:
    """The x-cam PROVABLY never happened for this star attempt.

    The one predicate behind both `attempt_caveat` and `pb_blocked_by`, so a
    row can never be marked wrong-quantity by one and offered as a legal PB by
    the other. `"grab"` is the payload SAYING the x-cam never arrived (reset,
    savestate load, level change, IGT reset, or the 300-frame backstop);
    `None` is a row that predates the key and is simply unknown, which is a
    different fact and is deliberately not this one. A segment has no x-cam to
    be legal about and its `timed_at` is None by construction."""
    return (attempt is not None and attempt.segment_id is None
            and attempt.timed_at == "grab")


def attempt_caveat(attempt) -> str | None:
    """The ONE caveat the PRACTICE LOG draws on this attempt's own time.

    Not `caveat_for`: that asks about a SAVED PB and its rank, and two of its
    three keys are about the pb row rather than the attempt (`unattributed`
    reads `strat_tag` off the pb; `old_clock` is about a saved time being
    incomparable to a fresh run). This asks the narrower question a row in the
    log can answer about itself — "is the number printed here the quantity you
    think you were practising".

    PROVEN only, and that is the whole design (2026-08-02, reversing an
    earlier ruling): "I want to add an extra (!) indicator to the entry if it
    was technically a star grab and not a correctly timed xcam entry… If
    you've been practicing all wrong, you should know." Measured against his
    own journal the same day, so the alarm-fatigue objection is settled with a
    number rather than an argument: of 837 star successes, **3 carry `"grab"`
    and 670 carry `None`**. Marking the unknown rows would put a warning on
    four fifths of the practice log forever and on nothing he can act on;
    marking the proven ones marks exactly the run he just threw away. The
    UNKNOWN rows are still marked where the question is about a rank rather
    than about a run — `caveats_for` covers them on the PB badge, which is the
    surface that asserts a grade.

    `old_clock` is deliberately absent rather than forgotten: it is the only
    other key an attempt could carry on its own, it would land on segment rows
    (rule 11's other half), and how many is unmeasured. One key here is a
    stated scope, not a gap — add the second when its own count says it reads."""
    return "grab_timed" if _proven_grab_timed(attempt) else None


def pb_blocked_by(attempt) -> str | None:
    """Why this attempt may NOT be saved as a PB — a caveat key, or None.

    A caveat says a saved time does not mean what the rank beside it implies.
    This says the same thing one step earlier, about a time that is not saved
    yet: "these fake PBs (fake because only xcam timing is legal) just
    shouldn't be allowed" (2026-08-02). A star timed at the GRAB is not an
    early version of a legal time, it is a different quantity — Usamune stops
    at the x-cam and a leaderboard accepts nothing else — so the honest thing
    is to refuse the save rather than record it and mark it afterwards.

    THE predicate for both halves of that: `tracking/service.py::save_pb`
    raises on it and `views._attempt_json` ships it so the button can be drawn
    disabled with the reason. Two doors computing "is this saveable" their own
    way is the divergent-duplication class, and here the drift would be a
    button that offers what the server refuses.

    Returns a caveat KEY rather than a sentence, so the browser draws it out of
    the vocabulary it already has (`ui/components/marks.js`) — the same glyph
    and the same words as the badge on a PB already saved with this problem.

    Not blocked: `old_clock` and `unattributed`. Both are about a time that is
    real and comparable to something, and refusing to save them would delete a
    legitimate record to make a point. Only the wrong QUANTITY is blocked.

    Both clocks are refused, not just igt. When the x-cam never happened the
    run's recorded end IS the grab, so the rta measures to that same illegal
    moment; letting the same fake time through on the other clock would be the
    rule with a hole in it.

    Blocked on PROOF, never on a guess. `timed_at == "grab"` means the payload
    itself says the x-cam never happened; `None` on a star means the row
    predates the key and its legality is simply unknown (669 of his 670 legacy
    rows took Usamune's own stored number, which is the legal quantity under
    STOP=Xcam and on any ground grab). Those are MARKED and still saveable —
    refusing them would delete a legal record on an assumption nobody
    measured, and it is most of his history.

    Segments are never blocked: `timed_at` is None for every non-star closure
    (projection.py), and a segment has no x-cam to be legal about."""
    return "grab_timed" if _proven_grab_timed(attempt) else None
