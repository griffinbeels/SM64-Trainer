# tools/watch_timer.py
"""Characterize the Usamune overall-timer candidate at 0x80417C74.

hunt_value.py isolated 0x80417C74 (u16, Usamune expansion-RAM global) as
the only address holding the frozen "overall star time" across two grabs.
This watch shows it live, with neighbors (Usamune globals cluster) and the
known section counter for reference:

    uv run python tools/watch_timer.py

Scenario checklist (watch the 0x80417C74 column):
  1. Idle OUTSIDE in SSL: does it tick at 30/s like the section column?
  2. Warp INTO the pyramid: does it KEEP counting (no reset) while the
     section column resets?  -> it's the running overall counter
     Or does it sit unchanged until the grab?  -> it's a grab-time store
  3. Grab the star: does it equal the frozen on-screen time?
  4. Usamune level reset: does it reset to ~0?
  5. Enter a different (single-area) level and grab any star: still equals
     the frozen display?
"""
import time

from sm64_events.detectors.star_grab import format_igt
from sm64_events.memory import addresses as A
from sm64_events.memory.pj64 import Pj64Memory

OVERALL = 0x80417C74
NEIGHBORS = [OVERALL - 4, OVERALL - 2, OVERALL + 2, OVERALL + 4, OVERALL + 6]
SECTION = 0x8033D5DC  # object-pool section counter (slot-dependent!)


def main() -> None:
    mem = Pj64Memory()
    print("Attaching to Project64.exe ...")
    while not mem.attach():
        print("  not found, retrying in 2s")
        time.sleep(2)
    print("Attached. Watching 2x/s — Ctrl+C to quit.\n")
    headers = (["level", f"OVERALL? {OVERALL:#x}"]
               + [f"{a:#x}" for a in NEIGHBORS]
               + [f"section {SECTION:#x}"])
    print("  ".join(h.rjust(18) for h in headers))
    while True:
        cells = [str(mem.read_s16(A.CURR_LEVEL))]
        v = mem.read_u16(OVERALL)
        cells.append(f"{format_igt(v)} {v}")
        for addr in NEIGHBORS:
            n = mem.read_u16(addr)
            cells.append(str(n))
        s = mem.read_u32(SECTION)
        cells.append(f"{format_igt(s)} {s}")
        print("  ".join(c.rjust(18) for c in cells))
        time.sleep(0.5)


if __name__ == "__main__":
    main()
