# tests/test_objects.py
from sm64_events.memory import addresses as A
from sm64_events.memory.objects import POOL_END, pool_slot, slot_address


def test_pool_slot_decodes_slot_and_field():
    assert pool_slot(A.OBJECT_POOL) == (0, 0)
    assert pool_slot(A.OBJECT_POOL + 0x154) == (0, 0x154)
    assert pool_slot(A.OBJECT_POOL + 4 * A.OBJECT_SIZE + 0xF0) == (4, 0xF0)


def test_pool_slot_rejects_outside_addresses():
    assert pool_slot(A.OBJECT_POOL - 1) is None
    assert pool_slot(POOL_END) is None
    assert pool_slot(A.GLOBAL_TIMER) is None


def test_slot_address_roundtrips():
    addr = slot_address(7, 0x154)
    assert pool_slot(addr) == (7, 0x154)
