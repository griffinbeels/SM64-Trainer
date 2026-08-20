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

        The swap is a Python loop over every word, so it costs ~300x the read
        itself on a large block: measured 2,995 us against 9.4 us for 146 KB
        (2026-08-20). Use it to dump a struct you are still learning; use
        `read_words` for a hot path that decodes a few known fields out of
        something big.
        """
        if addr % 4 or size % 4:
            raise ValueError(f"read_block needs word alignment: {addr:#x}+{size:#x}")
        raw = self._read_raw(addr - KSEG0_BASE, size)
        return b"".join(raw[at:at + 4][::-1] for at in range(0, size, 4))

    def read_words(self, addr: int, size: int) -> bytes:
        """`size` bytes from `addr` exactly as PJ64 stores them — ONE host
        read, NO swap.

        PJ64 keeps each N64 32-bit word little-endian at its own offset, so a
        WORD-ALIGNED 32-bit field decodes straight out of this with byte order
        "little" (`int.from_bytes(block[o:o+4], "little")`) and a float with
        `struct.unpack_from("<f", block, o)`. That makes reading a handful of
        fields out of a large block ~70x cheaper than normalising the whole
        thing first (2026-08-20: the object pool's decode, 2,955 us -> 42 us).

        WORD-ALIGNED 32-BIT FIELDS ONLY. A halfword or byte sits at `o ^ 2` /
        `o ^ 3` inside a word and needs `read_u16`/`read_u8`, which know that;
        reading one out of this block directly gives the wrong byte silently.
        """
        if addr % 4 or size % 4:
            raise ValueError(f"read_words needs word alignment: {addr:#x}+{size:#x}")
        return self._read_raw(addr - KSEG0_BASE, size)
