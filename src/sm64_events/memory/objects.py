# src/sm64_events/memory/objects.py
"""SM64 object-pool helpers (diagnostics and dynamic value location).

The game keeps OBJECT_COUNT slots of OBJECT_SIZE bytes at the pool's base,
which is per ROM version (`layout.object_pool`). Usamune implements its
practice timers as object behavior code, so timer values live in object
rawData fields — and a value's absolute address can change with slot
assignment per level/area. Identifying the owning object by its behavior
pointer is the slot-independent way to find such values.

`ObjectPool(layout)` binds the arithmetic to one version. The module-level
`pool_slot` / `slot_address` / `describe` keep answering for US, so a tool or
test that never cared which ROM is running stays valid unchanged.
"""
from sm64_events.memory import addresses as A
from sm64_events.memory.base import N64Memory
from sm64_events.memory.layout import US, Layout


class ObjectPool:
    def __init__(self, layout: Layout):
        layout.require("object_pool")
        self.base: int = layout.object_pool
        self.end: int = self.base + A.OBJECT_COUNT * A.OBJECT_SIZE

    def pool_slot(self, addr: int) -> tuple[int, int] | None:
        """(slot, field_offset) when addr lies inside the object pool, else None."""
        if not (self.base <= addr < self.end):
            return None
        rel = addr - self.base
        return rel // A.OBJECT_SIZE, rel % A.OBJECT_SIZE

    def slot_address(self, slot: int, field: int = 0) -> int:
        return self.base + slot * A.OBJECT_SIZE + field

    def describe(self, mem: N64Memory, addr: int) -> str:
        """Human-readable annotation for a RAM address (pool-aware)."""
        located = self.pool_slot(addr)
        if located is None:
            return "outside object pool"
        slot, field = located
        bhv = mem.read_u32(self.slot_address(slot, A.OBJECT_BEHAVIOR))
        return f"obj slot {slot:3d} +{field:#05x} bhv {bhv:#010x}"


_US_POOL = ObjectPool(US)
POOL_END = _US_POOL.end


def pool_slot(addr: int) -> tuple[int, int] | None:
    return _US_POOL.pool_slot(addr)


def slot_address(slot: int, field: int = 0) -> int:
    return _US_POOL.slot_address(slot, field)


def describe(mem: N64Memory, addr: int) -> str:
    return _US_POOL.describe(mem, addr)
