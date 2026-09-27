# src/sm64_events/memory/pj64.py
"""Attach to Project64 1.6 and locate the emulated N64 RDRAM.

Strategy: enumerate committed memory regions in the (32-bit) PJ64 process;
any region >= 4 MB whose start matches the libultra osBootConfig signature
is the RDRAM. The cartridge image sits 0x10000000 past it (`rom_header`).
Read-only access; never writes to the emulator.
"""
import ctypes
import logging
import threading
import time
from collections.abc import Callable, Iterator
from ctypes import wintypes
from dataclasses import dataclass

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


@dataclass
class _RomSighting:
    """What one Project64 process has shown of its cartridge image."""
    address: int | None = None       # where the image was last found
    seen: bool = False               # the process has shown one at all
    missed_at: float | None = None   # when a full read last found none


class Pj64Memory(RdramReader):
    #: How long after a FULL read of the large regions found no image
    #: rom_header() skips the next one. A process that has never shown a
    #: cartridge is not walked at all in that window; one that HAS shown one
    #: is mid-swap, so every ask still reads the first bytes of each large
    #: region, where Project64 1.6 puts an image.
    ROM_SCAN_RETRY_S = 5.0
    #: Where Project64 1.6 keeps the cartridge image: 0x10000000, the N64's
    #: cartridge bus address, past RDRAM's host base inside the same
    #: allocation. Read live 2026-09-25 (pid 14340): RDRAM at 0x4E7E0000, the
    #: image at 0x5E7E0000 with AllocationBase 0x4E7E0000. The one read this
    #: makes replaced a 632 ms walk over 381 MiB of driver images and mapped
    #: sections, repeated for every fresh reader.
    ROM_AFTER_RDRAM = 0x10000000
    #: Keyed by pid and shared by EVERY reader: the boot probe, the poller and
    #: the setup screen (a fresh reader each second while it is open) each
    #: build their own. A pid names the process the image belongs to.
    _roms: dict[int, _RomSighting] = {}
    _roms_lock = threading.Lock()
    _ROMS_KEPT = 8

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
        emulator's process, at least 8 MB for SM64. Looked for cheapest first,
        and every candidate must read as a whole header (`plausible_header`),
        never just the magic:

        1. where this process's image was last found -- one 0x40-byte read;
        2. `ROM_AFTER_RDRAM` past the RDRAM base -- one read, where Project64
           1.6 keeps it;
        3. the first bytes of every region large enough to hold one;
        4. every byte of those regions, in chunks, so an image placed at an
           offset inside its allocation is still found (review 2026-08-15) --
           at most once per `ROM_SCAN_RETRY_S` after one found nothing.

        The poller asks about once a second to notice a ROM swap. Project64
        1.6 releases the old image BEFORE it allocates the next (Memory.cpp
        Allocate_ROM), so only one image ever exists: a remembered address
        that no longer holds a header was released, and the search starts
        again from step 2. Read-only like everything else here.
        """
        if self._pm is None:
            return None
        from sm64_events.memory.version_probe import HEADER_SIZE
        pid = self._pm.process_id
        with self._roms_lock:
            sighting = self._roms.setdefault(pid, _RomSighting())
            self._forget_old_processes()
            remembered, seen, missed_at = sighting.address, sighting.seen, sighting.missed_at
        candidates = [remembered]
        if self._rdram_base is not None:
            candidates.append(self._rdram_base + self.ROM_AFTER_RDRAM)
        for address in dict.fromkeys(a for a in candidates if a is not None):
            head = self._header_at(address, HEADER_SIZE)
            if head is not None:
                if address != remembered:
                    self._remember(pid, address, "at the RDRAM base + 0x10000000")
                return head
        if remembered is not None:                # released: never read it again
            with self._roms_lock:
                if sighting.address == remembered:
                    sighting.address = None
        recently_missed = (missed_at is not None
                           and time.monotonic() - missed_at < self.ROM_SCAN_RETRY_S)
        if recently_missed and not seen:
            return None
        found = self._search_regions(deep=not recently_missed)
        if found is None:
            if not recently_missed:
                with self._roms_lock:
                    sighting.missed_at = time.monotonic()
            return None
        address, head, how = found
        self._remember(pid, address, how)
        return head

    def _header_at(self, address: int, size: int) -> bytes | None:
        from sm64_events.memory.version_probe import plausible_header
        try:
            head = self._pm.read_bytes(address, size)
        except pymem.exception.PymemError:
            return None
        return head if plausible_header(head) is not None else None

    def _remember(self, pid: int, address: int, how: str) -> None:
        with self._roms_lock:
            sighting = self._roms.setdefault(pid, _RomSighting())
            sighting.address, sighting.seen, sighting.missed_at = address, True, None
        log.info("cartridge image at 0x%X, %s (pid %d)", address, how, pid)

    def _forget_old_processes(self) -> None:
        """Keep the newest few pids; the lock is held."""
        for stale in list(self._roms)[:-self._ROMS_KEPT]:
            del self._roms[stale]

    def _search_regions(self, *, deep: bool) -> tuple[int, bytes, str] | None:
        """(address, header, how it was found) of the first cartridge image
        at the START of a region large enough to hold one, then -- when `deep`
        -- anywhere inside one; None if neither finds it. One walk."""
        from sm64_events.memory.version_probe import (HEADER_SIZE,
                                                      ROM_MAGIC_BE,
                                                      ROM_MAGIC_WORD_SWAPPED,
                                                      plausible_header)
        regions = [(base, size) for base, size in iter_committed_regions(self._pm.process_handle)
                   if size >= A.RDRAM_FULL_SIZE]      # an SM64 ROM is 8 MB
        for base, _size in regions:
            head = self._header_at(base, HEADER_SIZE)
            if head is not None:
                return base, head, "at the start of a large region"
        if not deep:
            return None
        chunk = 4 * 1024 * 1024
        for base, size in regions:
            for offset in range(0, size, chunk):
                try:
                    block = self._pm.read_bytes(base + offset,
                                                min(chunk + HEADER_SIZE, size - offset))
                except pymem.exception.PymemError:
                    break                      # released mid-read: the next region
                for magic in (ROM_MAGIC_BE, ROM_MAGIC_WORD_SWAPPED):
                    at = block.find(magic)
                    while at != -1:
                        head = block[at:at + HEADER_SIZE]
                        if len(head) == HEADER_SIZE and plausible_header(head) is not None:
                            return base + offset + at, head, "by reading every large region"
                        at = block.find(magic, at + 1)
        return None

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
