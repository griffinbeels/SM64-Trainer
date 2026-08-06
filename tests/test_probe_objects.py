"""The object probe's pure core (tools/probe_objects.py).

The probe needs PJ64 and a human, but what it CONCLUDES is decided entirely by
these functions, and this is a gate whose wrong answer is expensive: calling a
frame counter an identity would send us off to build a labelling tool keyed on
a number that changes every time he plays. So the cases below are about the
three rejections, not about wording -- a volatile offset, a kind-wide constant
and a value never seen twice must all fail to be candidates.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from sm64_events.memory import addresses as A            # noqa: E402
from sm64_events.memory.buffer import BufferMemory       # noqa: E402
from probe_objects import (analyse, behaviour_of,        # noqa: E402
                           identity_candidates, volatile_offsets, words)

BEHAVIOUR_WORD = A.OBJECT_BEHAVIOR // 4
WORDS = A.OBJECT_SIZE // 4
DOOR_BHV = 0x13003AB8


def dump(**at_offset: int) -> str:
    """An object dump with the named offsets set and everything else zero."""
    payload = [0] * WORDS
    payload[BEHAVIOUR_WORD] = DOOR_BHV
    for offset, value in at_offset.items():
        payload[int(offset[1:], 16) // 4] = value
    return b"".join(value.to_bytes(4, "big") for value in payload).hex()


def capture(obj: str, next_obj: str | None = None, *, epoch: int = 0,
            level: int = 6, area: int = 1, frame: int = 100) -> dict:
    return {
        "frame": frame, "epoch": epoch, "level": level, "area": area,
        "action": A.ACT_PUSHING_DOOR, "action_name": "ACT_PUSHING_DOOR",
        "field": 0x80, "field_hint": "usedObj?", "slot": 42,
        "mario_pos": [0.0, 0.0, 0.0],
        "obj": obj, "obj_next": obj if next_obj is None else next_obj,
    }


def offsets(candidates: list[dict]) -> list[int]:
    return [row["offset"] for row in candidates]


def three_doors() -> list[dict]:
    """Door A, door B, door A again -- the shape the report exists to find."""
    return [capture(dump(o0x40=value)) for value in (0xAAAA, 0xBBBB, 0xAAAA)]


def test_a_value_that_recurs_for_the_same_door_is_a_candidate():
    found = identity_candidates(three_doors(), volatile=set())
    assert 0x40 in offsets(found)
    best = next(row for row in found if row["offset"] == 0x40)
    assert best["distinct"] == 2 and best["repeats"] == 1


def test_the_behaviour_pointer_names_the_kind_and_is_never_a_candidate():
    # Constant across every capture: it cannot tell one door from another.
    found = identity_candidates(three_doors(), volatile=set())
    assert A.OBJECT_BEHAVIOR not in offsets(found)


def test_a_volatile_offset_is_rejected_even_though_it_recurs():
    # An animation counter can cycle back to a value it already held; only the
    # two-frame window separates it from an identity.
    captures = three_doors()
    assert 0x40 in offsets(identity_candidates(captures, volatile=set()))
    assert 0x40 not in offsets(identity_candidates(captures, volatile={0x40}))


def test_a_value_never_seen_twice_is_not_an_identity():
    captures = [capture(dump(o0x40=value)) for value in (1, 2, 3)]
    assert 0x40 not in offsets(identity_candidates(captures, volatile=set()))


def test_volatility_comes_from_the_second_read_of_the_same_object():
    moved = volatile_offsets([capture(dump(o0x40=1, o0x44=7),
                                      dump(o0x40=2, o0x44=7))])
    assert 0x40 in moved and 0x44 not in moved


def test_surviving_a_reload_outranks_recurring_within_one_epoch():
    # 0x50 holds the surviving value and 0x40 the within-epoch one ON PURPOSE:
    # every other term of the ranking ties here, so only the reload term can
    # put 0x50 first and a ranking that dropped it would fall back to offset
    # order and look right.
    survives = 0xAAAA   # seen in epoch 0 and again in epoch 1
    within = 0xCCCC     # seen twice, both inside epoch 0
    captures = [
        capture(dump(o0x50=survives, o0x40=within), epoch=0),
        capture(dump(o0x50=0xBBBB, o0x40=within), epoch=0),
        capture(dump(o0x50=survives, o0x40=0xDDDD), epoch=1),
    ]
    found = identity_candidates(captures, volatile=set())
    assert offsets(found)[0] == 0x50
    assert next(row for row in found if row["offset"] == 0x50)["cross_epoch"] == 1
    assert next(row for row in found if row["offset"] == 0x40)["cross_epoch"] == 0


def test_captures_group_by_place_and_behaviour():
    here = capture(dump(o0x40=1), area=1)
    upstairs = capture(dump(o0x40=1), area=2)
    found = analyse([here, upstairs])
    assert [(group["area"], len(group["captures"])) for group in found["groups"]] \
        == [(1, 1), (2, 1)]
    assert all(group["behaviour"] == DOOR_BHV for group in found["groups"])


def test_the_dump_decodes_back_to_the_words_that_were_written():
    assert words(dump(o0x40=0xDEADBEEF))[0x10] == 0xDEADBEEF
    assert behaviour_of(capture(dump())) == DOOR_BHV


def test_read_block_returns_n64_byte_order():
    # PJ64 stores each 32-bit word little-endian; a struct dump has to come
    # back the way the game wrote it or every offset in the report is wrong.
    mem = BufferMemory()
    mem.write_u32(A.OBJECT_POOL, 0x11223344)
    mem.write_u32(A.OBJECT_POOL + 4, 0xAABBCCDD)
    assert mem.read_block(A.OBJECT_POOL, 8) == bytes.fromhex("11223344AABBCCDD")
