"""Typed semantic configuration for the native ghost reader; no RAM reads.

The native reader owns the reviewed N64 pointer paths. These bytes configure
its absolute roots from Layout; they do not certify the new VERIFY addresses
or pair a sample with a rendered picture. JP remains disabled until verified.
"""
from dataclasses import dataclass, fields
import struct

from sm64_events.memory.addresses import KSEG0_BASE
from sm64_events.memory.layout import Layout, LayoutIncomplete

_CONFIG = struct.Struct("<16I")
VERSION = 2
RAM_WORD_SWAPPED = 1
ROM_US = 1


@dataclass(frozen=True)
class GhostLayout:
    """A validated, immutable gc_layout value (ghost_capture.h, pack 4)."""

    rdram_bytes: int
    global_timer: int
    mario_state: int
    mario_object: int
    current_area_pointer: int
    area_update_counter: int
    current_level: int
    current_area: int
    igt_overall: int
    object_pool: int

    def __post_init__(self) -> None:
        if any(type(getattr(self, field.name)) is not int for field in fields(self)):
            raise ValueError("Ghost layout fields must be integers")
        if self.rdram_bytes not in (4 << 20, 8 << 20):
            raise ValueError("Ghost capture requires 4 or 8 MiB of RDRAM")
        for name, size, alignment in (
            ("global_timer", 4, 4), ("mario_state", 0xA0, 4),
            ("mario_object", 4, 4), ("current_area_pointer", 4, 4),
            ("area_update_counter", 2, 2), ("current_level", 2, 2),
            ("current_area", 2, 2), ("igt_overall", 2, 2),
            ("object_pool", 240 * 0x260, 4),
        ):
            address = getattr(self, name)
            if (address % alignment or address < KSEG0_BASE
                    or address - KSEG0_BASE + size > self.rdram_bytes):
                raise ValueError(f"Ghost capture {name} lies outside aligned RDRAM")

    @classmethod
    def from_layout(cls, layout: Layout, *, rdram_bytes: int = 8 << 20) -> "GhostLayout":
        """Resolve known roots. VERIFY fields still need their human live gates."""
        if layout.version != "us":
            raise LayoutIncomplete("Native ghost capture supports only the US layout schema")
        names = ("global_timer", "mario_struct", "mario_object", "current_area_pointer",
                 "area_update_counter", "curr_level", "curr_area", "usamune_overall",
                 "object_pool")
        layout.require(*names)
        return cls(rdram_bytes, *(layout.value(name) for name in names))

    def to_bytes(self) -> bytes:
        """Exactly 64 bytes; compared against the compiled C reader in tests."""
        roots = (self.global_timer, self.mario_state, self.mario_object,
                 self.current_area_pointer, self.area_update_counter,
                 self.current_level, self.current_area, self.igt_overall,
                 self.object_pool)
        return _CONFIG.pack(_CONFIG.size, VERSION, RAM_WORD_SWAPPED,
                            self.rdram_bytes, ROM_US, *roots, 0, 0)
