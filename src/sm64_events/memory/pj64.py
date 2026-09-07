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

    def rom_header(self) -> bytes | None:
        """The first 0x40 bytes of the ROM image PJ64 holds, in whatever byte
        order it stores them (memory/version_probe.py normalises), or None.

        The ROM is not in RDRAM: it is a separate committed region of the
        emulator's process, at least 8 MB for SM64. Where in that region the
        image starts is NOT assumed (review 2026-08-15): each large region is
        searched for the cartridge magic in either byte order, in chunks, so a
        ROM placed at an offset inside its allocation is still found. Scanned
        only when asked -- attach() does not need it -- and read-only like
        everything else here.
        """
        if self._pm is None:
            return None
        from sm64_events.memory.version_probe import (HEADER_SIZE,
                                                      ROM_MAGIC_BE,
                                                      ROM_MAGIC_WORD_SWAPPED,
                                                      normalise_header)
        chunk = 4 * 1024 * 1024
        try:
            for base, size in iter_committed_regions(self._pm.process_handle):
                if size < A.RDRAM_FULL_SIZE:      # an SM64 ROM is 8 MB
                    continue
                for offset in range(0, size, chunk):
                    block = self._pm.read_bytes(base + offset,
                                                min(chunk + HEADER_SIZE, size - offset))
                    for magic in (ROM_MAGIC_BE, ROM_MAGIC_WORD_SWAPPED):
                        at = block.find(magic)
                        while at != -1:
                            head = block[at:at + HEADER_SIZE]
                            if len(head) == HEADER_SIZE and normalise_header(head) is not None:
                                return head
                            at = block.find(magic, at + 1)
        except pymem.exception.PymemError:
            return None
        return None

    # -- host-side process memory (used by the capture-layer install checks) --
    # The present counter lives in the emulator's OWN heap, outside the
    # emulated RDRAM, so it is read by absolute process address. Same handle,
    # same read-only discipline; every failure is a MemoryReadError so the
    # hunter degrades instead of crashing a thread.

    @property
    def rdram_host_base(self) -> int | None:
        """Where the emulated RDRAM sits in the process -- what a host-side
        sweep excludes."""
        return self._rdram_base

    def host_regions(self) -> list[tuple[int, int]]:
        """(base, size) of every readable committed region of the process."""
        if self._pm is None:
            raise MemoryReadError("not attached")
        try:
            return list(iter_committed_regions(self._pm.process_handle))
        except OSError as err:
            raise MemoryReadError(str(err)) from err

    def read_host_bytes(self, address: int, size: int) -> bytes:
        if self._pm is None:
            raise MemoryReadError("not attached")
        try:
            return self._pm.read_bytes(address, size)
        except pymem.exception.PymemError as err:
            raise MemoryReadError(str(err)) from err

    def read_host_u32(self, address: int) -> int:
        return int.from_bytes(self.read_host_bytes(address, 4), "little")

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
