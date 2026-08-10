"""The object probe's pure core (tools/probe_objects.py).

The probe needs PJ64 and a human, but what it CONCLUDES is decided entirely by
these functions, and this is a gate whose wrong answer is expensive: calling a
frame counter an identity would send us off to build a labelling tool keyed on
a number that changes every time he plays. The cases below are the three
rejections that stop that — an offset that moved, a name two things share, and
a name one thing changed — plus the two properties his live session proved:
one thing keeps one name when the pool moves it, and a thing that MOVES is
named by where it spawned rather than by where it is.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from sm64_events.memory import addresses as A            # noqa: E402
from sm64_events.memory.buffer import BufferMemory       # noqa: E402
from probe_objects import (analyse, behaviour_of,        # noqa: E402
                           corroborating_offsets, entities, home_of,
                           volatile_offsets, words)

BEHAVIOUR_WORD = A.OBJECT_BEHAVIOR // 4
HOME_WORD = A.OBJECT_HOME_POS // 4
WORDS = A.OBJECT_SIZE // 4
DOOR_BHV = 0x13003AB8
MARIO_BHV = 0x800EE040
LIVE_POS = 0xA0

HMC = (1126.0, -1074.0, -2661.0)     # his three castle-basement doors, and the
MOAT = (717.0, -1177.0, -869.0)      # names he gave them on 2026-08-05
DDD = (-3097.0, -1279.0, 1434.0)


def dump(home=HMC, bhv: int = DOOR_BHV, **at_offset: int) -> str:
    """An object dump with a spawn point, a behaviour, and nothing else set."""
    payload = [0] * WORDS
    payload[BEHAVIOUR_WORD] = bhv
    for axis, value in enumerate(home):
        payload[HOME_WORD + axis] = int.from_bytes(
            struct.pack(">f", value), "big")
    for offset, value in at_offset.items():
        payload[int(offset[1:], 16) // 4] = value
    return b"".join(value.to_bytes(4, "big") for value in payload).hex()


def capture(obj: str, next_obj: str | None = None, *, slot: int = 42,
            epoch: int = 0, level: int = 6, area: int = 3,
            frame: int = 100) -> dict:
    return {
        "frame": frame, "epoch": epoch, "level": level, "area": area,
        "action": A.ACT_PUSHING_DOOR, "action_name": "ACT_PUSHING_DOOR",
        "field": 0x80, "field_hint": "usedObj?", "slot": slot,
        "mario_pos": [0.0, 0.0, 0.0],
        "obj": obj, "obj_next": obj if next_obj is None else next_obj,
    }


def test_the_same_thing_keeps_one_name_when_the_pool_moves_it():
    """His HMC door held slot 3, then 38, then 3 again, and never moved."""
    found = entities([capture(dump(HMC), slot=slot) for slot in (3, 38, 3)])
    assert len(found) == 1
    assert found[0]["slots"] == [3, 38]
    assert found[0]["home"] == HMC


def test_two_things_at_different_spawn_points_stay_apart():
    found = entities([capture(dump(home)) for home in (HMC, MOAT, DDD, HMC)])
    assert [row["home"] for row in found] == [HMC, DDD, MOAT]  # busiest first
    assert [len(row["captures"]) for row in found] == [2, 1, 1]


def test_a_moving_thing_is_named_by_where_it_spawned_not_where_it_is():
    """The SSL bob-omb: 88 grabs, 19 pool slots, one spawn point.

    Its LIVE position took 14 values over 21 of those grabs, which is why the
    obvious field is the wrong one — a respawning enemy is a different object
    every time and only the spawn point calls it the same thing.
    """
    grabs = [capture(dump(MOAT, **{"o0xA0": drifting}), slot=slot)
             for drifting, slot in ((0x44DAC000, 40), (0x44DB4000, 62),
                                    (0x44DBC000, 17))]
    found = entities(grabs)
    assert len(found) == 1 and found[0]["slots"] == [17, 40, 62]
    assert len({words(grab["obj"])[LIVE_POS // 4] for grab in grabs}) == 3


def test_a_runtime_spawned_thing_has_no_spawn_point_and_the_key_admits_it():
    # Mario, and a star popping out of a box, are made mid-play: the level
    # script never wrote a home, so they all collapse to one row. The report
    # says so rather than claiming one thing was touched many times.
    zero = (0.0, 0.0, 0.0)
    assert home_of(capture(dump(zero, MARIO_BHV))) == zero


def test_an_offset_that_two_things_share_cannot_corroborate():
    marks = [capture(dump(HMC, **{"o0x40": 7})),
             capture(dump(MOAT, **{"o0x40": 7}))]
    assert 0x40 not in corroborating_offsets(marks, volatile=set())


def test_an_offset_one_thing_changed_cannot_corroborate():
    marks = [capture(dump(HMC, **{"o0x40": 7})),
             capture(dump(HMC, **{"o0x40": 8})),
             capture(dump(MOAT, **{"o0x40": 9}))]
    assert 0x40 not in corroborating_offsets(marks, volatile=set())


def test_an_offset_that_draws_the_same_line_does_corroborate():
    marks = [capture(dump(HMC, **{"o0x40": 7})),
             capture(dump(HMC, **{"o0x40": 7})),
             capture(dump(MOAT, **{"o0x40": 9}))]
    assert 0x40 in corroborating_offsets(marks, volatile=set())


def test_a_volatile_offset_never_corroborates():
    marks = [capture(dump(HMC, **{"o0x40": 7})),
             capture(dump(MOAT, **{"o0x40": 9}))]
    assert 0x40 in corroborating_offsets(marks, volatile=set())
    assert 0x40 not in corroborating_offsets(marks, volatile={0x40})


def test_volatility_comes_from_the_second_read_of_the_same_object():
    moved = volatile_offsets([capture(dump(HMC, **{"o0x40": 1, "o0x44": 7}),
                                      dump(HMC, **{"o0x40": 2, "o0x44": 7}))])
    assert 0x40 in moved and 0x44 not in moved


def test_a_capture_whose_second_read_was_dropped_contributes_no_volatility():
    # The probe leaves it empty when the game reloaded inside the two-frame
    # window: the same slot then holds whatever moved in, and scoring that
    # reads as the object having moved.
    dropped = capture(dump(HMC))
    dropped["obj_next"] = ""
    assert volatile_offsets([dropped]) == set()


def test_marios_own_object_cannot_mark_a_doors_position_volatile():
    """The bug his first session hit: one global volatility mask.

    Mario's object moves every frame, so a mask built over every capture calls
    POSITION volatile — and position is where the spawn point lives. Volatility
    has to be judged inside a behaviour, because "does this move" is a question
    about a KIND.
    """
    doors = [capture(dump(home, **{"o0x40": mark}))
             for home, mark in ((HMC, 1), (MOAT, 2))]
    walking = [capture(dump((0.0, 0.0, 0.0), MARIO_BHV, **{"o0x40": 3}),
                       dump((0.0, 0.0, 0.0), MARIO_BHV, **{"o0x40": 4}))]
    found = analyse(doors + walking)
    door_group = next(group for group in found["groups"]
                      if group["behaviour"] == DOOR_BHV)
    assert 0x40 in door_group["corroborating"]


def test_captures_group_by_place_and_behaviour():
    here = capture(dump(HMC), area=3)
    upstairs = capture(dump(HMC), area=2)
    found = analyse([here, upstairs])
    assert [(group["area"], len(group["captures"])) for group in found["groups"]] \
        == [(2, 1), (3, 1)]
    assert all(group["behaviour"] == DOOR_BHV for group in found["groups"])


def test_the_dump_decodes_back_to_the_words_that_were_written():
    assert words(dump(HMC, **{"o0x40": 0xDEADBEEF}))[0x10] == 0xDEADBEEF
    assert behaviour_of(capture(dump(HMC))) == DOOR_BHV


def test_read_block_returns_n64_byte_order():
    # PJ64 stores each 32-bit word little-endian; a struct dump has to come
    # back the way the game wrote it or every offset in the report is wrong.
    mem = BufferMemory()
    mem.write_u32(A.OBJECT_POOL, 0x11223344)
    mem.write_u32(A.OBJECT_POOL + 4, 0xAABBCCDD)
    assert mem.read_block(A.OBJECT_POOL, 8) == bytes.fromhex("11223344AABBCCDD")
