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


def test_a_thing_the_game_made_mid_play_was_not_PLACED_by_a_level_script():
    assert landmark(home=(0, 0, 0)).placed is False
    assert landmark(home=HMC_DOOR).placed is True


# -- naming a specific pole (round 9 item 7) ----------------------------------
# His push: "we sometimes have subsections based on grabbing a specific pole...
# Are we SURE there's no way to distinguish between poles? Not even their
# locations?" The WF tree's own numbers, from the 2026-08-05 probe captures:
# 6 grabs across two area reloads, this position every time.
WF_TREE, TREE_BHV = (2560, 256, 4608), 0x800EDC24


def pole(pos=WF_TREE) -> Landmark:
    return Landmark(level=24, area=1, behaviour=TREE_BHV, home=(0, 0, 0),
                    pos=pos)


def test_a_scriptless_object_is_keyed_by_where_it_stands():
    assert pole().key.endswith(":2560,256,4608")
    assert pole().nameable is True


def test_two_poles_in_one_area_key_apart():
    """The whole point of his ask: 'First pole grab' means a SPECIFIC pole."""
    assert pole().key != pole(pos=(1024, 0, 2048)).key


def test_a_placed_object_still_keys_by_its_spawn_point():
    """The five shipped instance names are home-keyed; a live position that
    happens to be read must never move them."""
    standing_elsewhere = Landmark(level=6, area=3, behaviour=DOOR_BHV,
                                  home=HMC_DOOR, pos=(9999, 9999, 9999))
    assert standing_elsewhere.key == landmark(HMC_DOOR).key


def test_an_object_with_neither_coordinate_is_still_refused():
    """The one thing a name cannot land on: no spawn point AND standing at the
    origin, so its key is shared with every other of its kind in the area."""
    assert pole(pos=(0, 0, 0)).nameable is False


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

    for axis, value in enumerate((10, 20, 30)):
        mem.write_u32(slot_address(slot, A.OBJECT_POS + axis * 4),
                      int.from_bytes(struct.pack(">f", float(value)), "big"))

    snapshot = SnapshotReader(mem).read()
    assert snapshot.landmark_behaviour == DOOR_BHV
    assert landmark_at(snapshot).home == HMC_DOOR
    # Both coordinates arrive in ONE block read; the key still uses the
    # spawn point, since this door has one.
    assert landmark_at(snapshot).pos == (10, 20, 30)
    assert landmark_at(snapshot).key.endswith(":1126,-1074,-2661")


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
