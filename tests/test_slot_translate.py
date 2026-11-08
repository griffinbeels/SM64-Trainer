"""A save state from one Usamune build, rewritten for the other: the atlas, the translator and the write paths' door.

Fake memory only. A small US world and its JP twin: two main-pool blocks each, a learned pair, Mario, and one
object whose script JP lacks. The simulation's evidence (both ROMs cold-booted, the real load, 300 frames) is in
the slot-library rule.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import test_handoff_try as fakes  # noqa: E402
import test_slot_library as libfakes  # noqa: E402
from sm64_events.core.paths import bundled_symbol_map  # noqa: E402
from sm64_events.memory import behaviours  # noqa: E402
from sm64_events.memory.layout import LAYOUT_ROWS, layout_for  # noqa: E402
from sm64_events.slotlibrary import atlas as A  # noqa: E402
from sm64_events.slotlibrary import relocate as R  # noqa: E402
from sm64_events.slotlibrary import translate as T  # noqa: E402
from sm64_events.slotlibrary import usamune  # noqa: E402
from sm64_events.slotlibrary.store import Damaged, KeptState  # noqa: E402
from sm64_events.sync.rom_candidates import rom_candidate  # noqa: E402

US = usamune.US
SYMBOL = {row.field: row.symbol for row in LAYOUT_ROWS}


def jp_build() -> usamune.Build:
    """The JP build at its verified addresses, and its candidates where the live gate has not run yet."""
    layout = layout_for("jp")
    value = {field: layout.value(name) or rom_candidate("jp", name) or behaviours.globals_map("jp").get(SYMBOL[name])
             for field, name in usamune._LAYOUT_FIELDS.items()}
    return usamune.Build("jp", **value)


JP = jp_build()
LEVEL, AREA = 9, 1
POOLS = {"us": (0x8005C010, 0x801C0FF0), "jp": (0x8005A010, 0x801C0FF0)}
LEFT, RIGHT = 0x100, 0x40
SCRIPTS = {version: layout_for(version).behaviour_base for version in ("us", "jp")}
SEG7 = {"us": 0x0005C010, "jp": 0x0005A000}
MUSIC = "bhvPlaysMusicTrackWhenTouched"          # in US only (memory/behaviours.py)
HOME = (100.0, 200.0, 300.0)
LIST = R.LIST_HEADS_IN_BODY + 3 * R.LIST_HEAD_BYTES


def segments(version: str) -> tuple[int, ...]:
    table = [0] * A.SEGMENTS
    table[0x13], table[0x07] = SCRIPTS[version] & 0x7FFFFFFF, SEG7[version]
    return tuple(table)


def world(version: str, objects=()) -> A.World:
    start, end = POOLS[version]
    return A.World(version, (start, end), (("L", start, LEFT), ("R", end - RIGHT, RIGHT)), segments(version),
                   {"us": 0x80417B4C, "jp": 0x80417C14}[version], tuple(objects))


def make_atlas(jp_objects=()) -> A.Atlas:
    pairs = {POOLS["us"][0] + 0x10: POOLS["jp"][0] + 0x10}
    return A.Atlas(LEVEL, AREA, {"us": world("us"), "jp": world("jp", jp_objects)},
                   {"us": pairs, "jp": {b: a for a, b in pairs.items()}})


def put(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into(">I", data, offset, value)


def word(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def obj(build: usamune.Build, index: int) -> int:
    return build.object_pool + index * R.OBJECT_SIZE


def at(index: int, field: int = 0) -> int:
    return R.POOL_IN_BODY + index * R.OBJECT_SIZE + field


def us_slot(*, held: bool = False) -> bytes:
    """Mario at object 0, a music trigger at object 5 alone in list 3, and one word of each kind."""
    data = bytearray(usamune.SLOT_BYTES)
    put(data, T.POOL_START_IN_BODY, POOLS["us"][0])
    put(data, T.POOL_END_IN_BODY, POOLS["us"][1])
    struct.pack_into(f">{A.SEGMENTS}I", data, T.SEGMENT_TABLE_IN_BODY, *segments("us"))
    put(data, R.MARIO_OBJECT_IN_BODY, obj(US, 0))
    put(data, at(0, R.OBJECT_BEHAVIOR), behaviours.pointer_of("us", "bhvMario"))
    struct.pack_into(">h", data, at(0, R.OBJECT_ACTIVE_FLAGS), 1)
    put(data, at(5, R.OBJECT_BEHAVIOR), behaviours.pointer_of("us", MUSIC))
    struct.pack_into(">h", data, at(5, R.OBJECT_ACTIVE_FLAGS), 1)
    struct.pack_into(">3f", data, at(5, 0x164), *HOME)
    head = US.body_start + LIST
    put(data, LIST + 0x60, obj(US, 5))
    put(data, LIST + 0x64, obj(US, 5))
    put(data, at(5, 0x60), head)
    put(data, at(5, 0x64), head)
    put(data, R.FREE_LIST_IN_BODY + 0x60, obj(US, 9))
    put(data, 0x1000, US.body_start + 0x40)                  # game memory a symbol names
    put(data, 0x1004, POOLS["us"][0] + 0x10)                 # a learned main-pool address
    put(data, 0x100C, 0x80100000)                            # inside the pool, in no block
    put(data, at(0, 0x98), 0x07000020)                       # a segmented address in rawData
    put(data, 0x1010, 0x07000020)                            # the same value where no structure says so
    if held:
        put(data, R.MARIO_HELD_OBJ_OFF, obj(US, 5))
    put(data, usamune.TRAILER_MARIO, obj(US, 0))
    struct.pack_into(">hh", data, usamune.TRAILER_LEVEL, LEVEL, AREA)
    return bytes(data)


def jp_game(level: int = LEVEL) -> libfakes.FakeGame:
    game = libfakes.FakeGame()
    game.head = fakes.header(b"SM64 USAMUNE v1.93j", b"J")
    game.poke(JP.mario_object, struct.pack(">I", obj(JP, 0)))
    game.poke(JP.curr_level, struct.pack(">h", level))
    game.poke(JP.curr_area, struct.pack(">h", AREA))
    start, end = POOLS["jp"]
    body = JP.body_start
    game.poke(body + T.SEGMENT_TABLE_IN_BODY, struct.pack(f">{A.SEGMENTS}I", *segments("jp")))
    game.poke(body + 0x310, struct.pack(">IIIII", 0x2DD0, start, end, start + LEFT, end - RIGHT - 16))
    game.poke(body + 0x1218, struct.pack(">I", 0x80123450))
    game.poke(start - 16 + 4, struct.pack(">I", start + LEFT))
    game.poke(end - RIGHT - 16 + 4, struct.pack(">I", end))
    return game


# --- the door every write path calls ---------------------------------------------------------------------

def test_each_kind_of_pointer_lands_where_the_jp_game_keeps_it():
    game = jp_game()
    out = T.for_build(game, JP, us_slot(), "us", make_atlas())
    data = out.data
    assert word(data, 0x1000) == JP.body_start + 0x40
    assert word(data, 0x1004) == POOLS["jp"][0] + 0x10
    assert word(data, R.MARIO_OBJECT_IN_BODY) == obj(JP, 0) == word(data, usamune.TRAILER_MARIO)
    assert word(data, at(0, R.OBJECT_BEHAVIOR)) == behaviours.pointer_of("jp", "bhvMario")
    assert word(data, at(0, 0x98)) == 0x07000030, "a segmented address follows its segment's new base"
    assert word(data, 0x1010) == 0x07000020, "a value where no structure says segmented stays as it was"
    assert out.counts["static, by symbol"] and out.counts["main pool, learned"] and out.counts["behaviour script"]


def test_the_running_games_own_memory_layout_replaces_the_slots():
    game = jp_game()
    data = T.for_build(game, JP, us_slot(), "us", make_atlas()).data
    for offset, size in T.LIVE_FIELDS:
        assert data[offset:offset + size] == game.read(JP.body_start + offset, size)


def test_a_pointer_with_no_place_is_reported_never_guessed():
    out = T.for_build(jp_game(), JP, us_slot(), "us", make_atlas())
    assert out.unmapped == ((0x100C, 0x80100000, "main pool, between blocks"),)
    assert word(out.data, 0x100C) == 0x80100000


def test_an_object_the_jp_game_lacks_is_unloaded_the_way_the_game_unloads_one():
    jp_coin = [(5, "bhvYellowCoin", *HOME)]
    out = T.for_build(jp_game(), JP, us_slot(), "us", make_atlas(jp_coin))
    data, head = out.data, JP.body_start + LIST
    assert struct.unpack_from(">h", data, at(5, R.OBJECT_ACTIVE_FLAGS))[0] == 0
    assert word(data, LIST + 0x60) == head and word(data, LIST + 0x64) == head, "its list is empty again"
    assert word(data, R.FREE_LIST_IN_BODY + 0x60) == obj(JP, 5)
    assert word(data, at(5, 0x60)) == obj(JP, 9), "it heads the free list, before what headed it"
    assert out.removed == (T.Removed(5, MUSIC, "bhvYellowCoin"),)
    assert out.words() == ("The Trainer changed it from the US version to your JP game. "
                           "It left out a music trigger, which the JP game does not have. "
                           "Your JP game has a yellow coin there instead.")


def test_nothing_in_its_place_is_said_too():
    out = T.for_build(jp_game(), JP, us_slot(), "us", make_atlas())
    assert out.removed == (T.Removed(5, MUSIC, None),)
    assert out.words().endswith("Nothing is there instead.")


def test_an_object_mario_holds_is_never_left_out():
    with pytest.raises(usamune.Refusal, match="holds on to"):
        T.for_build(jp_game(), JP, us_slot(held=True), "us", make_atlas())


def test_the_jp_game_must_stand_in_the_save_states_place():
    with pytest.raises(usamune.Refusal, match="enter the level"):
        T.for_build(jp_game(level=24), JP, us_slot(), "us", make_atlas())


def test_a_level_loaded_elsewhere_than_the_atlas_says_is_refused():
    moved = bytearray(us_slot())
    put(moved, T.POOL_START_IN_BODY, POOLS["us"][0] + 0x20)
    with pytest.raises(usamune.Refusal, match="other places than the map expects"):
        T.for_build(jp_game(), JP, bytes(moved), "us", make_atlas())
    game = jp_game()
    game.poke(POOLS["jp"][0] - 16 + 4, struct.pack(">I", POOLS["jp"][0] + LEFT + 0x20))
    game.poke(JP.body_start + T.HEAD_L_IN_BODY, struct.pack(">I", POOLS["jp"][0] + LEFT + 0x20))
    with pytest.raises(usamune.Refusal, match="other places than the map expects"):
        T.for_build(game, JP, us_slot(), "us", make_atlas())


def test_a_slot_of_the_running_build_is_not_translated():
    with pytest.raises(usamune.Refusal, match="no translation"):
        T.for_build(jp_game(), JP, us_slot(), "jp", make_atlas())


# --- the shift table, against the symbol tables both builds already ship ------------------------------------

def test_every_symbol_both_builds_name_moves_by_the_shift_table_both_ways():
    us, jp = behaviours.globals_map("us"), behaviours.globals_map("jp")
    shared = sorted(set(us) & set(jp))
    assert len(shared) >= 20
    for source, target in ((us, jp), (jp, us)):
        version = "us" if source is us else "jp"
        starts, deltas = T._shifts(version)
        for name in shared:
            i = np.searchsorted(starts, source[name], side="right") - 1
            assert source[name] + deltas[i] == target[name], name
    assert bundled_symbol_map("build_shifts.tsv").exists()


# --- the atlas ------------------------------------------------------------------------------------------

def test_an_atlas_survives_its_file_and_a_tampered_one_is_damaged():
    atlas = make_atlas([(5, "bhvYellowCoin", *HOME)])
    assert A.from_json(A.to_json(atlas)) == atlas
    with pytest.raises(Damaged):
        A.from_json(A.to_json(atlas).replace(b"v1.93j", b"v1.92j"))
    with pytest.raises(Damaged):
        A.from_json(b"{}")


def test_the_store_builds_once_and_rebuilds_a_damaged_file(tmp_path):
    built = []

    def builder(level, area, path):
        built.append((level, area))
        path.write_bytes(A.to_json(make_atlas()))
    store = A.AtlasStore(tmp_path / "atlases", builder)
    assert store.get(LEVEL, AREA) == make_atlas()
    assert store.get(LEVEL, AREA) == make_atlas()
    assert built == [(LEVEL, AREA)]
    store.path(LEVEL, AREA).write_bytes(b"not json")
    store.get(LEVEL, AREA)
    assert built == [(LEVEL, AREA)] * 2
    with pytest.raises(usamune.Refusal, match="no map"):
        A.AtlasStore(tmp_path / "none", None).get(LEVEL, AREA)


def test_the_pool_walk_follows_both_sides_and_refuses_a_broken_chain():
    game = jp_game()
    word_at = lambda address: int.from_bytes(game.read(address, 4), "big")  # noqa: E731
    start, end = POOLS["jp"]
    assert A.pool_blocks(word_at, start, end, start + LEFT, end - RIGHT - 16) == world("jp").blocks
    game.poke(start - 16 + 4, struct.pack(">I", start - 32))
    with pytest.raises(usamune.Refusal, match="hold together"):
        A.pool_blocks(word_at, start, end, start + LEFT, end - RIGHT - 16)


def test_learned_pairs_come_from_words_both_worlds_hold_in_the_same_place():
    us_ram, jp_ram = np.zeros(8 << 20, np.uint8), np.zeros(8 << 20, np.uint8)
    us_world, jp_world = world("us"), world("jp")
    for ram, version, body in ((us_ram, "us", US.body_start), (jp_ram, "jp", JP.body_start)):
        pool = POOLS[version][0]
        struct.pack_into(">II", ram, body - 0x80000000 + 0x40, pool + 0x10, 0x80331234)
        struct.pack_into(">I", ram, pool - 0x80000000 + 0x8, pool + 0x30)
    pairs = A.learn_pairs(us_ram, jp_ram, us_world, jp_world, (US.body_start, 0x100), (JP.body_start, 0x100))
    assert pairs == {POOLS["us"][0] + 0x10: POOLS["jp"][0] + 0x10, POOLS["us"][0] + 0x30: POOLS["jp"][0] + 0x30}


# --- the write paths ------------------------------------------------------------------------------------

def kept_us_state(library) -> KeptState:
    return library.add(us_slot(), source="captured", fingerprint="a" * 64, rom=usamune.ROM_NAMES["us"])[0]


def test_a_us_state_placed_in_a_jp_game_is_translated_and_says_so(tmp_path, monkeypatch):
    from sm64_events.slotlibrary.coursesets import place_states
    from sm64_events.slotlibrary.store import SlotLibrary
    monkeypatch.setattr(usamune, "build_for", lambda version: JP if version == "jp" else US)
    library = SlotLibrary(tmp_path / "library")
    state = kept_us_state(library)
    clean = bytearray(us_slot())
    put(clean, 0x100C, 0)                                     # nothing unmapped
    clean_state = library.add(bytes(clean), source="captured", fingerprint="a" * 64,
                              rom=usamune.ROM_NAMES["us"])[0]
    game, atlases = jp_game(), A.AtlasStore(tmp_path / "atlases", lambda l, a, p: p.write_bytes(A.to_json(make_atlas())))
    report = place_states(game, library, {2: clean_state.id}, atlases=atlases)
    placed, = report.placements
    assert placed.translated_from == "us" and placed.words.startswith("The Trainer changed it from the US version")
    assert word(game.slot(2), usamune.TRAILER_MARIO) == obj(JP, 0)
    with pytest.raises(usamune.Refusal, match="no place in the JP game"):
        place_states(game, library, {3: state.id}, atlases=atlases)
    with pytest.raises(usamune.Refusal, match="nothing here can translate it"):
        place_states(game, library, {3: clean_state.id})



def test_a_share_file_from_the_other_build_joins_a_running_games_library(tmp_path):
    from sm64_events.slotlibrary import sharefile
    from sm64_events.slotlibrary.store import SlotLibrary
    mine = SlotLibrary(tmp_path / "mine")
    state = mine.add(us_slot(), source="captured", fingerprint="b" * 64, rom=usamune.ROM_NAMES["jp"])[0]
    path = sharefile.export_state(mine, state.id, tmp_path / "jp.sm64state")
    friend = SlotLibrary(tmp_path / "friend")
    kept, is_new = sharefile.import_state(friend, path, {"a" * 64}, own_build="us")
    assert is_new and kept.build == "jp"
    for own in (None, "jp"):
        with pytest.raises(usamune.Refusal, match="different Usamune build"):
            sharefile.import_state(SlotLibrary(tmp_path / f"other-{own}"), path, {"a" * 64}, own_build=own)


def clean_us_slot() -> bytes:
    data = bytearray(us_slot())
    put(data, 0x100C, 0)                                      # nothing left unmapped
    return bytes(data)


def test_the_save_states_row_writes_a_us_frame_into_a_jp_game_and_says_so(tmp_path, monkeypatch):
    """The real route, a stand-in builder, a fake JP game: the write answers with the build and the words."""
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import test_scene_moments as moments_test
    from sm64_events.server import scene_lab_api
    from sm64_events.slotlibrary.store import SlotLibrary
    monkeypatch.setattr(usamune, "build_for", lambda version: JP if version == "jp" else US)
    packages = tmp_path / "packages"
    packages.mkdir()
    (packages / moments_test.PACKAGE).write_bytes(b"not read here")
    config = tmp_path / "lab.json"
    config.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(packages),
                                  "map": str(tmp_path / "map.txt"), "lab_root": str(tmp_path / "root")}),
                      encoding="utf-8")
    game = jp_game()
    answer = {**moments_test.ANSWER, "level": LEVEL, "area": AREA, "mario_object": obj(US, 0), "build": "us",
              "fingerprint": "a" * 64}
    atlases = A.AtlasStore(tmp_path / "atlases", lambda level, area, path: path.write_bytes(A.to_json(make_atlas())))
    library = SlotLibrary(tmp_path / "library")
    app = FastAPI()
    scene_lab_api.mount_scene_lab(app, config, moment_options={
        "builder": lambda _c, _p, _f, register=lambda child: None: (answer, clean_us_slot()),
        "game": lambda: game, "library": library, "backups": tmp_path / "backups", "atlases": atlases})
    with TestClient(app) as client:
        built = client.post("/api/scene-lab/moment", json={"package": moments_test.PACKAGE, "frame": 7}).json()
        assert built["build"] == "us"
        written = client.post(f"/api/scene-lab/moment/{built['id']}/write", json={"slot": 3}).json()
        assert written["written"] and written["build"] == "us"
        assert written["translated"]["to"] == "jp"
        assert written["translated"]["words"].startswith(
            "The Trainer changed it from the US version to your JP game.")
        assert word(game.slot(2), usamune.TRAILER_MARIO) == obj(JP, 0)
        kept, = library.scan().states
        assert kept.build == "us", "the library keeps the US state it was given, not the JP bytes"
        game.poke(JP.curr_level, struct.pack(">h", 24))
        refused = client.post(f"/api/scene-lab/moment/{built['id']}/write", json={"slot": 4}).json()
        assert refused["refused"] == "place"
