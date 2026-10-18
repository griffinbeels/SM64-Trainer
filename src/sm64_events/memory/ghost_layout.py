"""Typed semantic configuration for the native ghost reader; no RAM reads.

The native reader owns the reviewed N64 pointer paths. These bytes configure
its absolute roots from Layout; they do not certify the new VERIFY addresses
or pair a sample with a rendered picture.

Both practice cartridges share one RAM schema. `rom_layout` names which one
runs (1 US, 2 JP): the native reader names behaviour scripts through it
(plugin/gfxwrap/ghost_behaviours.h) and every clip records it. A JP Layout
still needs each root it lists; a missing one raises LayoutIncomplete.
"""
from dataclasses import dataclass, fields
import struct

from sm64_events.detectors import counter_epoch
from sm64_events.detectors.anchors import IGT_WRAP_CEILING, NEAR_ZERO_IGT
from sm64_events.ghost.native_layout import ROM_JP, ROM_US
from sm64_events.memory import addresses
from sm64_events.memory.addresses import KSEG0_BASE
from sm64_events.memory.layout import Layout, LayoutIncomplete

_CONFIG = struct.Struct("<20I")
_CLOCK_CONFIG = struct.Struct("<26I")
VERSION = 8
RAM_WORD_SWAPPED = 1
# Each game version the native ghost path draws and records, as its clip
# rom_layout. A version outside it shows no ghosts.
ROM_LAYOUTS = {"us": ROM_US, "jp": ROM_JP}


def ghost_supported(version: str) -> bool:
    """Whether native ghost capture and playback run on this game version."""
    return version in ROM_LAYOUTS


def clock_config_bytes() -> bytes:
    """Encode gcc_config from the existing clock/action registries, pack 4.

    Native gcc_init owns semantic validation. The version and list capacity
    here describe its ABI; timing/action values remain owned by their modules.
    """
    castles = sorted(addresses.CASTLE_LEVELS)
    warps = sorted(counter_epoch.AREA_WARP_OPS)
    if len(castles) > 8 or len(warps) > 8:
        raise ValueError("Ghost clock registry exceeds its native list capacity")
    return _CLOCK_CONFIG.pack(
        1, NEAR_ZERO_IGT, counter_epoch.AREA_LOAD_WINDOW,
        counter_epoch.TELEPORT_WINDOW, counter_epoch.LEVEL_LOAD_TAIL_FRAMES,
        counter_epoch.WARP_FIRED_WINDOW, addresses.ACT_TELEPORT_FADE_OUT,
        IGT_WRAP_CEILING, len(castles), len(warps),
        *castles, *([0] * (8 - len(castles))), *warps, *([0] * (8 - len(warps))),
    )


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
    pending_warp_op: int
    delayed_warp_timer: int
    sound_code: int
    sound_producer: int
    sound_consumer: int
    sound_ring: int
    behaviour_base: int = 0  # segment 0x13 in RAM; 0 leaves held objects uncaptured
    player1_controller: int = 0  # existing verified Controller root; 0 leaves pad unknown
    # Usamune's menu-open byte and input display setting; 0 shows the corner
    # stack whatever Usamune does with its own display.
    usamune_menu: int = 0
    usamune_input_display: int = 0
    rom_layout: int = ROM_US  # which cartridge these roots belong to (ROM_LAYOUTS)

    def __post_init__(self) -> None:
        if any(type(getattr(self, field.name)) is not int for field in fields(self)):
            raise ValueError("Ghost layout fields must be integers")
        if self.rom_layout not in ROM_LAYOUTS.values():
            raise ValueError("Ghost capture rom_layout names no supported cartridge")
        if self.rdram_bytes not in (4 << 20, 8 << 20):
            raise ValueError("Ghost capture requires 4 or 8 MiB of RDRAM")
        for name, size, alignment in (
            ("global_timer", 4, 4), ("mario_state", 0xA4, 4),
            ("mario_object", 4, 4), ("current_area_pointer", 4, 4),
            ("area_update_counter", 2, 2), ("current_level", 2, 2),
            ("current_area", 2, 2), ("igt_overall", 2, 2),
            ("object_pool", 240 * 0x260, 4),
            ("pending_warp_op", 2, 2), ("delayed_warp_timer", 2, 2),
            ("sound_code", 48, 4), ("sound_producer", 1, 1),
            ("sound_consumer", 1, 1), ("sound_ring", 256 * 8, 8),
        ):
            address = getattr(self, name)
            if (address % alignment or address < KSEG0_BASE
                    or address - KSEG0_BASE + size > self.rdram_bytes):
                raise ValueError(f"Ghost capture {name} lies outside aligned RDRAM")
        if self.behaviour_base and (self.behaviour_base % 4 or self.behaviour_base < KSEG0_BASE
                                    or self.behaviour_base - KSEG0_BASE + 4 > self.rdram_bytes):
            raise ValueError("Ghost capture behaviour_base lies outside aligned RDRAM")
        if self.player1_controller and (self.player1_controller % 4 or self.player1_controller < KSEG0_BASE
                                       or self.player1_controller - KSEG0_BASE + addresses.CONTROLLER_SIZE > self.rdram_bytes):
            raise ValueError("Ghost capture player1_controller lies outside aligned RDRAM")
        for name in ("usamune_menu", "usamune_input_display"):
            address = getattr(self, name)
            if address and (address < KSEG0_BASE or address - KSEG0_BASE + 1 > self.rdram_bytes):
                raise ValueError(f"Ghost capture {name} lies outside RDRAM")

    @classmethod
    def from_layout(cls, layout: Layout, *, rdram_bytes: int = 8 << 20) -> "GhostLayout":
        """Resolve known roots. VERIFY fields still need their human live gates."""
        if not ghost_supported(layout.version):
            raise LayoutIncomplete(f"Native ghost capture has no {layout.version} layout schema")
        names = ("global_timer", "mario_struct", "mario_object", "current_area_pointer",
                 "area_update_counter", "curr_level", "curr_area", "usamune_overall",
                 "object_pool", "pending_warp_op", "delayed_warp_timer",
                 "sound_code", "sound_producer", "sound_consumer", "sound_ring")
        layout.require(*names)
        return cls(rdram_bytes, *(layout.value(name) for name in names),
                   behaviour_base=layout.value("behaviour_base") or 0,
                   player1_controller=layout.value("player1_controller") or 0,
                   usamune_menu=layout.value("usamune_menu") or 0,
                   usamune_input_display=layout.value("usamune_input_display") or 0,
                   rom_layout=ROM_LAYOUTS[layout.version])

    def to_bytes(self) -> bytes:
        """Exactly 200 bytes; compared against the compiled C reader in tests."""
        roots = (self.global_timer, self.mario_state, self.mario_object,
                 self.current_area_pointer, self.area_update_counter,
                 self.current_level, self.current_area, self.igt_overall,
                 self.object_pool, self.pending_warp_op, self.delayed_warp_timer,
                 self.sound_code, self.sound_producer, self.sound_consumer, self.sound_ring)
        return (_CONFIG.pack(_CONFIG.size + _CLOCK_CONFIG.size + 16, VERSION, RAM_WORD_SWAPPED,
                             self.rdram_bytes, self.rom_layout, *roots) + clock_config_bytes()
                + self.behaviour_base.to_bytes(4, "little")
                + self.player1_controller.to_bytes(4, "little")
                + self.usamune_menu.to_bytes(4, "little")
                + self.usamune_input_display.to_bytes(4, "little"))
