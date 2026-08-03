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

Read spec §4.1/§4.2 for the retired strict-mode rules above for MOVEMENTS
specifically; none of the 56 opt back into match_mode="strict" today. That is
no longer true of the module as a whole -- REDS_TO_PIPE and HUNDRED_COIN_
EXITS below both do, and each one's own comment explains why the strict
cancellation rules are not just safe but the CORRECT behaviour for a def
confined to one course/stage visit, unlike a movement that crosses several.

Task 20 (spec 2026-07-28-multi-step-segments) adds three shapes loose
matching finally makes expressible: `seg:bowser2->bits` (a plain movement
surviving a long real detour -- BitFS re-entry, a pause exit, a lobby/
upstairs crossing, a BLJ), `REDS_TO_PIPE` (Bowser-stage reds star -> pipe,
still ends on a real edge so it stays Castle Movement, just UNGUARDED like
the legacy pipe-entry trio -- see mechanic()'s docstring) and
`HUNDRED_COIN_EXITS` (its own category, `HUNDRED_COIN_EXIT`, because it
DELIBERATELY ends on star_grabbed -- see the section comment above it for
why the run-ordering trap does not apply).

RESHAPED 2026-07-29 (same spec, live report): both `REDS_TO_PIPE` and
`HUNDRED_COIN_EXITS` originally started on the grab itself (the star or the
100 coins), which the user found unselectable before the grab and mistimed
(measuring only grab->end instead of the whole stage/course). Both now start
on stage/course entry and make the grab a WAYPOINT (mechanic()'s new `via`
parameter) and both ship match_mode="strict" -- see each section's own
comment for why (a Bowser stage having exactly one collectible star, vs. a
main course's other stars each ending the course same as they would in real
play). `HUNDRED_COIN_EXITS` shipped "loose" for a few hours the same day on a
reasoning that turned out to be wrong (checked against actual game behaviour,
not assumed) and was reshaped a second time, later the same day, on a live
report that loose's transparency to level_changed left it reading RUNNING
after the player had physically left the course -- see its own comment for
the corrected reasoning."""
from corpus_vocab import (BASEMENT, CASTLE_MOVEMENT, HUNDRED_COIN_EXIT, LOBBY,
                          UPSTAIRS, anchor, enter_area, enter_level, enter_warp,
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
    movement("seg:wf->ssl", "WF → SSL", exit_level(24), enter_level(8),
             via=[enter_area(BASEMENT)]),
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
    movement("seg:ccm->bbh", "CCM → BBH", exit_level(5), enter_level(4),
             via=[enter_level(26)]),
    # --- out of the Bowser 1 arena (its exit lands in the lobby) ----------
    movement("seg:bowser1->bob", "Bowser 1 → BoB",
             exit_level(30), enter_level(9)),
    movement("seg:bowser1->wf", "Bowser 1 → WF",
             exit_level(30), enter_level(24)),
    movement("seg:bowser1->ccm", "Bowser 1 → CCM",
             exit_level(30), enter_level(5)),
    movement("seg:bowser1->ssl", "Bowser 1 → SSL",
             exit_level(30), enter_level(8),
             via=[enter_area(BASEMENT)]),
    movement("seg:bowser1->ddd", "Bowser 1 → DDD (Crackslide)",
             exit_level(30), enter_level(23),
             via=[enter_area(BASEMENT)]),
    movement("seg:bowser1->bitfs", "Bowser 1 → BitFS (SBLJ / DDD Skip)",
             exit_level(30), enter_level(19),
             via=[enter_area(BASEMENT)]),
    # --- courtyard (BBH exits to level 26, not to the castle interior) ----
    # Load-bearing under loose matching too (2026-07-28, Task 19): removing
    # this waypoint makes tests/test_defaults_corpus.py's own-walk test fail
    # to arm at all -- can_run_from (segments.py) is unconditional regardless
    # of match_mode, and area_enter(level=6, area=BASEMENT)'s precondition
    # only fires from level 6. BBH's exit lands at level 26 (the courtyard),
    # so a plain end trigger is simply unfireable from the arm position; the
    # waypoint's level_enter(6) fires from anywhere but its own destination
    # and gets the arm past the courtyard.
    #
    # It PINS the courtyard as its source since 2026-08-02, when every course
    # gained its pause exit into the castle. Without that, a single
    # `level_changed 4 -> 6` -- BBH's own pause exit -- satisfies both this
    # def's start AND its first waypoint, and closures run before arming: the
    # def would arm in the lobby still owing a waypoint that can now only fire
    # if he leaves the castle and comes back. `tracking/lint.py`'s `unfireable`
    # rule names that trap and was what caught it.
    movement("seg:bbh->basement", "BBH → Basement",
             exit_level(4), enter_area(BASEMENT),
             via=[enter_level(6, frm=26, to_subarea=LOBBY)]),
    movement("seg:bbh->ddd", "BBH → DDD",
             exit_level(4), enter_level(23),
             via=[enter_level(6, frm=26, to_subarea=LOBBY),
                  enter_area(BASEMENT)]),
    # --- basement ---------------------------------------------------------
    movement("seg:mips1->ssl", "MIPS (1st) → SSL",
             grab_star(0, 3), enter_level(8)),
    movement("seg:ssl->lll", "SSL → LLL", exit_level(8), enter_level(22)),
    movement("seg:ssl->hmc", "SSL → HMC", exit_level(8), enter_level(7)),
    movement("seg:lll->hmc", "LLL → HMC", exit_level(22), enter_level(7)),
    movement("seg:lll->ddd", "LLL → DDD", exit_level(22), enter_level(23)),
    movement("seg:hmc->lll", "HMC → LLL", exit_level(7), enter_level(22)),
    movement("seg:hmc->ddd", "HMC → DDD", exit_level(7), enter_level(23)),
    # A TRICK, and its shortest path is NOT the route being practised: you
    # re-enter HMC and pause-exit, which lands in the Lobby and skips the
    # Basement -> Lobby walk. Griffin's rule for which course gets re-entered
    # (2026-08-02): "it's usually the starting stage (or in the case of bowser
    # stages, it's the actual bowser course, not the bowser fight)."
    movement("seg:hmc->rr", "HMC → RR (re-entry, pause exit)",
             exit_level(7), enter_level(15),
             via=[enter_level(7, frm=6),
                  enter_level(6, frm=7, to_subarea=LOBBY),
                  enter_area(UPSTAIRS)]),
    movement("seg:mips2->hmc", "MIPS (2nd) → HMC",
             grab_star(0, 4), enter_level(7)),
    # VCUtM opens off the castle GROUNDS, not the basement MIPS is grabbed
    # in, so the grounds are a step of this route rather than scenery.
    movement("seg:mips2->vcutm", "MIPS (2nd) → VCUtM",
             grab_star(0, 4), enter_level(18),
             via=[enter_level(16)]),
    movement("seg:vcutm->ccm", "VCUtM → CCM",
             exit_level(18), enter_level(5),
             via=[enter_level(6, frm=16, to_subarea=LOBBY)]),
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
    # Re-enters BitFS -- level 19, the COURSE, never arena 33 -- and pause
    # exits to the Lobby, skipping the Basement -> Lobby walk.
    movement("seg:ddd->wdw", "DDD → WDW (BitFS re-entry, pause exit)",
             exit_level(23), enter_level(11),
             via=[enter_level(19, frm=6),
                  enter_level(6, frm=19, to_subarea=LOBBY),
                  enter_area(UPSTAIRS)]),
    # --- out of the Bowser 2 arena (its exit lands in the basement) -------
    movement("seg:bowser2->ddd", "Bowser 2 → DDD",
             exit_level(33), enter_level(23)),
    movement("seg:bowser2->wdw", "Bowser 2 → WDW",
             exit_level(33), enter_level(11),
             via=[enter_area(LOBBY), enter_area(UPSTAIRS)]),
    # Griffin's own dictation of this route (2026-08-02): "I go from Bowser
    # two back into Bowser in the Fire Sea to the lobby, then to the
    # upstairs." The arena exit lands in the Basement; BitFS; the pause exit
    # puts him in the Lobby without walking there. Run the DIRECT way
    # (basement -> lobby -> upstairs) this records NOTHING, which is the
    # point: "That is a fixed path, and there are no other options."
    #
    # The BitFS step PINS its source. Losing the Bowser 2 fight is a single
    # `level_changed 33 -> 19`, which would otherwise satisfy both this
    # def's start and its first step -- the unfireable trap.
    movement("seg:bowser2->upstairs", "Bowser 2 → Upstairs",
             exit_level(33), enter_area(UPSTAIRS),
             via=[enter_level(19, frm=6),
                  enter_level(6, frm=19, to_subarea=LOBBY)]),
    # Real walk (Task 0017, live report): finish Bowser 2 -> back into BitFS
    # -> pause exit to the basement -> lobby -> upstairs -> BLJs -> BitS.
    # Every one of those steps would cancel a STRICT definition; a plain
    # loose def (no waypoints) passes them all through transparently, which
    # is the whole reason this is finally expressible (Task 20, spec
    # 2026-07-28-multi-step-segments) -- no via chain needed, same shape as
    # every other Bowser-arena-exit movement above. NOT referenced by any
    # route (route regression fix, 2026-07-28): the 0/1-star routes' tail
    # briefly collapsed through this single movement, but that lost the
    # named "-> Upstairs" / "Endless Staircase BLJ" splits their runners
    # care about, and a real-walk test proved the ORIGINAL two-step sequence
    # (seg:bowser2->upstairs + seg:bits-entry) already survives this exact
    # detour under loose matching -- see _LOW_STAR_TAIL in
    # corpus_routes_main.py. Kept as a standalone, independently-valid
    # movement (proven by test_bowser_2_to_bits_survives_the_whole_detour;
    # exempted from the orphan guard in test_no_movement_is_left_
    # unreferenced) rather than deleted -- it was requested by name and its
    # shape is correct regardless of whether a route uses it.
    movement("seg:bowser2->bits", "Bowser 2 → BitS",
             exit_level(33), enter_level(21),
             via=[enter_level(19, frm=6),
                  enter_level(6, frm=19, to_subarea=LOBBY),
                  enter_area(UPSTAIRS)]),
    # --- upstairs ---------------------------------------------------------
    movement("seg:wdw->thi", "WDW → THI", exit_level(11), enter_level(13)),
    movement("seg:thi->ttm", "THI → TTM", exit_level(13), enter_level(36)),
    movement("seg:ttm->sl", "TTM → SL", exit_level(36), enter_level(10)),
    # Re-enters SL and pause-exits, skipping the Upstairs -> Lobby walk.
    movement("seg:sl->basement", "SL → Basement (re-entry, pause exit)",
             exit_level(10), enter_area(BASEMENT),
             via=[enter_level(10, frm=6),
                  enter_level(6, frm=10, to_subarea=LOBBY)]),
    movement("seg:sl->rr", "SL → RR", exit_level(10), enter_level(15)),
    movement("seg:sl->wmotr", "SL → WMotR", exit_level(10), enter_level(31)),
    movement("seg:wmotr->ttc", "WMotR → TTC", exit_level(31), enter_level(14)),
    movement("seg:rr->ttc", "RR → TTC", exit_level(15), enter_level(14)),
    movement("seg:ttc->rr", "TTC → RR", exit_level(14), enter_level(15)),
    movement("seg:rr->bits", "RR → BitS", exit_level(15), enter_level(21)),
    movement("seg:ttc->bits", "TTC → BitS", exit_level(14), enter_level(21)),
]

# --- Bowser-stage reds -> pipe (Task 20, spec 2026-07-28-multi-step-segments;
# RESHAPED 2026-07-29, same spec, live report -- see module docstring below)
# "8 red coins levels in bowser stages. When you get this star, the level
# doesn't end -- you have to go into the pipe to finish the level." -- the
# reference autosplitter's own default (it waits for pipe entry), and the
# case its issue tracker shows it repeatedly getting wrong. Ends on
# warp_entered, not star_grabbed, so this does NOT trip the run-ordering
# trap and could be filed as a plain Castle Movement -- it stays UNGUARDED
# instead (see mechanic()'s docstring): there is exactly one pipe per stage,
# no route to scope against, matching the existing pipe-entry trio's
# always-armed shape more closely than the 56 route-scoped movements'.
#
# Starting on the star grab (as this shipped originally) was the live-reported
# bug: it could not be OFFERED before the grab, and it timed star->pipe (his
# log: successes at 237/378 frames, 7.9s/12.6s) instead of the whole stage.
# The reshape starts on stage entry -- the SAME any-of idiom
# corpus_legacy.py's seg:bitdw-pipe/etc. already use ([level_enter, anchor],
# copied rather than invented) -- and makes the reds star a WAYPOINT: the
# span the user actually wants is stage entry -> reds star -> pipe. This is
# also what makes both (1) the star grab alone and (2) this segment armed
# from the SAME event, satisfying his rule "if 1 is armed, 2 should always be
# armed" with no picker involved.
#
# match_mode="strict" (not the corpus's usual "loose"), because every Bowser
# course has EXACTLY ONE collectible star (STAR_NAMES[16/17/18] == ("8 Red
# Coins",), addresses.py) -- so the only star_grabbed event reachable before
# the pipe IS this def's own waypoint, and _feed_waypoint's major-action
# cancel (segments.py) can never misfire on an incidental OTHER star the way
# it would in a main course (see HUNDRED_COIN_EXITS below, which stays loose
# for exactly that reason). Strict also means leaving the stage via the pause
# menu without reaching the pipe -- a real level_changed matching neither the
# waypoint nor the end -- genuinely cancels the attempt instead of leaving it
# armed for an unrelated later visit, matching the legacy pipe-entry trio's
# own (now Exclusive) idiom more closely than "loose" would.
REDS_TO_PIPE = [
    mechanic("seg:reds->pipe:bitdw", "BitDW — 8 Red Coins → Pipe",
             [enter_level(17), anchor(17)], enter_warp(17), CASTLE_MOVEMENT,
             match_mode="strict", via=[grab_star(16, 0)]),
    mechanic("seg:reds->pipe:bitfs", "BitFS — 8 Red Coins → Pipe",
             [enter_level(19), anchor(19)], enter_warp(19), CASTLE_MOVEMENT,
             match_mode="strict", via=[grab_star(17, 0)]),
    mechanic("seg:reds->pipe:bits", "BitS — 8 Red Coins → Pipe",
             [enter_level(21), anchor(21)], enter_warp(21), CASTLE_MOVEMENT,
             match_mode="strict", via=[grab_star(18, 0)]),
]

# --- 100-coin star -> the star that actually exits (Task 20; RESHAPED
# 2026-07-29, same spec, live report -- see module docstring below) ---------
# "In normal stages, you don't exit the stage when you grab a 100 coins
# star, you can keep playing and must find another star to actually exit
# the level." The end trigger DELIBERATELY ends on star_grabbed. The rule it
# looks like it breaks -- a movement may start on a star grab but must never
# end on one -- exists because RunTracker (runs.py::_apply) only ever
# considers the CURRENT route step and projection.py closes stars-then-
# segments within one event, so a misordered ROUTE STEP stalls a run
# permanently and silently. That is a property of being a route step:
# nothing here is one (no seeded route references any of these seed_keys,
# and they carry no guard), so there is no run to stall. Hence its own
# category (HUNDRED_COIN_EXIT) rather than Castle Movement -- it never enters
# corpus_movements.MOVEMENTS, so it never reaches the tests built on "a
# movement never ends on star_grabbed" (test_no_movement_starts_and_ends_on_
# the_SAME_event and friends).
#
# The START used to be the 100-coin grab itself, which is the SAME live-
# reported bug as reds->pipe (2026-07-29): unselectable before the grab, and
# it timed 100-coins->exit rather than "the whole course visit" the user
# asked for ("Timing starts on reset -> timer ends after grabbing the final
# exit-star"). The reshape starts on stage entry (the same
# [level_enter, attempt_anchor] any-of idiom every movement/legacy row uses)
# and makes the 100-coin grab a WAYPOINT instead.
#
# match_mode is "strict" (RESHAPED AGAIN 2026-07-29, later the same day, live
# report): shipped "loose" first, on the reasoning that a main course's six
# OTHER named stars would falsely cancel a strict/waypoint dispatch if
# grabbed incidentally while hunting for coins. That reasoning was WRONG --
# checked, not assumed, against actual SM64 behaviour: grabbing ANY star
# EXITS the course (the star-grab cutscene returns Mario to the castle),
# except the 100-coin star specifically, which is the one star SM64 lets you
# keep playing through -- literally the asymmetry this family is named for
# ("you don't exit the stage when you grab a 100 coins star... you must find
# another star to actually exit the level"). So there is no real scenario
# where an ordinary star is grabbed WITHOUT the course also ending; a strict
# waypoint def's major-action cancel on that star grab is not a false
# positive, it is the correct rule arriving slightly before the level_changed
# that would have cancelled it anyway.
#
# "Loose" was ALSO the wrong span for a different reason, found live: a
# loose def is transparent to level_changed by design (the whole point of
# the mode), so leaving the course never disarmed it -- a player who left
# DDD for BitFS still saw "DDD -- 100 Coins -> Exit" reading RUNNING,
# tracking a course visit that had become physically impossible to finish.
# "Strict" (which _feed_waypoint dispatches to, same as reds->pipe) fixes
# both: `area_changed` stays transparent (a course's OWN subareas, like
# DDD's submarine bay, must not cancel a visit that legitimately crosses
# them), while a real-edge `level_changed` that isn't the next waypoint
# cancels -- "deactivate when I leave the stage", verbatim. It also removes
# the loose-mode staleness deadline for this family entirely
# (segments.py::_deadline_for returns None for any non-loose def) -- a real
# 100-coin hunt has no natural time limit the way a castle movement does, so
# this family never needed a budget check to begin with, and the earlier
# concern about a slow completion expiring one no longer applies.
#
# Every main course (1-15) has six numbered stars (0-5) plus 100 Coins at
# star_id 6 (addresses.star_count/star_name own that rule); end_triggers
# lists the six alternatives explicitly since the vocabulary has no "any
# star but this one" clause. The level column is COURSE_BY_LEVEL's inverse
# (addresses.py) -- the level the def arms in and the 100-coin waypoint's
# star_grabbed fires from (segments.py::fires_from's course->level check).
_MAIN_COURSES = [
    (1, "BoB", 9), (2, "WF", 24), (3, "JRB", 12), (4, "CCM", 5),
    (5, "BBH", 4), (6, "HMC", 7), (7, "LLL", 22), (8, "SSL", 8),
    (9, "DDD", 23), (10, "SL", 10), (11, "WDW", 11), (12, "TTM", 36),
    (13, "THI", 13), (14, "TTC", 14), (15, "RR", 15),
]

HUNDRED_COIN_EXITS = [
    mechanic(f"seg:100c->exit:{abbrev.lower()}", f"{abbrev} — 100 Coins → Exit",
             [enter_level(level), anchor(level)],
             [grab_star(course, star_id) for star_id in range(6)],
             HUNDRED_COIN_EXIT, match_mode="strict", via=[grab_star(course, 6)])
    for course, abbrev, level in _MAIN_COURSES
]
