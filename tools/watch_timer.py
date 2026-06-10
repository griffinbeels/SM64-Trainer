# tools/watch_timer.py
"""Validate the Usamune timer candidate address across game scenarios.

The find_timer.py hunt located the displayed timer in object-pool fields;
primary candidate 0x8033D5DC (object slot 0, field 0x154). Object slots are
dynamic, so before trusting a fixed address we must see it survive:

    uv run python tools/watch_timer.py

While it runs, work through this checklist and watch the PRIMARY column:
  1. Stand in a level: PRIMARY matches the on-screen timer.
  2. Exit to the castle, enter a DIFFERENT level: still matches.
  3. Usamune level reset and savestate load: resets/jumps with the screen.
  4. Set Usamune Timer/Display to OFF: PRIMARY keeps counting (this is the
     requirement — the counter must exist regardless of display settings).
  5. Turn the display back on: still in sync with the screen.
Report any row where PRIMARY stops tracking what the game is doing.
"""
import time

from sm64_events.detectors.star_grab import format_igt
from sm64_events.memory import addresses as A
from sm64_events.memory.pj64 import Pj64Memory

CANDIDATES = [
    ("PRIMARY 0x8033D5DC", 0x8033D5DC),
    ("mirror  0x8033DCFC", 0x8033DCFC),
    ("area?   0x8033DEF8", 0x8033DEF8),
]


def main() -> None:
    mem = Pj64Memory()
    print("Attaching to Project64.exe ...")
    while not mem.attach():
        print("  not found, retrying in 2s")
        time.sleep(2)
    print("Attached. Watching candidates 2x/s — Ctrl+C to quit.\n")
    print(f"{'level':>5}  {'course':>6}  " +
          "  ".join(f"{name:>24}" for name, _ in CANDIDATES))
    while True:
        level = mem.read_s16(A.CURR_LEVEL)
        course = mem.read_s8(A.LAST_COMPLETED_COURSE)
        cells = []
        for _, addr in CANDIDATES:
            v = mem.read_u32(addr)
            cells.append(f"{format_igt(v)} {v}".rjust(24))
        print(f"{level:>5}  {course:>6}  " + "  ".join(cells))
        time.sleep(0.5)


if __name__ == "__main__":
    main()
