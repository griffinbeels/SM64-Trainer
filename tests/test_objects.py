# tests/test_objects.py
from sm64_events.memory import addresses as A
from sm64_events.memory.layout import US
from sm64_events.memory.objects import POOL_END, pool_slot, slot_address


def test_pool_slot_decodes_slot_and_field():
    assert pool_slot(US.object_pool) == (0, 0)
    assert pool_slot(US.object_pool + 0x154) == (0, 0x154)
    assert pool_slot(US.object_pool + 4 * A.OBJECT_SIZE + 0xF0) == (4, 0xF0)


def test_pool_slot_rejects_outside_addresses():
    assert pool_slot(US.object_pool - 1) is None
    assert pool_slot(POOL_END) is None
    assert pool_slot(US.global_timer) is None


def test_slot_address_roundtrips():
    addr = slot_address(7, 0x154)
    assert pool_slot(addr) == (7, 0x154)
