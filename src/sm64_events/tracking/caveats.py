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
        # "grab" is also what a star recorded BEFORE the x-cam fix stamps:
        # `igt_timed_at` did not exist in the payload then, and its absence is
        # exactly "this row is the grab quantity" (projection.py).
        if attempt.timed_at == "grab":
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
