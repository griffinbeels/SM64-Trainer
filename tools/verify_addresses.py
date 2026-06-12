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
from sm64_events.detectors.death import DeathDetector
from sm64_events.detectors.dust import DustTrickDetector
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory.addresses import PARTICLE_DUST
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

    print("\nPhase 2: live watch — grab stars and verify identity/timing;")
    print("dive-rollout and double/triple-jump to verify the dust-trick")
    print("addresses. Expect: one visible slide/landing frame = DUSTLESS")
    print("(frame perfect); dusty slide/landing frames show [DUST].")
    print("PENDING_WARP_OP gate: fall into a pit (HMC) — expect a warp_op")
    print("0x13 line and '>> death: fall' BEFORE the level unloads; then die")
    print("normally (quicksand) and confirm exactly ONE death line. Detector")
    print("lines come from the REAL detectors — exactly what API listeners")
    print("receive. (Ctrl+C to quit)\n")
    star_det, dust_det, death_det = StarGrabDetector(), DustTrickDetector(), DeathDetector()
    prev_snap = reader.read()
    prev_action = None
    while True:
        s = reader.read()
        for ev in death_det.process(prev_snap, s):
            p = ev.payload
            print(f"  >> death: {p['cause']}  igt {p['igt_frames']}f"
                  f"  level {p['level']}  frame {ev.frame}")
        if s.pending_warp_op != prev_snap.pending_warp_op:
            print(f"frame {s.global_timer:>8}  warp_op {s.pending_warp_op:#06x}")
        for ev in star_det.process(prev_snap, s):
            p = ev.payload
            recon = "  [reconstructed: grab raced an IGT reset]" \
                if p["igt_reconstructed"] else ""
            print(f"  >> star_collected: {p['course_name']} / {p['star_name']}"
                  f"  igt {p['igt']} ({p['igt_frames']}f)"
                  f"  frame {ev.frame}{recon}")
        for ev in dust_det.process(prev_snap, s):
            p = ev.payload
            timing = "DUSTLESS" if p["dustless"] else f"{p['frames_late']} late"
            kind = f" ({p['kind']})" if "kind" in p else ""
            print(f"  >> {ev.type}{kind}: {timing}"
                  f"  ({p['landing_frames']} landing frames)"
                  f"  level {p['level']}  frame {ev.frame}")
        if s.mario_action != prev_action:
            dust = "  [DUST]" if s.particle_flags & PARTICLE_DUST else ""
            print(f"frame {s.global_timer:>8}  action {s.mario_action:#010x}  "
                  f"stars {s.num_stars:>3}  igt {s.igt_overall:>6} "
                  f"result {s.igt_result:>6}{dust}")
            prev_action = s.mario_action
        prev_snap = s
        time.sleep(0.016)


if __name__ == "__main__":
    main()
