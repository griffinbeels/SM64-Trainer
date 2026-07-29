"""The shared castle-movement segments (spec 2026-07-24 §4.4).

Converted to match_mode="loose" 2026-07-28 (Task 19, spec
2026-07-28-multi-step-segments) -- every row here now ships loose, and most
of these shapes are no longer FORCED the way this docstring used to say.
Before the conversion, 14 of the 55 rows carried a `via=[...]` chain whose
only job was surviving the STRICT matcher's cancellation rules:
  * a plain (via=[]) def used to be disarmed by any area_changed away from
    its arm position, and by any level_changed matching neither start nor
    end;
  * a waypoint-bearing def used to be SILENTLY CANCELLED by any star grab.
Loose matching passes all of that through transparently (only the deadline,
death/game_reset, session_started, and a real anchor still act on an armed
def — see SegmentEngine._feed_loose's docstring), so re-running
tests/test_defaults_corpus.py after deleting each `via` proved 13 of the 14
were dodging exactly those two rules and nothing else. One survived:

  * `seg:bbh->basement` keeps its waypoint because of a rule that has
    NOTHING to do with match_mode -- can_run_from (segments.py), the
    arm-position gate every match mode shares, refuses to arm a def whose
    very next required step (waypoint[0], or the end trigger when there are
    no waypoints) can't fire from wherever the start trigger actually landed
    Mario. BBH's exit lands in the courtyard (level 26), and a plain
    `area_enter(level=6, area=BASEMENT)` end is simply unfireable from
    there; see the comment on that row for the mechanism.

Two invariants survive untouched, because they hold for every match mode:
  * a movement may START on a star_grabbed clause but must NEVER end on one
    (run-ordering trap — spec §5.2);
  * a def whose start and end are satisfiable by the SAME event is
    UNFIREABLE (the direct-edge trap — spec §5.1), guarded by
    test_no_movement_starts_and_ends_on_the_SAME_event.

Read spec §4.1/§4.2 for the retired strict-mode rules above if a future row
ever opts back into match_mode="strict"; none do today.

Task 20 (spec 2026-07-28-multi-step-segments) adds three shapes loose
matching finally makes expressible: `seg:bowser2->bits` (a plain movement
surviving a long real detour -- BitFS re-entry, a pause exit, a lobby/
upstairs crossing, a BLJ), `REDS_TO_PIPE` (Bowser-stage reds star -> pipe,
still ends on a real edge so it stays Castle Movement, just UNGUARDED like
the legacy pipe-entry trio -- see mechanic()'s docstring) and
`HUNDRED_COIN_EXITS` (its own category, `HUNDRED_COIN_EXIT`, because it
DELIBERATELY ends on star_grabbed -- see the section comment above it for
why the run-ordering trap does not apply).
"""
from corpus_vocab import (BASEMENT, CASTLE_MOVEMENT, HUNDRED_COIN_EXIT, LOBBY,
                          UPSTAIRS, enter_area, enter_level, enter_warp,
                          exit_level, grab_star, mechanic, movement)

