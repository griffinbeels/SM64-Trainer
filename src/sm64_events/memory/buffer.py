# src/sm64_events/memory/buffer.py
"""In-memory RDRAM image laid out exactly like PJ64's (LE 32-bit words).

Test double for N64Memory; also used by snapshot/detector tests so the real
endian decode path is always exercised. Defaults to the full 8 MB image
because Usamune's timer globals live in expansion-pak RAM (above 4 MB).
"""
from sm64_events.memory.addresses import KSEG0_BASE, RDRAM_FULL_SIZE
from sm64_events.memory.base import RdramReader


class BufferMemory(RdramReader):
    def __init__(self, size: int = RDRAM_FULL_SIZE):
        self._buf = bytearray(size)

    def _read_raw(self, offset: int, size: int) -> bytes:
        return bytes(self._buf[offset:offset + size])

    def _check(self, off: int, size: int) -> None:
        # bytearray slice assignment past the end silently APPENDS — fail
        # loudly instead so a too-small image can't fake zero reads.
        if not (0 <= off and off + size <= len(self._buf)):
            raise IndexError(f"write at offset {off:#x} outside {len(self._buf):#x}-byte image")

    def write_u32(self, addr: int, value: int) -> None:
        off = addr - KSEG0_BASE
        self._check(off, 4)
        self._buf[off:off + 4] = value.to_bytes(4, "little")

    def write_u16(self, addr: int, value: int) -> None:
        off = (addr - KSEG0_BASE) ^ 2
        self._check(off, 2)
        self._buf[off:off + 2] = value.to_bytes(2, "little")

    def write_u8(self, addr: int, value: int) -> None:
        off = (addr - KSEG0_BASE) ^ 3
        self._check(off, 1)
        self._buf[off] = value
