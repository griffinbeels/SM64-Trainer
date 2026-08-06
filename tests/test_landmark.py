"""The landmark key — the one door onto "which door was that".

His three castle-basement doors are the fixture on purpose: they are the real
measurement (2026-08-05) and the numbers below are the ones his own session
produced, so a change that breaks the key breaks against live data rather than
against something invented here.
"""
import struct

from sm64_events.core.landmark import Landmark, landmark_at
from sm64_events.core.snapshot import GameSnapshot, SnapshotReader
from sm64_events.memory import addresses as A
from sm64_events.memory.buffer import BufferMemory
from sm64_events.memory.objects import slot_address

HMC_DOOR = (1126, -1074, -2661)
MOAT_DOOR = (717, -1177, -869)
DOOR_BHV = 0x800EBC8C


def landmark(home=HMC_DOOR, level=6, area=3, behaviour=DOOR_BHV) -> Landmark:
    return Landmark(level=level, area=area, behaviour=behaviour, home=home)


def test_the_same_door_keys_the_same_however_the_pool_moved_it():
    # The key names nothing about the slot, which is the whole point: his HMC
    # door wore slots 3, 38 and 3 again over one session.
    assert landmark().key == landmark().key


def test_two_doors_in_one_room_key_apart():
    assert landmark(HMC_DOOR).key != landmark(MOAT_DOOR).key


def test_the_area_is_part_of_the_key():
    # The castle's basement<->lobby door exists in BOTH areas at the same
    # coordinates, so dropping the area would merge two real things.
    assert landmark(area=3).key != landmark(area=1).key


def test_a_thing_the_game_made_mid_play_cannot_be_named():
    assert landmark(home=(0, 0, 0)).placed is False
    assert landmark(home=HMC_DOOR).placed is True


def test_no_engaged_object_means_no_landmark():
    assert landmark_at(GameSnapshot(
        wall_time_utc=None, global_timer=1, mario_action=0,
        mario_action_timer=0, num_stars=0, last_completed_course=0,
        last_completed_star=0)) is None


def test_the_reader_names_the_object_mario_is_engaged_with():
    """End to end through the real endian decode, on the real layout."""
    mem = BufferMemory()
    slot = 38
    mem.write_u32(A.MARIO_USED_OBJ, slot_address(slot))
    mem.write_u32(slot_address(slot, A.OBJECT_BEHAVIOR), DOOR_BHV)
    for axis, value in enumerate(HMC_DOOR):
        mem.write_u32(slot_address(slot, A.OBJECT_HOME_POS + axis * 4),
                      int.from_bytes(struct.pack(">f", float(value)), "big"))
    mem.write_u32(A.GLOBAL_TIMER, 26490)

    snapshot = SnapshotReader(mem).read()
    assert snapshot.landmark_behaviour == DOOR_BHV
    assert landmark_at(snapshot).home == HMC_DOOR


def test_holding_something_outranks_merely_touching_it():
    # Grabbing a bob-omb while standing in a door's trigger: what he HOLDS is
    # the deliberate act and is the thing he means.
    mem = BufferMemory()
    mem.write_u32(A.MARIO_INTERACT_OBJ, slot_address(38))
    mem.write_u32(slot_address(38, A.OBJECT_BEHAVIOR), DOOR_BHV)
    mem.write_u32(A.MARIO_HELD_OBJ, slot_address(40))
    mem.write_u32(slot_address(40, A.OBJECT_BEHAVIOR), 0x800EE2F4)
    assert SnapshotReader(mem).read().landmark_behaviour == 0x800EE2F4


def test_a_pointer_that_misses_a_slot_boundary_names_nothing():
    # A torn read must not name an landmark out of the middle of some other
    # object; the boundary test is the same one that found these pointers.
    mem = BufferMemory()
    mem.write_u32(A.MARIO_USED_OBJ, slot_address(38) + 0x10)
    mem.write_u32(slot_address(38, A.OBJECT_BEHAVIOR), DOOR_BHV)
    assert SnapshotReader(mem).read().landmark_behaviour == 0
