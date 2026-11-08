"""Which Usamune build a slot belongs to, and the slot paths taking that build's addresses. Fake memory only.

The two builds share one slot format at different addresses (slotlibrary/usamune.py), so a build here is the US
build moved 0x1370 lower, the way JP keeps the body: the tests hold whatever JP's verified rows turn out to be.
"""
from __future__ import annotations

import dataclasses
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
sys.path.insert(0, str(ROOT / "tests"))
import test_handoff_try as fakes  # noqa: E402
import test_slot_library as libfakes  # noqa: E402
import test_slot_relocate as world  # noqa: E402
from sm64_events.memory.layout import layout_for  # noqa: E402
from sm64_events.slotlibrary import relocate as R  # noqa: E402
from sm64_events.slotlibrary import usamune  # noqa: E402
from sm64_events.slotlibrary.store import KeptState  # noqa: E402

US = usamune.US
SHIFT = 0x1370
LOWER = dataclasses.replace(US, version="jp", **{name: getattr(US, name) - SHIFT for name in (
    "body_start", "object_pool", "mario_object", "curr_area", "segment_table")})


def shifted(data: bytes) -> bytes:
    """The same slot as the lower build holds it: every word pointing into the body moves down with the body."""
    words = np.frombuffer(data, ">u4").astype(np.uint32)
    inside = (words >= US.body_start) & (words < US.body_start + usamune.BODY_BYTES)
    words[inside] -= SHIFT
    return words.astype(">u4").tobytes()


def state(rom: str) -> KeptState:
    return KeptState("0" * 64, "", 9, 1, 0x80346038, "f" * 64, rom, "2026-11-08T00:00:00+00:00", "captured")


@pytest.mark.parametrize(("rom", "build"), [("SM64 USAMUNE v1.93u", "us"), ("SM64 USAMUNE v1.93j", "jp"),
                                            ("SM64 USAMUNE v1.92u", None), ("test", None)])
def test_a_kept_state_names_its_build_from_the_cartridge_name_its_record_already_holds(rom, build):
    assert state(rom).build == build


def test_the_us_cartridge_gives_the_us_build():
    assert usamune.check_cartridge(libfakes.FakeGame()) is US


def test_a_build_this_trainer_has_not_checked_is_refused_and_says_which(monkeypatch):
    unchecked = dataclasses.replace(layout_for("jp"), usamune_slot_save=None)
    monkeypatch.setattr(usamune, "layout_for", lambda version: unchecked)
    usamune.build_for.cache_clear()
    game = libfakes.FakeGame()
    game.head = fakes.header(b"SM64 USAMUNE v1.93j", b"J")
    try:
        with pytest.raises(usamune.Refusal, match="not checked Usamune JP"):
            usamune.check_cartridge(game)
    finally:
        monkeypatch.undo()
        usamune.build_for.cache_clear()


def test_another_rom_is_refused():
    game = libfakes.FakeGame()
    game.head = fakes.header(b"SUPER MARIO 64")
    with pytest.raises(usamune.Refusal, match="not Usamune"):
        usamune.check_cartridge(game)


def test_the_live_facts_come_from_the_builds_own_addresses():
    game = libfakes.FakeGame()
    game.poke(LOWER.mario_object, struct.pack(">I", 0x80346038))
    game.poke(LOWER.curr_level, struct.pack(">h", 9))
    game.poke(LOWER.curr_area, struct.pack(">h", 2))
    assert usamune.live_facts(game, LOWER) == usamune.SlotFacts(0x80346038, 9, 2)


def test_relocation_in_a_lower_build_is_the_same_move_at_the_lower_addresses():
    moves = [(world.SLOT_MARIO, world.LIVE)]
    lower_moves = [(a - SHIFT, b - SHIFT) for a, b in moves]
    assert R.relocate(shifted(world.make_slot()), lower_moves, LOWER).data == \
        shifted(R.relocate(world.make_slot(), moves, US).data)


def test_a_slot_of_either_build_passes_its_own_facts_check():
    lower = shifted(world.make_slot())
    assert usamune.slot_facts(lower).mario_object == world.SLOT_MARIO - SHIFT
