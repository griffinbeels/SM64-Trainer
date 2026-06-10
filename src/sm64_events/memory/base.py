# src/sm64_events/memory/base.py
"""Typed reads over PJ64's RDRAM image.

PJ64 stores the (big-endian) N64 RDRAM as little-endian 32-bit words.
N64 byte at offset o  -> host offset o ^ 3
aligned halfword at o -> host offset o ^ 2, little-endian
aligned word at o     -> host offset o, little-endian
This module is the ONLY place that knows this.
"""
from typing import Protocol

from sm64_events.memory.addresses import KSEG0_BASE


class MemoryReadError(RuntimeError):
    """Raised when the emulator's memory cannot be read (e.g. it closed)."""


class N64Memory(Protocol):
    def read_u8(self, addr: int) -> int: ...
    def read_u16(self, addr: int) -> int: ...
    def read_u32(self, addr: int) -> int: ...
    def read_s8(self, addr: int) -> int: ...
    def read_s16(self, addr: int) -> int: ...


class RdramReader:
    """Mixin implementing N64Memory over _read_raw(host_offset, size)."""

    def _read_raw(self, offset: int, size: int) -> bytes:
        raise NotImplementedError

    def read_u32(self, addr: int) -> int:
        return int.from_bytes(self._read_raw(addr - KSEG0_BASE, 4), "little")

    def read_u16(self, addr: int) -> int:
        return int.from_bytes(self._read_raw((addr - KSEG0_BASE) ^ 2, 2), "little")

    def read_u8(self, addr: int) -> int:
        return self._read_raw((addr - KSEG0_BASE) ^ 3, 1)[0]

    def read_s8(self, addr: int) -> int:
        v = self.read_u8(addr)
        return v - 0x100 if v >= 0x80 else v

    def read_s16(self, addr: int) -> int:
        v = self.read_u16(addr)
        return v - 0x10000 if v >= 0x8000 else v
