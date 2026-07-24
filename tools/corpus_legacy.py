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
"""
from corpus_vocab import (BOWSER_FIGHTS, CASTLE_MOVEMENT, TRICKS, UPSTAIRS,
                          anchor, enter_area, enter_level, enter_warp,
                          exit_level, grab_key, spawn)


def _seg(seed_key, name, start, end, category):
    return {"seed_key": seed_key, "name": name, "enabled": True,
            "start_triggers": start, "end_triggers": end,
            "waypoints": [], "guards": [], "category": category}


SEGMENTS = [
    _seg("seg:lblj", "LBLJ",
         [enter_level(6, frm=16), anchor(6, area=1)],
         [enter_level(17)], TRICKS),
    _seg("seg:mips-clip", "MIPS Clip",
         [exit_level(7, to=6)], [enter_level(23)], TRICKS),
    _seg("seg:lakitu-skip", "Lakitu Skip",
         [spawn(16)], [enter_level(6)], TRICKS),
    _seg("seg:bits-entry", "BitS Entry",
         [enter_area(UPSTAIRS)], [enter_level(21)], CASTLE_MOVEMENT),
    _seg("seg:bitdw-pipe", "BitDW Pipe Entry",
         [enter_level(17), anchor(17)], [enter_warp(17)], CASTLE_MOVEMENT),
    _seg("seg:bitfs-pipe", "BitFS Pipe Entry",
         [enter_level(19), anchor(19)], [enter_warp(19)], CASTLE_MOVEMENT),
    _seg("seg:bits-pipe", "BitS Pipe Entry",
         [enter_level(21), anchor(21)], [enter_warp(21)], CASTLE_MOVEMENT),
    _seg("seg:bowser-1", "Bowser 1",
         [enter_level(30), anchor(30)], [grab_key(30)], BOWSER_FIGHTS),
    _seg("seg:bowser-2", "Bowser 2",
         [enter_level(33), anchor(33)], [grab_key(33)], BOWSER_FIGHTS),
    _seg("seg:bowser-3", "Bowser 3",
         [enter_level(34), anchor(34)], [grab_key(34)], BOWSER_FIGHTS),
]
