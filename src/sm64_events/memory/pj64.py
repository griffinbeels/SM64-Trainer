# src/sm64_events/memory/pj64.py
"""Attach to Project64 1.6 and locate the emulated N64 RDRAM.

Strategy: enumerate committed memory regions in the (32-bit) PJ64 process;
any region >= 4 MB whose start matches the libultra osBootConfig signature
is the RDRAM. Read-only access; never writes to the emulator.
"""
import ctypes
import logging
from collections.abc import Callable, Iterator
from ctypes import wintypes

import pymem
import pymem.exception

from sm64_events.memory import addresses as A
from sm64_events.memory.base import MemoryReadError, RdramReader

log = logging.getLogger("sm64.pj64")

PROCESS_NAME = "Project64.exe"
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100


class _MBI64(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wintypes.DWORD),
        ("_align1", wintypes.DWORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("_align2", wintypes.DWORD),
    ]


def iter_committed_regions(handle: int) -> Iterator[tuple[int, int]]:
    """Yield (base, size) of readable committed regions, low to high."""
    kernel32 = ctypes.windll.kernel32
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    mbi = _MBI64()
    addr = 0
    while kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr),
                                  ctypes.byref(mbi), ctypes.sizeof(mbi)):
        readable = (mbi.State == MEM_COMMIT
                    and not (mbi.Protect & PAGE_GUARD)
                    and mbi.Protect != PAGE_NOACCESS)
        if readable:
            yield mbi.BaseAddress, mbi.RegionSize
        addr = mbi.BaseAddress + mbi.RegionSize
        if addr >= 0x1_0000_0000:  # PJ64 1.6 is 32-bit
            break


def looks_like_rdram(read_u32: Callable[[int], int]) -> bool:
    """osBootConfig signature — written by libultra in every N64 game."""
    return (read_u32(A.OS_ROM_BASE) == 0xB0000000
            and read_u32(A.OS_MEM_SIZE) in (0x400000, 0x800000)
            and read_u32(A.OS_TV_TYPE) <= 2)


class Pj64Memory(RdramReader):
    def __init__(self):
        self._pm: pymem.Pymem | None = None
        self._rdram_base: int | None = None

    @property
    def attached(self) -> bool:
        return self._pm is not None and self._rdram_base is not None

    def attach(self) -> bool:
        self._close()
        try:
            self._pm = pymem.Pymem(PROCESS_NAME)
        except pymem.exception.PymemError:
            self._pm = None
            return False
        for base, size in iter_committed_regions(self._pm.process_handle):
            if size < A.RDRAM_MIN_SIZE:
                continue
            if self._check_signature_at(base):
                self._rdram_base = base
                log.info("attached: RDRAM at host base 0x%X", base)
                return True
        self._close()  # process found, ROM not loaded yet
        return False

    def detach(self) -> None:
        self._close()

    def _close(self) -> None:
        if self._pm is not None:
            try:
                self._pm.close_process()
            except pymem.exception.PymemError:
                pass
        self._pm = None
        self._rdram_base = None

    def _check_signature_at(self, base: int) -> bool:
        def u32(n64_addr: int) -> int:
            data = self._pm.read_bytes(base + (n64_addr - A.KSEG0_BASE), 4)
            return int.from_bytes(data, "little")
        try:
            return looks_like_rdram(u32)
        except pymem.exception.PymemError:
            return False

    def _read_raw(self, offset: int, size: int) -> bytes:
        if not self.attached:
            raise MemoryReadError("not attached to Project64")
        try:
            return self._pm.read_bytes(self._rdram_base + offset, size)
        except pymem.exception.PymemError as exc:
            raise MemoryReadError(str(exc)) from exc
