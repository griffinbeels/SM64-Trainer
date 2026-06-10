# tools/hunt_value.py
"""Cheat-Engine-style exact-value search: find where a number shown on the
Usamune display lives in RDRAM.

Use when a displayed value has no ticking counter behind it (e.g. the
"overall star time" frozen during the star dance). Best workflow:

    uv run python tools/hunt_value.py

1. Grab a star in a MULTI-AREA level (e.g. SSL pyramid) and leave the game
   on the star dance / frozen display.
2. At the prompt, type the displayed time exactly, e.g.  0'20"20
   (also accepted: 20.2 seconds, or f606 for raw frames).
3. The tool scans all of RDRAM for that value (+/- 2 frames) and reports
   candidate addresses, annotated with object-pool slot/behavior.
4. If too many candidates remain, grab another star (different time) and
   enter the new displayed value at the next prompt — the intersection
   shrinks fast. Blank input finishes and prints the survivors.
"""
import array
import re
import time

from sm64_events.detectors.star_grab import format_igt
from sm64_events.memory import addresses as A
from sm64_events.memory.objects import describe
from sm64_events.memory.pj64 import Pj64Memory

TOLERANCE = 2  # display-tick / freeze-ordering slack, in frames
MAX_REPORT = 24


def parse_frames(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    if text.startswith("f"):
        return int(text[1:])
    m = re.fullmatch(r"(\d+)\s*[':]\s*(\d+)\s*[\".:]\s*(\d+)", text)
    if m:
        mins, secs, cents = (int(g) for g in m.groups())
        return mins * 1800 + secs * 30 + round(cents * 30 / 100)
    return round(float(text) * 30)


def rdram_size(mem: Pj64Memory) -> int:
    size = mem.read_u32(A.OS_MEM_SIZE)
    if size not in (0x400000, 0x800000):
        return 0x400000
    try:
        mem._read_raw(size - 4, 4)
        return size
    except Exception:
        return 0x400000


def matches(block: bytes, frames: int) -> tuple[set[int], set[int]]:
    lo, hi = frames - TOLERANCE, frames + TOLERANCE
    b32 = array.array("I", block)
    hits32 = {i * 4 for i, v in enumerate(b32) if lo <= v <= hi}
    b16 = array.array("H", block)
    hits16 = {i * 2 for i, v in enumerate(b16) if lo <= v <= hi}
    return hits32, hits16


def main() -> None:
    mem = Pj64Memory()
    print("Attaching to Project64.exe ...")
    while not mem.attach():
        print("  not found, retrying in 2s")
        time.sleep(2)
    size = rdram_size(mem)
    print(f"Attached ({size // 0x100000} MB RDRAM).\n")

    cand32: set[int] | None = None
    cand16: set[int] | None = None
    while True:
        text = input("Displayed value (M'SS\"CC / seconds / fNNN; blank = done): ")
        frames = parse_frames(text)
        if frames is None:
            break
        block = mem._read_raw(0, size)
        h32, h16 = matches(block, frames)
        cand32 = h32 if cand32 is None else (cand32 & h32)
        cand16 = h16 if cand16 is None else (cand16 & h16)
        print(f"  {frames} frames -> {len(cand32)} u32 + {len(cand16)} u16 candidates")
        if len(cand32) + len(cand16) <= MAX_REPORT:
            break

    if cand32 is None:
        return
    cand16 = {o for o in cand16 if (o & ~3) not in cand32}
    found = ([(A.KSEG0_BASE + o, "u32") for o in sorted(cand32)]
             + [(A.KSEG0_BASE + (o ^ 2), "u16") for o in sorted(cand16)])
    if not found:
        print("\nNo candidates survived — re-check the value entered.")
        return
    print(f"\n{len(found)} candidates (showing up to {MAX_REPORT}):")
    for addr, kind in found[:MAX_REPORT]:
        if kind == "u32":
            v = mem.read_u32(addr)
        else:
            v = mem.read_u16(addr)
        print(f"  {addr:#010x}/{kind} = {v} ({format_igt(v)})  {describe(mem, addr)}")


if __name__ == "__main__":
    main()
