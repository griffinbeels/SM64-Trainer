# tools/verify_addresses.py
"""Live verification of registry addresses against PJ64 1.6 + Usamune v1.93u.

Usage: start PJ64 1.6 with Usamune running (UNPAUSED, in-game), then:

    uv run python tools/verify_addresses.py

Phase 1 runs automatic checks (PASS/FAIL per address). Phase 2 is a live
watch: grab stars and confirm the printed identity matches what you grabbed.
On any FAIL, cross-check the address at ukikipedia.net/wiki/RAM (US column)
or the SM64 decomp US symbol map, fix addresses.py, and rerun.

Live-gate checklist (segment events — Task 17 Step 4):
  1. CURR_AREA: see SKIP note in Phase 1 below; hunt it FIRST with
     tools/hunt_value.py (value 1 in lobby, 2 upstairs, 3 basement) then
     confirm all three areas with watch_timer.py before replacing 0x0.
  2. Walk into BitDW (level 17) pipe and BitFS (level 19) pipe — confirm
     warp_entered fires in Phase 2 live watch; note action id printed.
     Adjust WARP_ENTRY_ACTIONS if a different action shows.
  3. File-select spawn on Castle Grounds — confirm spawned fires; note kind.
  4. Grab the B1 key (level 30): key_grabbed must fire, NO star_collected.
     Record gLastCompletedCourseNum/StarNum values printed in the action
     stream — needed for VERIFY note in addresses.py.
  5. B3 grand star (level 34): key_grabbed which=grand must fire (NO
     star_collected — the grand star is not a collectable; live-verified
     2026-06-12: ACT_JUMBO_STAR_CUTSCENE, numStars unchanged, gLastCompleted*
     untouched, no star-dance action ever appeared).
  6. Level ids 7/17/19/21/23/30/33/34: walk into each; confirm level_changed
     payloads in Phase 2. Fix LEVEL_NAMES entries if any id is wrong.
  7. End-to-end: practice one real LBLJ; confirm segment_armed then
     attempt_completed with kind="segment" and a plausible rta value.
"""
import time

