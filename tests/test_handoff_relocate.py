"""Moving Mario inside a slot's bytes: every link follows, nothing else changes, and a word no structure explains stops it."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "scene_lab"))
import handoff_relocate as tool  # noqa: E402
import handoff_slot as slots  # noqa: E402

BODY = tool.BODY_START
MARIO_PLACE, LIVE_PLACE, OTHER_PLACE = 200, 50, 7
OBJ = lambda i: tool.POOL_START + i * tool.OBJECT_BYTES  # noqa: E731
MARIO, LIVE, OTHER = OBJ(MARIO_PLACE), OBJ(LIVE_PLACE), OBJ(OTHER_PLACE)
FREE_HEAD = OBJ(60)
BEHAVIOR_MARIO, BEHAVIOR_OTHER = 0x800EE000, 0x800EE040
BEHAVIOR_OF_LIVE_PLACE = 0x800EE080


def put(slot: bytearray, address: int, value: int) -> None:
    struct.pack_into(">I", slot, address - BODY, value)


def word(slot: bytes, address: int) -> int:
    return struct.unpack_from(">I", slot, address - BODY)[0]


def field(index: int, offset: int) -> int:
    return OBJ(index) + offset


def make_slot() -> bytes:
    """A world small enough to read: Mario, one other object, one object in the place the reset gave Mario."""
    slot = bytearray(slots.SLOT_BYTES)
    put(slot, tool.BODY_START + 0x88, MARIO)                                     # gMarioState->marioObj
    put(slot, tool.BODY_START + 0x7C, OTHER)                                     # heldObj
    put(slot, tool.BODY_START + 0x78, LIVE)                                      # interactObj: the object moved aside
    put(slot, slots.MARIO_OBJECT, MARIO)
    put(slot, 0x80361160, MARIO)                                                 # gCurrentObject
    head = tool.LIST_HEADS
    put(slot, head + 0x60, MARIO)                                                # list head next
    put(slot, head + 0x64, LIVE)                                                 # list head prev
    put(slot, field(MARIO_PLACE, 0x60), LIVE)
    put(slot, field(MARIO_PLACE, 0x64), head)
    put(slot, field(LIVE_PLACE, 0x60), head)
    put(slot, field(LIVE_PLACE, 0x64), MARIO)
    put(slot, field(MARIO_PLACE, 0x04), OTHER)                                   # the draw circle
    put(slot, field(MARIO_PLACE, 0x08), LIVE)
    put(slot, field(LIVE_PLACE, 0x04), MARIO)
    put(slot, field(LIVE_PLACE, 0x08), OTHER)
    put(slot, field(OTHER_PLACE, 0x04), LIVE)
    put(slot, field(OTHER_PLACE, 0x08), MARIO)
    put(slot, field(OTHER_PLACE, 0x68), MARIO)                                   # parentObj
    put(slot, field(OTHER_PLACE, 0x78), LIVE)                                    # collidedObjs[0]
    put(slot, field(OTHER_PLACE, 0x214), MARIO)                                  # platform
    put(slot, field(OTHER_PLACE, 0xF4), LIVE)                                    # a rawData pointer
    put(slot, field(MARIO_PLACE, 0x50), MARIO + 0x21C)                           # throwMatrix: its own transform
    put(slot, field(MARIO_PLACE, tool.BEHAVIOR), BEHAVIOR_MARIO)
    put(slot, field(LIVE_PLACE, tool.BEHAVIOR), BEHAVIOR_OF_LIVE_PLACE)
    put(slot, field(OTHER_PLACE, tool.BEHAVIOR), BEHAVIOR_OTHER)
    put(slot, tool.FREE_LIST + 0x60, FREE_HEAD)                                  # the free list: untouched objects
    struct.pack_into(">I", slot, slots.TRAILER_MARIO, MARIO)
    struct.pack_into(">hh", slot, slots.TRAILER_LEVEL, 9, 1)
    return bytes(slot)


def test_mario_sits_in_the_live_place_and_the_old_occupant_takes_his():
    out = tool.relocate(make_slot(), [(MARIO, LIVE)]).data
    assert word(out, field(LIVE_PLACE, tool.BEHAVIOR)) == BEHAVIOR_MARIO
    assert word(out, field(MARIO_PLACE, tool.BEHAVIOR)) == BEHAVIOR_OF_LIVE_PLACE
    assert tool.slot_mario(out) == LIVE
    assert word(out, slots.MARIO_OBJECT) == LIVE


def test_every_named_pointer_follows():
    out = tool.relocate(make_slot(), [(MARIO, LIVE)]).data
    assert word(out, tool.BODY_START + 0x88) == LIVE, "gMarioState->marioObj"
    assert word(out, tool.BODY_START + 0x78) == MARIO, "interactObj, on the object moved aside"
    assert word(out, tool.BODY_START + 0x7C) == OTHER, "heldObj, on an object that did not move"
    assert word(out, 0x80361160) == LIVE, "gCurrentObject"
    assert word(out, tool.LIST_HEADS + 0x60) == LIVE
    assert word(out, tool.LIST_HEADS + 0x64) == MARIO
    assert word(out, field(OTHER_PLACE, 0x68)) == LIVE, "parentObj"
    assert word(out, field(OTHER_PLACE, 0x78)) == MARIO, "collidedObjs"
    assert word(out, field(OTHER_PLACE, 0x214)) == LIVE, "platform"
    assert word(out, field(OTHER_PLACE, 0xF4)) == MARIO, "rawData"
    assert word(out, tool.FREE_LIST + 0x60) == FREE_HEAD


def test_links_keep_their_order_after_the_move():
    """The loop and the free list run by links, so the order of the world must be the same."""
    out = tool.relocate(make_slot(), [(MARIO, LIVE)]).data
    mario_next = word(out, field(LIVE_PLACE, 0x60))
    assert mario_next == MARIO, "Mario's next is the object that used to sit here, now at his old place"
    assert word(out, field(MARIO_PLACE, 0x60)) == tool.LIST_HEADS, "and that one's next is the head, as before"
    assert word(out, field(MARIO_PLACE, 0x64)) == LIVE
    assert word(out, field(LIVE_PLACE, 0x08)) == MARIO, "the draw circle"
    assert word(out, field(OTHER_PLACE, 0x08)) == LIVE


def test_a_pointer_into_an_object_keeps_its_offset():
    out = tool.relocate(make_slot(), [(MARIO, LIVE)]).data
    assert word(out, field(LIVE_PLACE, 0x50)) == LIVE + 0x21C


def test_already_in_place_is_the_same_bytes():
    slot = make_slot()
    assert tool.relocate(slot, [(MARIO, MARIO)]).data == slot


def test_there_and_back_is_the_same_bytes():
    slot = make_slot()
    assert tool.relocate(tool.relocate(slot, [(MARIO, LIVE)]).data, [(LIVE, MARIO)]).data == slot


def test_a_word_no_structure_explains_stops_it():
    slot = bytearray(make_slot())
    put(slot, field(OTHER_PLACE, 0x20), MARIO)                                   # pos.x holding an object address
    with pytest.raises(tool.Ambiguous):
        tool.relocate(bytes(slot), [(MARIO, LIVE)])
    slot = bytearray(make_slot())
    put(slot, 0x80361168, MARIO)                                                 # a global nobody named
    with pytest.raises(tool.Ambiguous):
        tool.relocate(bytes(slot), [(MARIO, LIVE)])


def test_a_raw_data_pointer_is_named_as_found_by_value():
    moved = tool.relocate(make_slot(), [(MARIO, LIVE)])
    assert [(index, offset) for index, offset, _behavior, _value in moved.raw_data] == [(OTHER_PLACE, 0xF4)]


def test_moves_name_objects_not_places():
    """A second move names the object where the slot had it, even after the first move shifted it."""
    third = OBJ(90)
    slot = make_slot()
    place = tool.permutation([(MARIO, LIVE), (LIVE, third)])
    assert place[MARIO_PLACE] == LIVE_PLACE
    assert place[LIVE_PLACE] == 90
    assert place[90] == MARIO_PLACE
    assert sorted(place.tolist()) == list(range(tool.OBJECT_COUNT))
    assert slot


def test_a_whole_slot_only():
    with pytest.raises(ValueError):
        tool.relocate(bytes(100), [(MARIO, LIVE)])
