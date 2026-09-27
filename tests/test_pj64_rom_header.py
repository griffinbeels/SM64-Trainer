# tests/test_pj64_rom_header.py
"""The cartridge header read against a REAL process, because the claim under
test is about Windows memory, not about our code: Project64 1.6 releases the
old ROM image before it allocates the next (Memory.cpp Allocate_ROM), so a
remembered address either still holds the current cartridge or can no longer
be read. A child Python process plays Project64's part: it commits an 8 MB
image below 4 GB (the 32-bit range the region walk covers) and swaps it."""
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.memory import addresses as A
from sm64_events.memory import pj64

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows process memory")

EMULATOR = r"""
import ctypes, sys
k = ctypes.windll.kernel32
k.VirtualAlloc.restype = ctypes.c_void_p
k.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
k.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
size, image = int(sys.argv[1]), None
for line in sys.stdin:
    command, *args = line.split()
    if command == "free":
        k.VirtualFree(image, 0, 0x8000)
        image = None
        print("ok", flush=True)
    elif command == "decoy":
        # Game memory that happens to hold the swapped magic: a pointer to
        # 0x80371240 stored word-swapped, followed by ordinary binary data.
        decoy = None
        for want in (int(a, 16) for a in args[0].split(",")):
            decoy = k.VirtualAlloc(want, size, 0x3000, 0x04)
            if decoy:
                data = bytes.fromhex(args[1])
                ctypes.memmove(decoy + 0x1000, data, len(data))
                break
        print(hex(decoy or 0), flush=True)
    elif command == "open":
        # An image `offset` bytes into its own allocation (0: at its start).
        if image is not None:
            k.VirtualFree(image, 0, 0x8000)
        header, offset = bytes.fromhex(args[1]), int(args[2], 16) if len(args) > 2 else 0
        for want in (int(a, 16) for a in args[0].split(",")):
            image = k.VirtualAlloc(want, size + offset, 0x3000, 0x04)
            if image:
                ctypes.memmove(image + offset, header, len(header))
                break
        print(hex(image or 0), flush=True)
    elif command == "machine":
        # Project64 1.6's own layout: one reservation, RDRAM committed at its
        # base and the cartridge image at +0x10000000 (read live 2026-09-25).
        base = None
        for want in (int(a, 16) for a in args[0].split(",")):
            base = k.VirtualAlloc(want, 0x20000000, 0x2000, 0x01)
            if base:
                k.VirtualAlloc(base, size, 0x1000, 0x04)
                k.VirtualAlloc(base + 0x10000000, size, 0x1000, 0x04)
                break
        print(hex(base or 0), flush=True)
    elif command == "write":
        at, data = int(args[0], 16), bytes.fromhex(args[1])
        ctypes.memmove(at, data, len(data))
        print("ok", flush=True)
"""

LOW_ADDRESSES = [hex(0x10000000 * n) for n in range(2, 15)]
ROM_AFTER_RDRAM = 0x10000000
USAMUNE, VANILLA = b"SM64 USAMUNE v1.93u", b"SUPER MARIO 64"
#: The interpreter itself: a venv's python.exe is a launcher that runs it as a
#: child, so the launcher's pid would hold no image at all.
INTERPRETER = getattr(sys, "_base_executable", sys.executable)


def cartridge(name: bytes) -> bytes:
    """A header as Project64 1.6 stores it (word-swapped)."""
    header = bytearray(0x40)
    header[0:4] = b"\x80\x37\x12\x40"
    header[0x20:0x34] = name.ljust(20, b" ")
    header[0x3E] = ord("E")
    return b"".join(bytes(header[at:at + 4])[::-1] for at in range(0, 0x40, 4))


