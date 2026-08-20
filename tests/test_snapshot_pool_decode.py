# tests/test_snapshot_pool_decode.py
"""The object-pool DECODE, which nothing covered before 2026-08-20.

`tests/test_caused.py` builds `CausedState` objects by hand, so every caused
detector test passed without once reading a pool out of memory. That left the
reader free to change how it decodes bytes with nothing to catch a mistake --
which mattered the moment the decode was rewritten to stop byte-swapping
146 KB to use twelve bytes per object.

Every field decodes here: an unsigned pointer, two SIGNED integers, and two
float vectors, through `BufferMemory`, which stores words exactly the way PJ64
does.
"""
import struct

from sm64_events.memory import addresses as A
from sm64_events.memory.behaviours import pointer_of
from sm64_events.memory.buffer import BufferMemory
from sm64_events.memory.layout import US
from sm64_events.core.snapshot import SnapshotReader

GOOMBA = "bhvGoomba"
SWITCH = "bhvBlueCoinSwitch"


def slot_at(slot: int, field: int) -> int:
    return US.object_pool + slot * A.OBJECT_SIZE + field


def place(mem: BufferMemory, slot: int, symbol: str, *, action: int,
          health: int, home: tuple, pos: tuple) -> None:
    pointer = pointer_of("us", symbol, base=US.behaviour_base)
    assert pointer is not None, f"{symbol} has no pointer in the US map"
    mem.write_u32(slot_at(slot, A.OBJECT_BEHAVIOR), pointer)
    mem.write_u32(slot_at(slot, A.OBJECT_ACTION), action & 0xFFFFFFFF)
    mem.write_u32(slot_at(slot, A.OBJECT_HEALTH), health & 0xFFFFFFFF)
    for index, value in enumerate(home):
        mem.write_f32(slot_at(slot, A.OBJECT_HOME_POS) + 4 * index, value)
    for index, value in enumerate(pos):
        mem.write_f32(slot_at(slot, A.OBJECT_POS) + 4 * index, value)


def test_a_watched_object_decodes_every_field():
    mem = BufferMemory()
    place(mem, 19, GOOMBA, action=102, health=2047,
          home=(-2713.0, 152.0, 5778.0), pos=(-2700.5, 160.25, 5770.0))

    caused = SnapshotReader(mem).read().caused

    assert len(caused) == 1
    state = caused[0]
    assert state.slot == 19
    assert state.symbol == GOOMBA
    assert state.behaviour == pointer_of("us", GOOMBA, base=US.behaviour_base)
    assert state.action == 102
    assert state.health == 2047
    assert state.home == (-2713.0, 152.0, 5778.0)
    assert state.pos == (-2700.5, 160.25, 5770.0)


def test_negative_action_and_health_stay_negative():
    """oAction and oHealth are s32. Decoding them unsigned turns -1 into
    4294967295, which reads as a perfectly plausible action id."""
    mem = BufferMemory()
    place(mem, 4, SWITCH, action=-1, health=-32768,
          home=(0.0, 0.0, 0.0), pos=(0.0, 0.0, 0.0))

    state = SnapshotReader(mem).read().caused[0]

    assert state.action == -1
    assert state.health == -32768


def test_several_watched_objects_come_back_in_slot_order():
    mem = BufferMemory()
    place(mem, 63, GOOMBA, action=1, health=0, home=(1.0, 2.0, 3.0),
          pos=(4.0, 5.0, 6.0))
    place(mem, 9, SWITCH, action=2, health=0, home=(7.0, 8.0, 9.0),
          pos=(10.0, 11.0, 12.0))

    caused = SnapshotReader(mem).read().caused

    assert [state.slot for state in caused] == [9, 63]
    assert [state.symbol for state in caused] == [SWITCH, GOOMBA]


def test_an_unwatched_behaviour_is_not_reported():
    mem = BufferMemory()
    mem.write_u32(slot_at(31, A.OBJECT_BEHAVIOR), US.behaviour_base + 0x4444)

    assert SnapshotReader(mem).read().caused == ()


def test_the_pool_decode_reads_the_same_bytes_the_swapping_one_did():
    """A direct comparison against the byte-swapping decode this replaced.

    The old path normalised the whole pool to big-endian and read every field
    with `"big"`. If the new offsets or signedness drift, this goes red even
    when the fields above happen to round-trip.
    """
    mem = BufferMemory()
    place(mem, 42, GOOMBA, action=-7, health=1234,
          home=(-1.5, 2.25, -3.75), pos=(9.5, -8.25, 7.125))

    pool = mem.read_block(US.object_pool, A.OBJECT_COUNT * A.OBJECT_SIZE)
    base = 42 * A.OBJECT_SIZE
    expected_action = int.from_bytes(
        pool[base + A.OBJECT_ACTION:base + A.OBJECT_ACTION + 4], "big",
        signed=True)
    expected_home = struct.unpack_from(">fff", pool, base + A.OBJECT_HOME_POS)

    state = SnapshotReader(mem).read().caused[0]

    assert state.action == expected_action == -7
    assert state.home == expected_home == (-1.5, 2.25, -3.75)


def test_every_pool_field_the_reader_decodes_is_word_aligned():
    """What makes `read_words` legal at this call site.

    PJ64 stores each N64 word little-endian at its own offset, so a word-
    aligned 32-bit field decodes straight out of the raw block -- but a
    halfword or byte would sit at `offset ^ 2` / `^ 3` inside its word and
    come back silently wrong. If one of these offsets ever moves off a word
    boundary, the decode needs `read_u16`/`read_u8`, not this.
    """
    for name in ("OBJECT_SIZE", "OBJECT_BEHAVIOR", "OBJECT_ACTION",
                 "OBJECT_HEALTH", "OBJECT_HOME_POS", "OBJECT_POS"):
        offset = getattr(A, name)
        assert offset % 4 == 0, f"{name} = {offset:#x} is not word-aligned"
