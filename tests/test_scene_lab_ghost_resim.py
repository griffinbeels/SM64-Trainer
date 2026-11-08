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

# Where the US map puts them (the module reads the map; these are the test's own copy of it).
SYMBOLS = {"gMarioStates": 0x8033B170, "gControllerPads": 0x8033AFF8, "gCurrActNum": 0x8033BAC8,
           "gGlobalTimer": 0x8032D5D4, "gAreaUpdateCounter": 0x8032DF08, "gCurrLevelNum": 0x8032DDF8,
           "gCurrAreaIndex": 0x8033BACA}


def regions(timer=3489, counter=2194, level=24, area=1, action=0x1924, buttons=0x20, act=1):
    """Big-endian regions as a scene's first frame carries them."""
    spans = {0x32D560: np.zeros(0x1000, np.uint8), 0x33AF90: np.zeros(0x1000, np.uint8)}

    def put(address, fmt, value):
        at = address & 0x1FFFFFFF
        begin = max(low for low in spans if low <= at)
        struct.pack_into(fmt, spans[begin], at - begin, value)

    put(SYMBOLS[gr.GAME_TIMER], ">I", timer)
    put(SYMBOLS[stage_sim.CURR_LEVEL], ">h", level)
    put(SYMBOLS[gr.AREA_COUNTER], ">H", counter)
    put(SYMBOLS["gControllerPads"], ">H", buttons)
    put(SYMBOLS["gMarioStates"] + 0x0C, ">I", action)
    put(SYMBOLS[stage_sim.CURR_AREA], ">h", area)
    put(SYMBOLS["gCurrActNum"], ">h", act)
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
    """His decision (2026-11-08): every start makes save states; a rebuilt or downloaded one only before its
    parting frame, and only after each slot's load check."""
    for start in (gr.GHOST_START, gr.OWN_SCENE):
        assert gr.save_states(start) == {"allowed": True, "reason": None}
    for start in (gr.ANOTHER_RESET, gr.SIMULATED_RESET, gr.DOWNLOADED_START):
        assert gr.save_states(start, frame=10, parting=20) == {"allowed": True, "reason": None, "until": 20, "check": "load"}
        assert gr.save_states(start, frame=500, parting=None)["check"] == "load"
        refused = gr.save_states(start, frame=20, parting=20)
        assert refused["allowed"] is False and "no longer matches the ghost" in refused["reason"]
    monkeypatch.setitem(gr.SAVE_STATES, gr.SIMULATED_RESET, False)
    assert gr.save_states(gr.SIMULATED_RESET, frame=1, parting=None)["allowed"] is False


def test_a_byte_exact_scene_already_made_is_the_answer(tmp_path):
    """His own scene's build saves a start file; the next ask must not become a new build from it."""
    import json
    lab = gr.GhostScenes(tmp_path, tmp_path, scenes_dir=None, starts_dir=None,
                         stages=SimpleNamespace(symbols=SYMBOLS, rom_path=tmp_path / "rom"))
    sha = "ab" * 32
    for start, exact in (("own_scene", "bytes"), ("simulated_reset", "mario")):
        (tmp_path / f"ghost-{sha[:16]}-{start}-k.sm64scene").write_bytes(b"package")
        (tmp_path / f"ghost-{sha[:16]}-{start}-k.json").write_text(json.dumps(
            {"version": gr.RESIM_VERSION, "ghost_sha256": sha, "start": start, "exact": exact}), encoding="utf-8")
    path, info = lab._cached_exact(sha)
    assert info["start"] == "own_scene" and path.name.startswith(f"ghost-{sha[:16]}-own_scene")
    assert lab._cached_exact("cd" * 32) is None
    (tmp_path / f"ghost-{sha[:16]}-own_scene-k.json").write_text(json.dumps(
        {"version": gr.RESIM_VERSION + 1, "ghost_sha256": sha, "exact": "bytes"}), encoding="utf-8")
    assert lab._cached_exact(sha) is None                     # another version's scene is not this one's


def test_every_start_has_a_switch_row_and_an_exactness():
    assert set(gr.SAVE_STATES) == set(gr.STARTS) == set(gr.EXACT)
