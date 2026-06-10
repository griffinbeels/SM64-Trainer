# tools/find_timer.py
"""Empirically locate the Usamune timer variable in RDRAM.

Run with PJ64 + Usamune in-game, with the Usamune timer ENABLED and visibly
counting on screen (e.g. inside a level with Timer/Display = ALWAYS):

    uv run python tools/find_timer.py

Phase A scans all of RDRAM for steadily incrementing counters (25-65 ticks
per second covers 30 fps and 60 fps counters). The expected tick window is
scaled by the MEASURED elapsed time between samples, so Python processing
time between reads cannot disqualify true counters.
Phase B prints the live value of every surviving candidate twice a second,
formatted as M'SS"CC — watch the screen and note which ADDRESS matches the
on-screen timer (it should also reset when the Usamune timer resets).
"""
import array
import time

from sm64_events.detectors.star_grab import format_igt
from sm64_events.memory import addresses as A
from sm64_events.memory.pj64 import Pj64Memory

KNOWN_FRAME_COUNTERS = {A.GLOBAL_TIMER}  # exclude; we already track these
RATE_LO, RATE_HI = 25.0, 65.0  # accepted ticks/second band
ROUNDS = 4
MAX_CANDIDATES = 12


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


def u32_at(block: bytes, off: int) -> int:
    return int.from_bytes(block[off:off + 4], "little")


def u16_at(block: bytes, off: int) -> int:
    return int.from_bytes(block[off:off + 2], "little")


def main() -> None:
    mem = Pj64Memory()
    print("Attaching to Project64.exe ...")
    while not mem.attach():
        print("  not found, retrying in 2s")
        time.sleep(2)
    size = rdram_size(mem)
    print(f"Attached. Scanning {size // 0x100000} MB of RDRAM.\n\n"
          f"Phase A: hunting steady counters ({ROUNDS} rounds). Keep the game\n"
          "UNPAUSED with the Usamune timer visibly counting the whole time.\n")

    t_prev = time.perf_counter()
    prev = read = mem._read_raw(0, size)
    time.sleep(1.0)
    t_curr = time.perf_counter()
    curr = mem._read_raw(0, size)
    elapsed = t_curr - t_prev
    lo32 = lo16 = int(RATE_LO * elapsed)
    hi32 = hi16 = int(RATE_HI * elapsed) + 1

    # Full-grid first pass (slow, done once).
    p32, c32 = array.array("I", prev), array.array("I", curr)
    cand32 = {i * 4 for i in range(len(p32))
              if lo32 <= ((c32[i] - p32[i]) & 0xFFFFFFFF) <= hi32}
    p16, c16 = array.array("H", prev), array.array("H", curr)
    cand16 = {i * 2 for i in range(len(p16))
              if lo16 <= ((c16[i] - p16[i]) & 0xFFFF) <= hi16}
    print(f"  round 1 ({elapsed:.2f}s window): "
          f"{len(cand32)} u32 + {len(cand16)} u16 candidates")
    prev, t_prev = curr, t_curr

    # Targeted re-checks (fast): only surviving offsets, real elapsed time.
    for round_no in range(2, ROUNDS + 1):
        time.sleep(1.0)
        t_curr = time.perf_counter()
        curr = mem._read_raw(0, size)
        elapsed = t_curr - t_prev
        lo = int(RATE_LO * elapsed)
        hi = int(RATE_HI * elapsed) + 1
        cand32 = {o for o in cand32
                  if lo <= ((u32_at(curr, o) - u32_at(prev, o)) & 0xFFFFFFFF) <= hi}
        cand16 = {o for o in cand16
                  if lo <= ((u16_at(curr, o) - u16_at(prev, o)) & 0xFFFF) <= hi}
        print(f"  round {round_no} ({elapsed:.2f}s window): "
              f"{len(cand32)} u32 + {len(cand16)} u16 candidates")
        prev, t_prev = curr, t_curr

    # A u32 counter also ticks in its low u16 half — drop the duplicates.
    cand16 = {o for o in cand16 if (o & ~3) not in cand32}

    cands: list[tuple[int, str]] = []
    for off in sorted(cand32):
        addr = A.KSEG0_BASE + off
        if addr not in KNOWN_FRAME_COUNTERS:
            cands.append((addr, "u32"))
    for off in sorted(cand16):
        cands.append((A.KSEG0_BASE + (off ^ 2), "u16"))

    if not cands:
        print("\nNo stable counters found — was the on-screen timer counting"
              " and the game unpaused for the whole scan?")
        return
    if len(cands) > MAX_CANDIDATES:
        print(f"\n{len(cands)} candidates; showing the first {MAX_CANDIDATES}.")
        cands = cands[:MAX_CANDIDATES]

    print(f"\nPhase B: live values ({len(cands)} candidates), 2x/s.")
    print("Find the column matching the on-screen timer; it should also")
    print("reset when the Usamune timer resets. Ctrl+C to quit.\n")
    print("  ".join(f"{addr:#010x}/{kind}".rjust(16) for addr, kind in cands))
    while True:
        cells = []
        for addr, kind in cands:
            v = mem.read_u32(addr) if kind == "u32" else mem.read_u16(addr)
            cells.append(f"{format_igt(v)} {v}".rjust(16))
        print("  ".join(cells))
        time.sleep(0.5)


if __name__ == "__main__":
    main()
