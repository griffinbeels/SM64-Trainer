"""HMC Toad clocks from the Ultimate Sheet, each a separate practice entity.

The star grab is a required middle event. Both clocks end at the HMC entrance,
and neither can grade the shorter pickup-only castle star.
"""
from corpus_vocab import (BASEMENT, ROUTE_SCOPED, STANDARD_STRAT, enter_entrance,
                          exit_level, grab_star, moment)


def _toad(seed_key, label, start):
    return {"seed_key": seed_key, "name": label, "enabled": True,
            "start_triggers": [start],
            "waypoints": [[grab_star(0, 0)]],
            "end_triggers": [enter_entrance(7)],
            "guards": ROUTE_SCOPED, "category": "Toad Segment",
            "default_strat": STANDARD_STRAT, "match_mode": "strict",
            "clock_start": "move",
            "sheet_target": {"section": "★ HMC", "label": label}}


SEGMENTS = [
    _toad("seg:hmc-toad-result", "HMC result - Enter HMC (Toad)",
          exit_level(7, to=6)),
    _toad("seg:hmc-toad-door", "HMC door - Enter HMC (Toad)",
          moment("door_open", level=6, area=BASEMENT,
                 landmark="6:3:bhvDoor:1126,-1074,-2661")),
]
