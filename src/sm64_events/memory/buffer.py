# src/sm64_events/memory/buffer.py
"""In-memory RDRAM image laid out exactly like PJ64's (LE 32-bit words).

Test double for N64Memory; also used by snapshot/detector tests so the real
endian decode path is always exercised.
"""
from sm64_events.memory.addresses import KSEG0_BASE, RDRAM_MIN_SIZE
from sm64_events.memory.base import RdramReader


class BufferMemory(RdramReader):
    def __init__(self, size: int = RDRAM_MIN_SIZE):
        self._buf = bytearray(size)

    def _read_raw(self, offset: int, size: int) -> bytes:
        return bytes(self._buf[offset:offset + size])

    def write_u32(self, addr: int, value: int) -> None:
        off = addr - KSEG0_BASE
        self._buf[off:off + 4] = value.to_bytes(4, "little")

    def write_u16(self, addr: int, value: int) -> None:
        off = (addr - KSEG0_BASE) ^ 2
        self._buf[off:off + 2] = value.to_bytes(2, "little")

    def write_u8(self, addr: int, value: int) -> None:
        self._buf[(addr - KSEG0_BASE) ^ 3] = value
