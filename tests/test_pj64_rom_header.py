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
    elif command == "open":
        if image is not None:
            k.VirtualFree(image, 0, 0x8000)
        header = bytes.fromhex(args[1])
        for want in (int(a, 16) for a in args[0].split(",")):
            image = k.VirtualAlloc(want, size, 0x3000, 0x04)
            if image:
                ctypes.memmove(image, header, len(header))
                break
        print(hex(image or 0), flush=True)
"""

LOW_ADDRESSES = [hex(0x10000000 * n) for n in range(2, 15)]
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

    def open(self, name: bytes, addresses: list[str]) -> int:
        at = int(self.send(f"open {','.join(addresses)} {cartridge(name).hex()}"), 16)
        assert at, "no free low address for the image"
        return at

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


def attached_to(emulator) -> pj64.Pj64Memory:
    import pymem
    memory = pj64.Pj64Memory()
    memory._pm = pymem.Pymem(emulator.process.pid)
    return memory


def test_a_swap_is_read_through_the_remembered_address_or_found_again(emulator, walks):
    usamune, vanilla = b"SM64 USAMUNE v1.93u", b"SUPER MARIO 64"
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

        # Mid-swap (released, not yet allocated) is scanned every time,
        # because this process has shown a cartridge before.
        assert emulator.send("free") == "ok"
        assert memory.rom_header() is None
        assert memory.rom_header() is None
        assert walks[0] == 4
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
