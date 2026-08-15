# tools/watch_timer.py
"""Watch arbitrary RDRAM addresses live — step 3 of the memory-hunting
playbook (characterize a candidate across game scenarios before trusting it).

    uv run python tools/watch_timer.py [ADDR[:u16|u32] ...]

Defaults to the two Usamune IGT globals. Prints gCurrLevelNum plus each
address (raw value and formatted as an IGT time) twice a second.
Scenarios worth testing for any new candidate: level change, area warp,
savestate load, Usamune level reset, timer display OFF.
"""
import sys
import time

from sm64_events.detectors.star_grab import format_igt
from sm64_events.memory.layout import layout_for, version_from_argv
from sm64_events.memory.pj64 import Pj64Memory

LAYOUT = layout_for(version_from_argv())      # --version jp reads the JP layout

DEFAULT_WATCH = [(LAYOUT.usamune_overall, "u16"), (LAYOUT.usamune_star_result, "u16")]


def parse_spec(spec: str) -> tuple[int, str]:
    addr, _, kind = spec.partition(":")
    return int(addr, 16), (kind or "u32")


def main() -> None:
    watch = [parse_spec(s) for s in sys.argv[1:]] or DEFAULT_WATCH
    mem = Pj64Memory()
    print("Attaching to Project64.exe ...")
    while not mem.attach():
        print("  not found, retrying in 2s")
        time.sleep(2)
    print("Attached. Watching 2x/s — Ctrl+C to quit.\n")
    headers = ["level"] + [f"{addr:#010x}/{kind}" for addr, kind in watch]
    print("  ".join(h.rjust(18) for h in headers))
    while True:
        cells = [str(mem.read_s16(LAYOUT.curr_level))]
        for addr, kind in watch:
            v = mem.read_u32(addr) if kind == "u32" else mem.read_u16(addr)
            cells.append(f"{format_igt(v)} {v}")
        print("  ".join(c.rjust(18) for c in cells))
        time.sleep(0.5)


if __name__ == "__main__":
    main()
