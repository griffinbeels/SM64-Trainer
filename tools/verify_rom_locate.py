# tools/verify_rom_locate.py
"""Live gate: a fresh reader finds the cartridge image where Project64 1.6
keeps it, in one read.

Usage: Project64 1.6 open with a ROM loaded (it need not be running), then

    uv run python tools/verify_rom_locate.py

Read-only, like every reader: it attaches the way the boot probe, the poller
and the setup screen do and never writes emulator memory. PASS means the image
was read at RDRAM + 0x10000000 (`Pj64Memory.ROM_AFTER_RDRAM`), a fresh reader
took under 5 ms (632 ms before, 2026-09-25) and a second fresh reader for the
same process read the remembered address once. A FAIL naming "the start of a
large region" or "reading every large region" means this Project64 keeps the
image somewhere else: still found, but slower, and worth a look.
"""
import logging
import sys
import time

from sm64_events.memory.pj64 import Pj64Memory


class _Found(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def main() -> int:
    found = _Found()
    logging.getLogger("sm64.pj64").addHandler(found)
    logging.getLogger("sm64.pj64").setLevel(logging.INFO)
    first = Pj64Memory()
    started = time.perf_counter()
    if not first.attach():
        print("FAIL: Project64 with a loaded ROM is not running")
        return 1
    attached = time.perf_counter()
    header = first.rom_header()
    located = time.perf_counter()
    first.detach()
    second = Pj64Memory()
    second.attach()
    reads: list[int] = []
    read = second._pm.read_bytes
    second._pm.read_bytes = lambda address, size: reads.append(size) or read(address, size)
    again = time.perf_counter()
    same = second.rom_header()
    again = time.perf_counter() - again
    second.detach()

    where = found.lines[-1] if found.lines else "no image found"
    print(f"attach {1000 * (attached - started):.1f} ms, first rom_header "
          f"{1000 * (located - attached):.2f} ms, second fresh reader "
          f"{1000 * again:.2f} ms in {len(reads)} read(s)")
    print(f"image: {where}")
    ok = (header is not None and same == header and "RDRAM base + 0x10000000" in where
          and located - attached < 0.005 and reads == [0x40])
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