MOVEMENTS = [
    # --- lobby ------------------------------------------------------------
    movement("seg:castle-entry->bob", "Castle Entrance → BoB",
             enter_level(6, frm=16), enter_level(9)),
    movement("seg:bob->wf", "BoB → WF", exit_level(9), enter_level(24)),
    movement("seg:bob->pss", "BoB → PSS", exit_level(9), enter_level(27)),
    movement("seg:bob->ccm", "BoB → CCM", exit_level(9), enter_level(5)),
    movement("seg:bob->basement", "BoB → Basement",
             exit_level(9), enter_area(BASEMENT)),
    movement("seg:pss->wf", "PSS → WF", exit_level(27), enter_level(24)),
    movement("seg:wf->pss", "WF → PSS", exit_level(24), enter_level(27)),
    movement("seg:wf->ccm", "WF → CCM", exit_level(24), enter_level(5)),
    movement("seg:wf->sa", "WF → Secret Aquarium",
             exit_level(24), enter_level(20)),
    movement("seg:wf->bitdw", "WF → BitDW", exit_level(24), enter_level(17)),
    movement("seg:wf->ssl", "WF → SSL", exit_level(24), enter_level(8)),
    movement("seg:sa->jrb", "Secret Aquarium → JRB",
             exit_level(20), enter_level(12)),
    movement("seg:jrb->pss", "JRB → PSS", exit_level(12), enter_level(27)),
    movement("seg:pss->totwc", "PSS → TotWC", exit_level(27), enter_level(29)),
    movement("seg:totwc->pss", "TotWC → PSS", exit_level(29), enter_level(27)),
    movement("seg:totwc->bitdw", "TotWC → BitDW",
             exit_level(29), enter_level(17)),
    movement("seg:pss->bitdw", "PSS → BitDW", exit_level(27), enter_level(17)),
    movement("seg:pss->bob", "PSS → BoB", exit_level(27), enter_level(9)),
    movement("seg:ccm->bitdw", "CCM → BitDW", exit_level(5), enter_level(17)),
    movement("seg:ccm->bbh", "CCM → BBH", exit_level(5), enter_level(4)),
    # --- out of the Bowser 1 arena (its exit lands in the lobby) ----------
    movement("seg:bowser1->bob", "Bowser 1 → BoB",
             exit_level(30), enter_level(9)),
    movement("seg:bowser1->wf", "Bowser 1 → WF",
             exit_level(30), enter_level(24)),
    movement("seg:bowser1->ccm", "Bowser 1 → CCM",
             exit_level(30), enter_level(5)),
    movement("seg:bowser1->ssl", "Bowser 1 → SSL",
             exit_level(30), enter_level(8)),
    movement("seg:bowser1->ddd", "Bowser 1 → DDD (Crackslide)",
             exit_level(30), enter_level(23)),
    movement("seg:bowser1->bitfs", "Bowser 1 → BitFS (SBLJ / DDD Skip)",
             exit_level(30), enter_level(19)),
    # --- courtyard (BBH exits to level 26, not to the castle interior) ----
    # Load-bearing under loose matching too (2026-07-28, Task 19): removing
    # this waypoint makes tests/test_defaults_corpus.py's own-walk test fail
    # to arm at all -- can_run_from (segments.py) is unconditional regardless
    # of match_mode, and area_enter(level=6, area=BASEMENT)'s precondition
    # only fires from level 6. BBH's exit lands at level 26 (the courtyard),
    # so a plain end trigger is simply unfireable from the arm position; the
    # waypoint's level_enter(6) has no `from`, so it fires from anywhere but
    # its own destination and gets the arm past the courtyard.
    movement("seg:bbh->basement", "BBH → Basement",
             exit_level(4), enter_area(BASEMENT), via=[enter_level(6)]),
    movement("seg:bbh->ddd", "BBH → DDD",
             exit_level(4), enter_level(23)),
    # --- basement ---------------------------------------------------------
    movement("seg:mips1->ssl", "MIPS (1st) → SSL",
             grab_star(0, 3), enter_level(8)),
    movement("seg:ssl->lll", "SSL → LLL", exit_level(8), enter_level(22)),
    movement("seg:ssl->hmc", "SSL → HMC", exit_level(8), enter_level(7)),
    movement("seg:lll->hmc", "LLL → HMC", exit_level(22), enter_level(7)),
    movement("seg:lll->ddd", "LLL → DDD", exit_level(22), enter_level(23)),
    movement("seg:hmc->lll", "HMC → LLL", exit_level(7), enter_level(22)),
    movement("seg:hmc->ddd", "HMC → DDD", exit_level(7), enter_level(23)),
    movement("seg:hmc->rr", "HMC → RR (re-entry, pause exit)",
             exit_level(7), enter_level(15)),
    movement("seg:mips2->hmc", "MIPS (2nd) → HMC",
             grab_star(0, 4), enter_level(7)),
    movement("seg:mips2->vcutm", "MIPS (2nd) → VCUtM",
             grab_star(0, 4), enter_level(18)),
    movement("seg:vcutm->ccm", "VCUtM → CCM",
             exit_level(18), enter_level(5)),
    # Started on the star that opens the sub until 2026-07-27, on the premise
    # that BitFS is entered DIRECTLY from DDD (23 -> 19) — which would make
    # `level_exit from=23` and `level_enter to=19` the same event, arming a def
    # on the event that should close it. The premise was false: BitFS is
    # entered from the BASEMENT, and the live journal of the real walk has no
    # 23 -> 19 transition at all (`23 -> 6 (from_area 2)`, `area 2 -> 3`,
    # `warp_entered {level: 6, area: 3}`, `6 -> 19 (from_area 3)`). The bad
    # one-way edge is gone from addresses.py, so this is now the same plain
    # basement-to-basement shape as LLL → DDD and HMC → DDD, and needs no
    # waypoint: both ends are the one region.
    #
    # What the old start cost: a movement fired the moment the DDD star was
    # grabbed, while the player was still standing in DDD — and since an arm
    # retires a star target (projection.py), practising that star lost the
    # target on every successful grab (live report 2026-07-27).
    #
    # The hang it was working around — warping DDD -> SSL left this "running"
    # in Shifting Sand Land (2026-07-24) — is now handled structurally by the
    # arm-position gate: an unpinned `level_exit` from a non-castle level must
    # land in CASTLE_LEVELS, and SSL is not the castle, so that arm is refused.
    movement("seg:ddd->bitfs", "DDD → BitFS (sub)",
             exit_level(23), enter_level(19)),
    movement("seg:ddd->wdw", "DDD → WDW (BitFS re-entry, pause exit)",
             exit_level(23), enter_level(11)),
    # --- out of the Bowser 2 arena (its exit lands in the basement) -------
    movement("seg:bowser2->ddd", "Bowser 2 → DDD",
             exit_level(33), enter_level(23)),
    movement("seg:bowser2->wdw", "Bowser 2 → WDW",
             exit_level(33), enter_level(11)),
    movement("seg:bowser2->upstairs", "Bowser 2 → Upstairs",
             exit_level(33), enter_area(UPSTAIRS)),
    # Real walk (Task 0017, live report): finish Bowser 2 -> back into BitFS
    # -> pause exit to the basement -> lobby -> upstairs -> BLJs -> BitS.
    # Every one of those steps would cancel a STRICT definition; a plain
    # loose def (no waypoints) passes them all through transparently, which
    # is the whole reason this is finally expressible (Task 20, spec
    # 2026-07-28-multi-step-segments) -- no via chain needed, same shape as
    # every other Bowser-arena-exit movement above.
    movement("seg:bowser2->bits", "Bowser 2 → BitS",
             exit_level(33), enter_level(21)),
    # --- upstairs ---------------------------------------------------------
    movement("seg:wdw->thi", "WDW → THI", exit_level(11), enter_level(13)),
    movement("seg:thi->ttm", "THI → TTM", exit_level(13), enter_level(36)),
    movement("seg:ttm->sl", "TTM → SL", exit_level(36), enter_level(10)),
    movement("seg:sl->basement", "SL → Basement (re-entry, pause exit)",
             exit_level(10), enter_area(BASEMENT)),
    movement("seg:sl->rr", "SL → RR", exit_level(10), enter_level(15)),
    movement("seg:sl->wmotr", "SL → WMotR", exit_level(10), enter_level(31)),
    movement("seg:wmotr->ttc", "WMotR → TTC", exit_level(31), enter_level(14)),
    movement("seg:rr->ttc", "RR → TTC", exit_level(15), enter_level(14)),
    movement("seg:ttc->rr", "TTC → RR", exit_level(14), enter_level(15)),
    movement("seg:rr->bits", "RR → BitS", exit_level(15), enter_level(21)),
    movement("seg:ttc->bits", "TTC → BitS", exit_level(14), enter_level(21)),
]

