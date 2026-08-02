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

  * `old_clock` — the attempt was timed by a wall-frame delta even though its
    closing event type is one that WOULD carry Usamune's IGT today. BOTH
    clauses are load-bearing and the second is the whole point: 570 of 626
    segment attempts in the 2026-07-31 journal are delta-timed, and most are
    delta FOREVER — a castle movement closes on a `level_changed`, an event
    that has no Usamune number to give, so its delta simply IS how that
    segment is measured and stays perfectly comparable to the next run of it.
    Marking those would have put a warning on nearly every movement PB he
    owns. Round-3 ruling 6; the predicate was measured against a reprojected
    snapshot of the dev journal and selects 10 of 23 saved segment PBs.

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


def caveats_for(pb_row, attempt) -> list[str]:
    """Every caveat true of this PB, unordered. Split out from `caveat_for`
    so a test can assert the PREDICATES independently of the precedence — the
    two have failed separately (ruling 6's own framing was wrong about the
    size of `old_clock` in one direction and my generalization of it wrong in
    the other, and only a reprojection could say so)."""
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
                and attempt.closed_by in IGT_BEARING_EVENT_TYPES):
            found.append("old_clock")
    # Deliberately outside the `attempt is not None` guard: a PB whose saving
    # attempt has been wiped still shows, and it is still unclaimable.
    if pb_row["strat_tag"] is None:
        found.append("unattributed")
    return found


def caveat_for(pb_row, attempt) -> str | None:
    """The ONE caveat a surface draws for this PB, worst first, or None."""
    found = caveats_for(pb_row, attempt)
    return next((key for key in CAVEAT_SEVERITY if key in found), None)


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
    if attempt is not None and attempt.segment_id is None \
            and attempt.timed_at == "grab":
        return "grab_timed"
    return None