from sm64_events.core.snapshot import SnapshotReader
from sm64_events.detectors.area import AreaChangeDetector
from sm64_events.detectors.dust import DustTrickDetector
from sm64_events.detectors.key import KeyGrabDetector
from sm64_events.detectors.spawn import SpawnDetector
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.detectors.warp import WarpDetector
from sm64_events.memory.addresses import (
    CURR_AREA, KEY_GRAB_LEVELS, LEVEL_NAMES, PARTICLE_DUST,
    SPAWN_ACTIONS, WARP_ENTRY_ACTIONS,
)
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

    # --- Segment-event primitive checks (VERIFY items from addresses.py) ------

    # CURR_AREA: placeholder until live-gate hunt. When 0x0, print a clear
    # TODO — the FIRST step at the live gate is to locate it with hunt_value.py.
    if CURR_AREA == 0x0:
        print("  [SKIP] CURR_AREA: address is 0x0 PLACEHOLDER — hunt it first:")
        print("         uv run python tools/hunt_value.py  (value 1 in lobby,")
        print("         then re-filter with 2 upstairs, 3 basement)")
        print("         Then replace CURR_AREA = 0x0 in addresses.py and rerun.")
    else:
        ok &= check("CURR_AREA reads 1-3", 1 <= s2.curr_area <= 3,
                    f"curr_area = {s2.curr_area} (must be 1=lobby/2=upstairs/3=basement; "
                    f"only valid if you are in the castle interior right now)")

    # WARP_ENTRY_ACTIONS: verify the set is non-empty and the values look like
    # action ids (top byte encodes the action group; pipe actions should be in
    # the 0x0000_1xxx range based on the decomp fetch used to seed them).
    ok &= check("WARP_ENTRY_ACTIONS non-empty",
                len(WARP_ENTRY_ACTIONS) > 0,
                f"{len(WARP_ENTRY_ACTIONS)} action(s): "
                f"{', '.join(hex(a) for a in sorted(WARP_ENTRY_ACTIONS))}")

    # SPAWN_ACTIONS: same sanity check.
    ok &= check("SPAWN_ACTIONS non-empty",
                len(SPAWN_ACTIONS) > 0,
                f"{len(SPAWN_ACTIONS)} action(s): "
                f"{', '.join(hex(a) for a in sorted(SPAWN_ACTIONS))}")

    # ACT_INTRO_CUTSCENE: imported via SnapshotReader chain but confirm it is
    # distinct from SPAWN_ACTIONS (they must be disjoint — spawned() classifies
    # "leaving intro" vs "entering spawn" as separate branches).
    from sm64_events.memory.addresses import ACT_INTRO_CUTSCENE
    ok &= check("ACT_INTRO_CUTSCENE not in SPAWN_ACTIONS",
                ACT_INTRO_CUTSCENE not in SPAWN_ACTIONS,
                f"ACT_INTRO_CUTSCENE = {ACT_INTRO_CUTSCENE:#010x}")

    # KEY_GRAB_LEVELS: Bowser 1 + 2 arenas; verify the set contains the two
    # expected level ids (30 and 33) from the decomp-derived table.
    ok &= check("KEY_GRAB_LEVELS contains arenas 30 and 33",
                {30, 33} <= KEY_GRAB_LEVELS,
                f"KEY_GRAB_LEVELS = {sorted(KEY_GRAB_LEVELS)}")

    # Arena + segment-relevant level ids exist in LEVEL_NAMES. Walk into each
    # of these at the live gate and confirm the level_changed payload matches.
    for lid, name in [(7, "Hazy Maze Cave"), (17, "Bowser in the Dark World"),
                      (19, "Bowser in the Fire Sea"), (21, "Bowser in the Sky"),
                      (23, "Dire, Dire Docks"), (30, "Bowser 1 Arena"),
                      (33, "Bowser 2 Arena"), (34, "Bowser 3 Arena")]:
        ok &= check(f"LEVEL_NAMES[{lid}] == '{name}'",
                    LEVEL_NAMES.get(lid) == name,
                    f"got {LEVEL_NAMES.get(lid)!r}")

    print("\nPhase 1:", "ALL PASS" if ok else "FAILURES — fix addresses.py first")
    print("\nSegment live-gate TODO list (Phase 2 walk-in checklist):")
    if CURR_AREA == 0x0:
        print("  [ ] 1. Hunt CURR_AREA with hunt_value.py; pin address; confirm 1/2/3.")
    else:
        print("  [done] 1. CURR_AREA pinned at", hex(CURR_AREA))
    print("  [done] 2. Pipes fire warp_entered, action 0x1300 (2026-06-12: BitDW")
    print("         pipe, BitS funnel, castle->BitS warp).")
    print("  [done] 3. Fresh-file spawn fires spawned kind=intro at control gain;")
    print("         existing-file loads emit NO spawned event (2026-06-12).")
    print("  [done] 4. B1+B2 keys -> key_grabbed which=bitdw/bitfs, no")
    print("         star_collected; gLastCompleted* untouched both times (2026-06-12).")
    print("  [~] 5. B3 grand star: all components live-verified separately")
    print("         (edge machinery via B1/B2, action 0x1909 observed at the")
    print("         grand star, level 34 walked); composed re-grab optional.")
    print("  [done] 6. Walk-ins 2026-06-12: 6/7/16/17/19/21/22/23/30/33/34;")
    print("         only 26 (courtyard, segment-unused) stays decomp-only.")
    print("  [ ] 7. Practice one LBLJ with the SERVER running (uvicorn, not this")
    print("         tool) → segment_armed then attempt_completed (kind=segment).")

    print("\nPhase 2: live watch — grab stars and verify identity/timing;")
    print("dive-rollout and double/triple-jump to verify the dust-trick")
    print("addresses. Expect: one visible slide/landing frame = DUSTLESS")
    print("(frame perfect); dusty slide/landing frames show [DUST].")
    print("Segment primitives (warp_entered / key_grabbed / spawned / area_changed)")
    print("also print as they fire — use these to work the live-gate checklist above.")
    print("Detector lines come from the REAL detectors — exactly what API listeners")
    print("receive. (Ctrl+C to quit)\n")
    star_det = StarGrabDetector()
    dust_det = DustTrickDetector()
    area_det = AreaChangeDetector()
    warp_det = WarpDetector()
    key_det = KeyGrabDetector()
    spawn_det = SpawnDetector()
    prev_snap = reader.read()
    prev_action = None
    while True:
        s = reader.read()
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
        for ev in area_det.process(prev_snap, s):
            p = ev.payload
            print(f"  >> area_changed: level {p['level']}"
                  f"  {p['from']} -> {p['to']}  frame {ev.frame}")
        for ev in warp_det.process(prev_snap, s):
            p = ev.payload
            print(f"  >> warp_entered: level {p['level']}  area {p['area']}"
                  f"  action {p['action']:#010x}  frame {ev.frame}"
                  f"  [LIVE-GATE: note action id for WARP_ENTRY_ACTIONS]")
        for ev in key_det.process(prev_snap, s):
            p = ev.payload
            print(f"  >> key_grabbed: level {p['level']}  which={p['which']}"
                  f"  frame {ev.frame}"
                  f"  last_completed course={s.last_completed_course}"
                  f" star={s.last_completed_star}"
                  f"  [LIVE-GATE: record last_completed values for VERIFY note]")
        for ev in spawn_det.process(prev_snap, s):
            p = ev.payload
            print(f"  >> spawned: level {p['level']}  kind={p['kind']}"
                  f"  frame {ev.frame}"
                  f"  [LIVE-GATE: confirm kind for file-select spawn on grounds]")
        if s.mario_action != prev_action:
            dust = "  [DUST]" if s.particle_flags & PARTICLE_DUST else ""
            area_str = f"  area {s.curr_area}" if CURR_AREA != 0x0 else ""
            print(f"frame {s.global_timer:>8}  action {s.mario_action:#010x}  "
                  f"stars {s.num_stars:>3}  igt {s.igt_overall:>6} "
                  f"result {s.igt_result:>6}"
                  f"  level {s.curr_level}{area_str}{dust}")
            prev_action = s.mario_action
        prev_snap = s
        time.sleep(0.016)


if __name__ == "__main__":
    main()
