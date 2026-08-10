"""The ten pre-existing seeded segments, carried forward verbatim.

These predate the corpus (storage/db.py MIGRATIONS v4, with the v5 LBLJ and v6
Bowser 3 repairs folded into their corrected values) and are already installed
on every live db. Reconcile overwrites untouched seeded rows, so ANY drift here
silently rewrites a real user's segments at startup —
tests/test_build_defaults_seed.py pins them and test_seed_reconcile.py's
real-bundled-seed gate proves reconcile leaves an existing install untouched.

They stay UNGUARDED (no in_active_route): their always-arm behaviour is what
the stage banner's Bowser-course mutual exclusion and the standalone practice
flows depend on.

The three Bowser pipe-entry rows carry match_mode="exclusive" since 2026-07-29
(corpus reshape, live report 2026-07-29) -- "the pipe entrance without going
for the reds" is now what this def MEANS (its sibling seg:reds->pipe:* in
corpus_movements.py covers "grab the reds star, then the pipe"), and Exclusive
is exactly "cancels if I grab a star or key" (segments.py MATCH_MODES), which
is the rule the user gave verbatim: "it's fine if you grab coins... If we grab
the star, then we clearly weren't doing no-reds." The other seven legacy rows
are unchanged and keep no match_mode key (meaning "strict" through reconcile's
own default), so "not s['guards']" alone no longer tells the whole legacy
group apart from the reds->pipe/100c->exit mechanics -- see
tests/test_build_defaults_seed.py::test_every_non_movement_defs_match_mode
for the taxonomy this now takes.

**default_strat, six of the ten (untagged-PB fix, live report 2026-07-31):**
a def with no default records every attempt with `strat_tag=None` (projection
caveat 17 only pre-seeds `strat_by_segment` from a REAL default), and an
untagged PB can never be found by `views.py::current_pbs_by_strat` -- the
Bowser 1 report ("PB shows 0'26"30, rank shows Capless 5") traced to exactly
this: `segment_defs.default_strat` was NULL for all ten legacy rows, Bowser 1
included, though its rank standards define exactly ONE strategy ("Standard").
Checked against `data/rank_standards.seed.json` (the bundled community data,
`segment:<N>` keyed 1-10 in this file's own insertion order): LBLJ,
MIPS Clip, Lakitu Skip, BitS Entry, Bowser 1 and Bowser 2 EACH define only
"Standard" -- stamping it is not a guess, exactly the reasoning that already
justifies every castle movement's default (`tools/build_defaults_seed.py`'s
own `_movement_row`). BitDW/BitFS/BitS Pipe Entry (2-4 real strategies each)
and Bowser 3 ("Normal File"/"120 Star File") stay at None deliberately --
forcing either one would credit an old untagged time to a strategy the
player may never have run; `tests/test_build_defaults_seed.py`'s own
docstring used to say the opposite ("the Bowser fights, LBLJ have real
competing strategies") for all seven ambiguous-or-not rows alike, which was
already false for three of them by the time this comment was written -- a
docstring is not a test, so nothing failed while it drifted.
"""
from corpus_vocab import (BOWSER_FIGHTS, CASTLE_MOVEMENT, STANDARD_STRAT,
                          TRICKS, UPSTAIRS, anchor, enter_area, enter_entrance,
                          enter_level, enter_warp, exit_level, grab_key,
                          moment, spawn)


def _seg(seed_key, name, start, end, category, match_mode=None,
         default_strat=None):
    row = {"seed_key": seed_key, "name": name, "enabled": True,
           "start_triggers": start, "end_triggers": end,
           "waypoints": [], "guards": [], "category": category}
    if match_mode is not None:
        row["match_mode"] = match_mode
    if default_strat is not None:
        row["default_strat"] = default_strat
    return row


SEGMENTS = [
    _seg("seg:lblj", "LBLJ",
         [enter_level(6, frm=16), anchor(6, area=1)],
         [enter_entrance(17)], TRICKS, default_strat=STANDARD_STRAT),
    _seg("seg:mips-clip", "MIPS Clip",
         [exit_level(7, to=6)], [enter_entrance(23)], TRICKS,
         default_strat=STANDARD_STRAT),
    # LAKITU SKIP ENDS AT THE DOOR, not at the castle load (task 0026).
    # We read 7"33 where the community reads 6"13, because entering level 6
    # is the LOAD and the community's split is Mario grabbing the door.
    #
    # The START is unchanged and that is deliberate: task 0026 says "timer
    # starts when mario can move around", and `spawn(16)` already IS that
    # frame -- `spawned` fires on the edge OUT of ACT_INTRO_CUTSCENE, which
    # addresses.py calls "the canonical Lakitu-skip timing start"
    # (live-verified 2026-06-12). Moving it would have shifted a number that
    # is already right and left the live gate scoring two changes at once.
    #
    # Ordinal 1: the front door is the first door of the run. Pinning it
    # means a practice attempt that opens some other door first records
    # nothing rather than a wrong time, which is the stated preference --
    # "we should fail if we deviate from the steps".
    _seg("seg:lakitu-skip", "Lakitu Skip",
         [spawn(16)], [moment("door_open", level=16, ordinal=1)],
         TRICKS, default_strat=STANDARD_STRAT),
    _seg("seg:bits-entry", "BitS Entry",
         [enter_area(UPSTAIRS)], [enter_entrance(21)], CASTLE_MOVEMENT,
         default_strat=STANDARD_STRAT),
    # BitDW/BitFS/BitS Pipe Entry: 2-4 real competing strategies each in the
    # standards (Standard/Framewalk; Pole Glitch/Ultimate/BLJ/Zero Cycle;
    # Right TJWK/Left TJWK/TAS LJ + Ult/Pole) -- no default_strat, deliberately.
    _seg("seg:bitdw-pipe", "BitDW Pipe Entry",
         [enter_level(17), anchor(17)], [enter_warp(17)], CASTLE_MOVEMENT,
         match_mode="exclusive"),
    _seg("seg:bitfs-pipe", "BitFS Pipe Entry",
         [enter_level(19), anchor(19)], [enter_warp(19)], CASTLE_MOVEMENT,
         match_mode="exclusive"),
    _seg("seg:bits-pipe", "BitS Pipe Entry",
         [enter_level(21), anchor(21)], [enter_warp(21)], CASTLE_MOVEMENT,
         match_mode="exclusive"),
    _seg("seg:bowser-1", "Bowser 1",
         [enter_level(30), anchor(30)], [grab_key(30)], BOWSER_FIGHTS,
         default_strat=STANDARD_STRAT),
    _seg("seg:bowser-2", "Bowser 2",
         [enter_level(33), anchor(33)], [grab_key(33)], BOWSER_FIGHTS,
         default_strat=STANDARD_STRAT),
    # Bowser 3: two real competing strategies ("Normal File"/"120 Star File")
    # -- no default_strat, deliberately (same reasoning as the pipe trio).
    _seg("seg:bowser-3", "Bowser 3",
         [enter_level(34), anchor(34)], [grab_key(34)], BOWSER_FIGHTS),
]
