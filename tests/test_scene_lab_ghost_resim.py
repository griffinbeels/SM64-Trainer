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


RESET_RAM, BOOT_RAM = np.full(16, 7, np.uint8), np.zeros(16, np.uint8)


class ResetStages(SimpleNamespace):
    """Stages as a scene's start sees them: a reset per level (refused for the castle's own levels), the boot."""

    def reset(self, level, area, act):
        self.asked.append((level, area, act))
        if level == 6:
            raise stage_sim.StageError("The simulated game never reached level 6 area 1 act 1.")
        return RESET_RAM

    def boot(self):
        return BOOT_RAM, 0

    def course_of_level(self, level):
        return {24: 2, 17: 16, 6: 0}[level]


def _own_scene(tmp_path, monkeypatch, *, ghost_act: int | None = None, scene_act: int | None = 2, level: int = 24):
    """His own scene's start for the test clip: its first frame names `scene_act` in `level`; the ghost's star
    names `ghost_act`."""
    words = {"timer": 3489, "counter": 2194, "level": level, "area": 1, "action": 0x1924, "buttons": 0x20,
             "act": scene_act}
    (tmp_path / "take-1.sm64scene").write_bytes(b"scene")
    monkeypatch.setattr(gr.package, "open_package", lambda path, rom: None)
    monkeypatch.setattr(gr, "first_state", lambda pkg: ([], None))
    monkeypatch.setattr(gr, "probe", lambda regions, symbols: dict(words))
    stages = ResetStages(symbols=SYMBOLS, rom_path=tmp_path / "rom", map_path=tmp_path / "map", asked=[])
    scenes = gr.GhostScenes(tmp_path, tmp_path / "cache", scenes_dir=[tmp_path], starts_dir=None, stages=stages)
    own = next(scenes.starts(clip(level=level), "ab" * 32, ghost_act))
    assert own.kind == gr.OWN_SCENE
    return own, stages


def test_a_scene_start_lies_over_its_levels_reset_so_the_level_draws(tmp_path, monkeypatch):
    """The first real pictures (2026-11-09): a scene's start laid over the castle's boot RAM drew the level black (its
    textures load outside the scene's regions and main pool; 0 of 396 frames drew his recording's bytes, 28 with
    the reset under it and 4,787 bytes off in all). His own scene and another take lie over the reset of their
    level, area and the act their first frame names."""
    own, stages = _own_scene(tmp_path, monkeypatch)
    assert own.ram.tolist() == RESET_RAM.tolist() and stages.asked == [(24, 1, 2)]
    assert own.detail["base"] == "reset 24-1-2"


def test_a_scene_naming_no_act_takes_the_ghosts_star_act_or_is_refused(tmp_path, monkeypatch):
    """No silent guess: a course with an act selector whose scene names no act takes the ghost's star act (and says
    so), else the start is refused and the next one is tried; a course without acts uses act 1, its reset's key;
    the castle's own levels, which make no reset, lie over the boot and say why."""
    own, stages = _own_scene(tmp_path, monkeypatch, ghost_act=3, scene_act=None)
    assert own.ram.tolist() == RESET_RAM.tolist() and stages.asked == [(24, 1, 3)]
    assert own.detail["act_from"].startswith("the ghost's star")
    own, stages = _own_scene(tmp_path, monkeypatch, scene_act=0)
    with pytest.raises(stage_sim.StageError, match="names no act"):
        _ram = own.ram
    own, stages = _own_scene(tmp_path, monkeypatch, level=17, scene_act=0)
    assert own.ram.tolist() == RESET_RAM.tolist() and stages.asked == [(17, 1, 1)]
    own, stages = _own_scene(tmp_path, monkeypatch, level=6, scene_act=0)
    assert own.ram.tolist() == BOOT_RAM.tolist() and own.detail["base"] == "boot" and "level 6" in own.detail["base_reason"]


def test_a_start_is_the_roms_own_so_the_other_cartridges_start_gives_way_to_the_simulated_reset(tmp_path, monkeypatch):
    """Either cartridge re-simulates (2026-11-09), but a start's bytes belong to the ROM that made it: a start file
    made on the other cartridge is refused with a sentence, and the lab's own simulated reset is the start."""
    import hashlib
    rom = b"the lab's own ROM"
    stages = ResetStages(symbols=SYMBOLS, rom=rom, rom_path=tmp_path / "rom", asked=[],
                         key=lambda level, area, act: f"{level}-{area}-{act}")
    scenes = gr.GhostScenes(tmp_path, tmp_path / "cache", scenes_dir=None, starts_dir=tmp_path, stages=stages)
    scenes.refusals = []
    for maker, first_kind in ((b"another cartridge", gr.SIMULATED_RESET), (rom, gr.GHOST_START)):
        saved = SimpleNamespace(rom_sha256=hashlib.sha256(maker).hexdigest(), pool_base=None, level=24, area=1, act=1)
        monkeypatch.setattr(gr.start_frame, "read_start",
                            lambda folder, sha, downloaded=False, rom=None, saved=saved: None if downloaded else saved)
        assert next(scenes.starts(clip(), "ab" * 32)).kind == first_kind
    assert scenes.refusals == [{"start": gr.GHOST_START, "reason": "This ghost's start was made on another ROM."}]


def test_every_start_has_a_switch_row_and_an_exactness():
    assert set(gr.SAVE_STATES) == set(gr.STARTS) == set(gr.EXACT)


def test_a_scene_names_the_cartridge_its_ghost_was_recorded_on_and_the_one_it_ran_on():
    """Another player's JP ghost kept Mario exact on the US cartridge; the scene says both, never one for the
    other (a clip's rom_layout, and the lab ROM's header country)."""
    from sm64_events.ghost.clip import ROM_LAYOUTS
    assert sorted(gr.REGION_OF_LAYOUT[layout] for layout in ROM_LAYOUTS) == ["jp", "us"]
    header = bytearray(0x40)
    header[:4] = bytes.fromhex("80371240")
    header[0x20:0x34] = b"SM64 USAMUNE v1.93j "
    for country, region in ((ord("J"), "jp"), (ord("E"), "us")):
        header[0x3E] = country
        stages = SimpleNamespace(rom=bytes(header) + bytes(64))
        assert stage_sim.Stages.region(stages) == region
