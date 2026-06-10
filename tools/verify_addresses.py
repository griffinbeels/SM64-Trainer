# tools/verify_addresses.py
"""Live verification of registry addresses against PJ64 1.6 + Usamune v1.93u.

Usage: start PJ64 1.6 with Usamune running (UNPAUSED, in-game), then:

    uv run python tools/verify_addresses.py

Phase 1 runs automatic checks (PASS/FAIL per address). Phase 2 is a live
watch: grab stars and confirm the printed identity matches what you grabbed.
On any FAIL, cross-check the address at ukikipedia.net/wiki/RAM (US column)
or the SM64 decomp US symbol map, fix addresses.py, and rerun.
"""
import time

from sm64_events.core.snapshot import SnapshotReader
from sm64_events.detectors.star_grab import format_igt
from sm64_events.memory import addresses as A
from sm64_events.memory.pj64 import Pj64Memory


def check(label: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    return ok


def main() -> None:
    mem = Pj64Memory()
    print("Attaching to Project64.exe ...")
    while not mem.attach():
        print("  not found (is PJ64 running with the ROM loaded?) retrying in 2s")
        time.sleep(2)
    print("Attached.\n\nPhase 1: automatic checks (game must be unpaused)")

    reader = SnapshotReader(mem)
    s1 = reader.read()
    time.sleep(1.0)
    s2 = reader.read()

    delta = s2.global_timer - s1.global_timer
    ok = True
    ok &= check("GLOBAL_TIMER ticks ~30/s", 25 <= delta <= 35,
                f"delta over 1s = {delta}")
    ok &= check("MARIO_NUM_STARS plausible", 0 <= s2.num_stars <= 182,
                f"numStars = {s2.num_stars} (compare with the in-game counter)")
    ok &= check("MARIO_ACTION nonzero", s2.mario_action != 0,
                f"action = {s2.mario_action:#010x}")
    ok &= check("LAST_COMPLETED plausible",
                0 <= s2.last_completed_course <= 25
                and 0 <= s2.last_completed_star <= 7,
                f"course={s2.last_completed_course} star={s2.last_completed_star}")
    print("\nPhase 1:", "ALL PASS" if ok else "FAILURES — fix addresses.py first")

    print("\nPhase 2: live watch — grab stars and verify identity/timing.")
    print("(Ctrl+C to quit)\n")
    prev_action = None
    while True:
        s = reader.read()
        if s.mario_action != prev_action:
            # tag only the EDGE into the grab set (matches detector semantics;
            # midair grabs pass through two in-set actions but are one grab)
            in_set = (s.mario_action in A.STAR_GRAB_ACTIONS
                      and prev_action not in A.STAR_GRAB_ACTIONS)
            star_id = s.last_completed_star - 1
            igt = format_igt(max(0, s.igt_timer - s.mario_action_timer))
            tag = (f"  << STAR GRAB: {A.course_name(s.last_completed_course)} / "
                   f"{A.star_name(s.last_completed_course, star_id)} "
                   f"(grab frame {s.global_timer - s.mario_action_timer}, "
                   f"igt {igt})"
                   if in_set else "")
            print(f"frame {s.global_timer:>8}  action {s.mario_action:#010x}  "
                  f"stars {s.num_stars:>3}  igt {s.igt_timer:>8}{tag}")
            prev_action = s.mario_action
        time.sleep(0.016)


if __name__ == "__main__":
    main()