class Emulator:
    def __init__(self):
        self.process = subprocess.Popen(
            [INTERPRETER, "-c", EMULATOR, str(A.RDRAM_FULL_SIZE)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            **quiet_spawn_kwargs())

    def send(self, line: str) -> str:
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        return self.process.stdout.readline().strip()

    def open(self, name: bytes, addresses: list[str], offset: int = 0) -> int:
        at = int(self.send(f"open {','.join(addresses)} {cartridge(name).hex()} {offset:x}"), 16)
        assert at, "no free low address for the image"
        return at

    def machine(self, name: bytes) -> int:
        """RDRAM's base, with the image at base + 0x10000000."""
        base = int(self.send(f"machine {','.join(LOW_ADDRESSES)}"), 16)
        assert base, "no free low 512 MiB for the machine"
        assert self.send(f"write {base + ROM_AFTER_RDRAM:x} {cartridge(name).hex()}") == "ok"
        return base

    def close(self):
        self.process.kill()
        self.process.wait(timeout=10)


@pytest.fixture
def emulator():
    running = Emulator()
    try:
        yield running
    finally:
        running.close()


@pytest.fixture
def walks(monkeypatch):
    """How many times the whole process was walked for an image."""
    count = [0]
    walk = pj64.iter_committed_regions

    def counted(handle):
        count[0] += 1
        return walk(handle)

    monkeypatch.setattr(pj64, "iter_committed_regions", counted)
    return count


@pytest.fixture(autouse=True)
def no_sightings():
    """Every reader shares what each process has shown; tests must not."""
    pj64.Pj64Memory._roms.clear()
    yield
    pj64.Pj64Memory._roms.clear()


def attached_to(emulator, rdram_base: int | None = None) -> pj64.Pj64Memory:
    import pymem
    memory = pj64.Pj64Memory()
    memory._pm = pymem.Pymem(emulator.process.pid)
    memory._rdram_base = rdram_base
    return memory


def reads_of(memory) -> list[tuple[int, int]]:
    """Every (address, size) the reader reads from the process from now on."""
    reads, read = [], memory._pm.read_bytes

    def counted(address, size):
        reads.append((address, size))
        return read(address, size)

    memory._pm.read_bytes = counted
    return reads


def chunks(reads) -> list[tuple[int, int]]:
    """Reads bigger than a header: the process's memory itself being read."""
    return [r for r in reads if r[1] > 0x40]


def test_a_swap_is_read_through_the_remembered_address_or_found_again(emulator, walks):
    usamune, vanilla = USAMUNE, VANILLA
    first = emulator.open(usamune, LOW_ADDRESSES)
    memory = attached_to(emulator)
    try:
        assert memory.rom_header() == cartridge(usamune)
        assert walks[0] == 1
        assert memory.rom_header() == cartridge(usamune)
        assert walks[0] == 1, "an unchanged cartridge is one small read, not a walk"

        # Released and allocated elsewhere: the old address cannot be read.
        moved = emulator.open(vanilla, [a for a in LOW_ADDRESSES if int(a, 16) != first])
        assert moved != first
        assert memory.rom_header() == cartridge(vanilla)
        assert walks[0] == 2

        # Released and allocated at the same place: the read sees the new one.
        assert emulator.open(usamune, [hex(moved)]) == moved
        assert memory.rom_header() == cartridge(usamune)
        assert walks[0] == 2

        # Mid-swap (released, not yet allocated) is looked for at every ask,
        # because this process has shown a cartridge before -- but its large
        # regions are read through only once per ROM_SCAN_RETRY_S.
        assert emulator.send("free") == "ok"
        assert int(emulator.send(f"decoy {','.join(LOW_ADDRESSES)} 00"), 16), \
            "a large region holding no image, for the miss to read"
        reads = reads_of(memory)
        assert memory.rom_header() is None
        assert chunks(reads), "the first miss reads the large regions through"
        del reads[:]
        assert memory.rom_header() is None
        assert walks[0] == 4
        assert reads and not chunks(reads), "inside the window: region starts only"

        # The next image, at the start of its allocation, is found at once.
        emulator.open(vanilla, LOW_ADDRESSES)
        assert memory.rom_header() == cartridge(vanilla)
    finally:
        memory._close()


def test_the_image_where_project64_keeps_it_is_one_read_and_no_walk(emulator, walks):
    """Project64 1.6 keeps the image 0x10000000 past RDRAM, in the same
    allocation (read live 2026-09-25). In address order, 381 MiB of driver
    images and mapped sections came before it: 632 ms for every fresh reader,
    three per boot and one a second while the setup screen was open."""
    base = emulator.machine(USAMUNE)
    memory = attached_to(emulator, rdram_base=base)
    reads = reads_of(memory)
    try:
        assert memory.rom_header() == cartridge(USAMUNE)
        assert reads == [(base + ROM_AFTER_RDRAM, 0x40)] and walks[0] == 0

        # A new cartridge in the same place: the one read sees it.
        swapped = cartridge(VANILLA).hex()
        assert emulator.send(f"write {base + ROM_AFTER_RDRAM:x} {swapped}") == "ok"
        assert memory.rom_header() == cartridge(VANILLA)
        assert len(reads) == 2 and walks[0] == 0
    finally:
        memory._close()


def test_a_fresh_reader_for_the_same_process_does_not_search_again(emulator, walks):
    """The boot probe, the poller and the setup screen (a fresh reader every
    second while it is open) each build their own: what one found, all read."""
    emulator.open(USAMUNE, LOW_ADDRESSES)
    first = attached_to(emulator)
    try:
        assert first.rom_header() == cartridge(USAMUNE)
        assert walks[0] == 1
    finally:
        first._close()
    second = attached_to(emulator)
    reads = reads_of(second)
    try:
        assert second.rom_header() == cartridge(USAMUNE)
        assert walks[0] == 1 and len(reads) == 1
    finally:
        second._close()


def test_an_image_inside_its_allocation_is_still_found(emulator, walks):
    """Where in a region an image starts is not assumed (review 2026-08-15):
    with nothing past RDRAM and no region starting with one, every large
    region is read through."""
    emulator.open(USAMUNE, LOW_ADDRESSES, offset=0x100000)
    memory = attached_to(emulator, rdram_base=0x10000)
    reads = reads_of(memory)
    try:
        assert memory.rom_header() == cartridge(USAMUNE)
        assert walks[0] == 1 and chunks(reads)
    finally:
        memory._close()


def test_a_process_that_never_showed_a_cartridge_is_not_walked_at_every_ask(emulator, walks):
    memory = attached_to(emulator)
    try:
        assert memory.rom_header() is None
        assert memory.rom_header() is None
        assert walks[0] == 1
    finally:
        memory._close()


def test_game_memory_holding_the_magic_is_not_mistaken_for_the_cartridge(emulator, walks):
    """Live 2026-09-22: Usamune was reported unsupported because the scan took
    the first 4-byte magic it met -- a pointer to 0x80371240 in a LOWER region
    -- as the header, and remembered it. Garbage after the magic must be
    skipped and the real image found."""
    garbage = (bytes.fromhex("40123780") + bytes(range(0x90, 0xd0)))
    decoy = int(emulator.send(f"decoy {','.join(LOW_ADDRESSES[:6])} {garbage.hex()}"), 16)
    assert decoy
    real = emulator.open(b"SM64 USAMUNE v1.93u", [a for a in LOW_ADDRESSES[6:]])
    assert real > decoy, "the decoy must come first in address order to be meaningful"
    memory = attached_to(emulator)
    try:
        assert memory.rom_header() == cartridge(b"SM64 USAMUNE v1.93u")
    finally:
        memory._close()
