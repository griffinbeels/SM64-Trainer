"""The shared castle-movement segments (spec 2026-07-24 §4.4).

Shapes are FORCED by the frozen matcher, not chosen. Read spec §4.1/§4.2
before editing ANY row:
  * a plain (via=[]) def is disarmed by any area_changed away from its arm
    position, and by any level_changed matching neither start nor end;
  * a waypoint-bearing def is SILENTLY CANCELLED by any star grab;
  * a movement may START on a star_grabbed clause but must NEVER end on one
    (run-ordering trap — spec §5.2).
"""
from corpus_vocab import (BASEMENT, LOBBY, UPSTAIRS, enter_area, enter_level,
                          exit_level, grab_star, movement)

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
             exit_level(23), enter_level(11),
             via=[enter_level(19), exit_level(19)]),
    # --- out of the Bowser 2 arena (its exit lands in the basement) -------
    movement("seg:bowser2->ddd", "Bowser 2 → DDD",
             exit_level(33), enter_level(23)),
    movement("seg:bowser2->wdw", "Bowser 2 → WDW",
             exit_level(33), enter_level(11), via=[enter_area(UPSTAIRS)]),
    # The lobby waypoint is load-bearing, not decoration: the castle interior
    # is a LINE (basement <-> lobby <-> upstairs), so this movement crosses TWO
    # area edges. A plain def would be disarmed by the first one before its
    # `area_enter upstairs` end could ever match — caught by
    # tests/test_defaults_corpus.py's simulation, which is the whole reason
    # that layer exists.
    movement("seg:bowser2->upstairs", "Bowser 2 → Upstairs",
             exit_level(33), enter_area(UPSTAIRS),
             via=[enter_area(LOBBY)]),
    # --- upstairs ---------------------------------------------------------
    movement("seg:wdw->thi", "WDW → THI", exit_level(11), enter_level(13)),
    movement("seg:thi->ttm", "THI → TTM", exit_level(13), enter_level(36)),
    movement("seg:ttm->sl", "TTM → SL", exit_level(36), enter_level(10)),
    movement("seg:sl->basement", "SL → Basement (re-entry, pause exit)",
             exit_level(10), enter_area(BASEMENT),
             via=[enter_level(10), exit_level(10)]),
    movement("seg:sl->rr", "SL → RR", exit_level(10), enter_level(15)),
    movement("seg:sl->wmotr", "SL → WMotR", exit_level(10), enter_level(31)),
    movement("seg:wmotr->ttc", "WMotR → TTC", exit_level(31), enter_level(14)),
    movement("seg:rr->ttc", "RR → TTC", exit_level(15), enter_level(14)),
    movement("seg:ttc->rr", "TTC → RR", exit_level(14), enter_level(15)),
    movement("seg:rr->bits", "RR → BitS", exit_level(15), enter_level(21)),
    movement("seg:ttc->bits", "TTC → BitS", exit_level(14), enter_level(21)),
]
