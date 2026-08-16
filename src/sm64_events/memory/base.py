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

    def read_image(self) -> bytes:
        """The whole RDRAM image as PJ64 stores it (host order, LE words) —
        the input `sync/checks.py`'s scans take. Sized by libultra's own
        osMemSize word (4 MB without the expansion pak, 8 MB with), probed
        with one read at the top so a lying header falls back to 4 MB
        rather than raising mid-scan (tools/hunt_value.py::rdram_size)."""
        from sm64_events.memory.addresses import (OS_MEM_SIZE, RDRAM_FULL_SIZE,
                                                  RDRAM_MIN_SIZE)
        size = self.read_u32(OS_MEM_SIZE)
        if size not in (RDRAM_MIN_SIZE, RDRAM_FULL_SIZE):
            size = RDRAM_FULL_SIZE      # header unreadable: probe the top
        try:
            if len(self._read_raw(size - 4, 4)) != 4:
                raise MemoryReadError("short read at the top of RDRAM")
        except Exception:
            size = RDRAM_MIN_SIZE
        return self._read_raw(0, size)

    def read_block(self, addr: int, size: int) -> bytes:
        """`size` bytes from `addr`, in N64 (big-endian) order — ONE host read.

        For dumping a whole struct whose layout is not yet known: decode any
        field out of the result with byte order "big". Word-aligned only,
        because the swap that undoes PJ64's storage is per 32-bit word.
        """
        if addr % 4 or size % 4:
            raise ValueError(f"read_block needs word alignment: {addr:#x}+{size:#x}")
        raw = self._read_raw(addr - KSEG0_BASE, size)
        return b"".join(raw[at:at + 4][::-1] for at in range(0, size, 4))