# --- Bowser-stage reds -> pipe (Task 20, spec 2026-07-28-multi-step-segments)
# "8 red coins levels in bowser stages. When you get this star, the level
# doesn't end -- you have to go into the pipe to finish the level." -- the
# reference autosplitter's own default (it waits for pipe entry), and the
# case its issue tracker shows it repeatedly getting wrong. Ends on
# warp_entered, not star_grabbed, so this does NOT trip the run-ordering
# trap and could be filed as a plain Castle Movement -- it stays UNGUARDED
# instead (see mechanic()'s docstring): there is exactly one pipe per stage,
# no route to scope against, matching the existing pipe-entry trio's
# always-armed shape more closely than the 56 route-scoped movements'.
REDS_TO_PIPE = [
    mechanic("seg:reds->pipe:bitdw", "BitDW — 8 Red Coins → Pipe",
             grab_star(16, 0), enter_warp(17), CASTLE_MOVEMENT),
    mechanic("seg:reds->pipe:bitfs", "BitFS — 8 Red Coins → Pipe",
             grab_star(17, 0), enter_warp(19), CASTLE_MOVEMENT),
    mechanic("seg:reds->pipe:bits", "BitS — 8 Red Coins → Pipe",
             grab_star(18, 0), enter_warp(21), CASTLE_MOVEMENT),
]

# --- 100-coin star -> the star that actually exits (Task 20) ---------------
# "In normal stages, you don't exit the stage when you grab a 100 coins
# star, you can keep playing and must find another star to actually exit
# the level." This is the one seeded shape that DELIBERATELY ends on
# star_grabbed. The rule it looks like it breaks -- a movement may start on
# a star grab but must never end on one -- exists because RunTracker
# (runs.py::_apply) only ever considers the CURRENT route step and
# projection.py closes stars-then-segments within one event, so a
# misordered ROUTE STEP stalls a run permanently and silently. That is a
# property of being a route step: nothing here is one (no seeded route
# references any of these seed_keys, and they carry no guard), so there is
# no run to stall. Hence its own category (HUNDRED_COIN_EXIT) rather than
# Castle Movement -- it never enters corpus_movements.MOVEMENTS, so it never
# reaches the tests built on "a movement never ends on star_grabbed"
# (test_no_movement_starts_and_ends_on_the_SAME_event and friends).
# Every main course (1-15) has six numbered stars (0-5) plus 100 Coins at
# star_id 6 (addresses.star_count/star_name own that rule); end_triggers
# lists the six alternatives explicitly since the vocabulary has no "any
# star but this one" clause.
_MAIN_COURSES = [
    (1, "BoB"), (2, "WF"), (3, "JRB"), (4, "CCM"), (5, "BBH"), (6, "HMC"),
    (7, "LLL"), (8, "SSL"), (9, "DDD"), (10, "SL"), (11, "WDW"), (12, "TTM"),
    (13, "THI"), (14, "TTC"), (15, "RR"),
]

HUNDRED_COIN_EXITS = [
    mechanic(f"seg:100c->exit:{abbrev.lower()}", f"{abbrev} — 100 Coins → Exit",
             grab_star(course, 6),
             [grab_star(course, star_id) for star_id in range(6)],
             HUNDRED_COIN_EXIT)
    for course, abbrev in _MAIN_COURSES
]
