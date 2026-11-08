"""Choosing a ghost's re-simulation start and the Save states switch (tools/scene_lab/ghost_resim.py).
The simulation itself needs his ROM and a recorded session: its measured runs are in the module's
docstring; these pin the decisions that can drift without them."""
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import ghost_resim as gr  # noqa: E402
import stage_sim  # noqa: E402

MARIO, PADS, ACT = 0x8033B170, 0x8033AFF8, 0x8033BAC8
SYMBOLS = {"gMarioStates": MARIO, "gControllerPads": PADS, "gCurrActNum": ACT}


def regions(timer=3489, counter=2194, level=24, area=1, action=0x1924, buttons=0x20, act=1):
    """Big-endian regions as a scene's first frame carries them."""
    spans = {0x32D560: np.zeros(0x1000, np.uint8), 0x33AF90: np.zeros(0x1000, np.uint8)}

    def put(address, fmt, value):
        at = address & 0x1FFFFFFF
        begin = max(low for low in spans if low <= at)
        struct.pack_into(fmt, spans[begin], at - begin, value)

    put(gr.GAME_TIMER, ">I", timer)
    put(stage_sim.CURR_LEVEL, ">h", level)
    put(gr.AREA_COUNTER, ">H", counter)
    put(PADS, ">H", buttons)
    put(MARIO + 0x0C, ">I", action)
    put(stage_sim.CURR_AREA, ">h", area)
    put(ACT, ">h", act)
    return list(spans.items())


def record(timer=3489, counter=2194, level=24, area=1, action=0x1924, posed=True):
    raw = bytearray(1300)
    struct.pack_into("<3f", raw, gr.POSE_AT, 1.0, 2.0, 3.0)
    struct.pack_into("<I", raw, gr.POSE_ACTION, action)
    return SimpleNamespace(global_timer=timer, area_update_counter=counter, level=level, area=area, raw=bytes(raw),
                           valid_fields=gr.VALID_POSE if posed else 0)


def clip(**changes):
    return SimpleNamespace(frames=[record(**changes)])


def test_his_own_scene_is_the_one_whose_first_frame_is_the_first_record():
    assert gr.words_match(gr.probe(regions(), SYMBOLS), clip())


@pytest.mark.parametrize("change", [{"timer": 3490}, {"counter": 2195}, {"level": 9}, {"area": 2}, {"action": 0x0C400201}])
def test_any_other_scene_is_not_his_own(change):
    assert not gr.words_match(gr.probe(regions(), SYMBOLS), clip(**change))


def test_another_take_counts_as_a_reset_only_when_it_begins_with_the_reset():
    first = record()
    assert gr.words_reset(gr.probe(regions(timer=5005, counter=3710), SYMBOLS), first)
    assert not gr.words_reset(gr.probe(regions(buttons=0), SYMBOLS), first)          # a take begun mid-play
    assert not gr.words_reset(gr.probe(regions(action=0x0C400201), SYMBOLS), first)
    assert not gr.words_reset(gr.probe(regions(level=9), SYMBOLS), first)


def test_the_act_comes_from_the_scene():
    assert gr.probe(regions(act=3), SYMBOLS)["act"] == 3


def test_a_recorded_pose_reads_position_and_action():
    position, action = gr.pose_of(record(action=0x03000888))
    assert position.tolist() == [1.0, 2.0, 3.0] and action == 0x03000888
    assert gr.pose_of(record(posed=False)) is None


def test_save_states_follow_the_one_switch(monkeypatch):
    for start in (gr.GHOST_START, gr.OWN_SCENE):
        assert gr.save_states(start)["allowed"] is True
    for start in (gr.ANOTHER_RESET, gr.SIMULATED_RESET):
        refused = gr.save_states(start)
        assert refused["allowed"] is False and "platforms" in refused["reason"]
    monkeypatch.setitem(gr.SAVE_STATES, gr.SIMULATED_RESET, "before parting")
    assert gr.save_states(gr.SIMULATED_RESET, frame=10, parting=20)["allowed"] is True
    assert gr.save_states(gr.SIMULATED_RESET, frame=20, parting=20)["allowed"] is False
    assert gr.save_states(gr.SIMULATED_RESET, frame=500, parting=None)["allowed"] is True


def test_every_start_has_a_switch_row_and_an_exactness():
    assert set(gr.SAVE_STATES) == set(gr.STARTS) == set(gr.EXACT)
