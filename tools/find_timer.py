# tools/find_timer.py
"""Empirically locate the Usamune timer variable in RDRAM.

Run with PJ64 + Usamune in-game, with the Usamune timer ENABLED and visibly
counting on screen (e.g. inside a level with the timer option on):

    uv run python tools/find_timer.py

Phase A scans all of RDRAM for counters ticking at ~30/frame-per-second
(sampled over several rounds, so transient values drop out).
Phase B prints the live value of every surviving candidate twice a second,
formatted as M'SS"CC — watch the screen and note which ADDRESS matches the
on-screen timer (it should also reset when the Usamune timer resets).
Then put that address into addresses.py as the timer source.
"""
import array
import time

from sm64_events.detectors.star_grab import format_igt
from sm64_events.memory import addresses as A
from sm64_events.memory.pj64 import Pj64Memory

KNOWN_FRAME_COUNTERS = {A.GLOBAL_TIMER}  # exclude; we already track these
TICK_RANGE = range(25, 36)  # expected delta per 1s sample at 30 fps
ROUNDS = 3
MAX_CANDIDATES = 14


def rdram_size(mem: Pj64Memory) -> int:
    """Scan the full RDRAM (8 MB with expansion pak, else 4 MB)."""
    size = mem.read_u32(A.OS_MEM_SIZE)
    if size not in (0x400000, 0x800000):
        return 0x400000
    try:
        mem._read_raw(size - 4, 4)  # confirm the host region is that large
        return size
    except Exception:
        return 0x400000


def read_block(mem: Pj64Memory, size: int) -> bytes:
    return mem._read_raw(0, size)


def tick_candidates(prev: bytes, curr: bytes) -> tuple[set[int], set[int]]:
    """Return (u32 byte offsets, u16 byte offsets) that ticked ~30."""
    p32 = array.array("I", prev)
    c32 = array.array("I", curr)
    u32_hits = {i * 4 for i in range(len(p32))
                if ((c32[i] - p32[i]) & 0xFFFFFFFF) in TICK_RANGE}
    p16 = array.array("H", prev)
    c16 = array.array("H", curr)
    u16_hits = {i * 2 for i in range(len(p16))
                if ((c16[i] - p16[i]) & 0xFFFF) in TICK_RANGE}
    return u32_hits, u16_hits


def main() -> None:
    mem = Pj64Memory()
    print("Attaching to Project64.exe ...")
    while not mem.attach():
        print("  not found, retrying in 2s")
        time.sleep(2)
    size = rdram_size(mem)
    print(f"Attached. Scanning {size // 0x100000} MB of RDRAM.\n\n"
          f"Phase A: scanning for ~30/s counters "
          f"({ROUNDS} rounds, 1s apart). Keep the game UNPAUSED with the\n"
          "Usamune timer visibly counting on screen.\n")

    prev = read_block(mem, size)
    u32s: set[int] | None = None
    u16s: set[int] | None = None
    for round_no in range(ROUNDS):
        time.sleep(1.0)
        curr = read_block(mem, size)
        h32, h16 = tick_candidates(prev, curr)
        u32s = h32 if u32s is None else (u32s & h32)
        u16s = h16 if u16s is None else (u16s & h16)
        prev = curr
        print(f"  round {round_no + 1}: {len(u32s)} u32 + {len(u16s)} u16 candidates")

    # A u32 counter also looks like a ticking u16 in its low half — drop
    # u16 offsets that overlap a surviving u32 word.
    u16s = {o for o in u16s if (o & ~3) not in u32s}

    cands: list[tuple[int, str]] = []  # (n64_addr, kind)
    for off in sorted(u32s):
        addr = A.KSEG0_BASE + off
        if addr not in KNOWN_FRAME_COUNTERS:
            cands.append((addr, "u32"))
    for off in sorted(u16s):
        cands.append((A.KSEG0_BASE + (off ^ 2), "u16"))

    if not cands:
        print("\nNo stable counters found — was the on-screen timer counting?")
        return
    if len(cands) > MAX_CANDIDATES:
        print(f"\n{len(cands)} candidates; showing the first {MAX_CANDIDATES}.")
        cands = cands[:MAX_CANDIDATES]

    print(f"\nPhase B: live values ({len(cands)} candidates), 2x/s.")
    print("Watch the on-screen timer and note which ADDRESS matches it —")
    print("it should also reset when the Usamune timer resets. Ctrl+C to quit.\n")
    header = "  ".join(f"{addr:#010x}/{kind}" for addr, kind in cands)
    print(header)
    while True:
        cells = []
        for addr, kind in cands:
            v = mem.read_u32(addr) if kind == "u32" else mem.read_u16(addr)
            cells.append(f"{format_igt(v & 0xFFFF):>7} {v:>6}"[:14].rjust(14))
        print("  ".join(cells))
        time.sleep(0.5)


if __name__ == "__main__":
    main()
