# src/sm64_events/memory/objects.py
"""SM64 object-pool helpers (diagnostics and dynamic value location).

The game keeps OBJECT_COUNT slots of OBJECT_SIZE bytes at OBJECT_POOL.
Usamune implements its practice timers as object behavior code, so timer
values live in object rawData fields — and a value's absolute address can
change with slot assignment per level/area. Identifying the owning object
by its behavior pointer is the slot-independent way to find such values.
"""
from sm64_events.memory import addresses as A
from sm64_events.memory.base import N64Memory

POOL_END = A.OBJECT_POOL + A.OBJECT_COUNT * A.OBJECT_SIZE


def pool_slot(addr: int) -> tuple[int, int] | None:
    """(slot, field_offset) when addr lies inside the object pool, else None."""
    if not (A.OBJECT_POOL <= addr < POOL_END):
        return None
    rel = addr - A.OBJECT_POOL
    return rel // A.OBJECT_SIZE, rel % A.OBJECT_SIZE


def slot_address(slot: int, field: int = 0) -> int:
    return A.OBJECT_POOL + slot * A.OBJECT_SIZE + field


def describe(mem: N64Memory, addr: int) -> str:
    """Human-readable annotation for a RAM address (pool-aware)."""
    located = pool_slot(addr)
    if located is None:
        return "outside object pool"
    slot, field = located
    bhv = mem.read_u32(slot_address(slot, A.OBJECT_BEHAVIOR))
    return f"obj slot {slot:3d} +{field:#05x} bhv {bhv:#010x}"
