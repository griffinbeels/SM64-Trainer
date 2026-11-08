---
paths:
  - "src/sm64_events/slotlibrary/**"
  - "tools/scene_lab/slot_library.py"
  - "tools/scene_lab/handoff_slot.py"
  - "tools/scene_lab/handoff_try.py"
  - "tools/scene_lab/moment_slot.py"
  - "src/sm64_events/server/scene_moments.py"
  - "src/sm64_events/ui/components/scenemoment.js"
  - "src/sm64_events/ui/components/scenestrip.js"
  - "src/sm64_events/server/scene_slots.py"
  - "tests/test_slot_library.py"
  - "tests/test_handoff_try.py"
  - "tests/test_slot_relocate.py"
  - "tests/test_slot_refit.py"
  - "tests/test_slot_builds.py"
  - "tests/test_slot_translate.py"
  - "tools/scene_lab/slot_atlas.py"
  - "tools/scene_lab/handoff_relocate.py"
---

# The Slot Library — where to change what

| To change... | Edit |
|---|---|
| Slot format, cartridge checks, the one write into game memory | `slotlibrary/usamune.py`. Every write goes through `write_slot_bytes`, inside one slot's range |
| A save state from the other build (US in a JP game, or back) | `slotlibrary/translate.py::for_build`, the one door: `coursesets.place_states` and `handoff_try.install` call it when the state's build differs from the running one. `slotlibrary/atlas.py` owns the per-place atlas (format, `AtlasStore`, the hidden child); `tools/scene_lab/slot_atlas.py` builds one from cold boots of both ROMs (`cold_boot.py`). Static memory moves by `data/build_shifts.tsv` (`tools/import_stroop_maps.py`) |
| Which Usamune build (US or JP) a slot belongs to | `slotlibrary/usamune.py`: `check_cartridge` returns the running `Build`, built from that version's layout rows; every reader and writer takes it. Module constants are slot-format offsets, the same in both builds. `KeptState.build` reads the cartridge name the record already holds, so no record changed |
| Moving Mario inside a slot's data to the running game's place | `slotlibrary/relocate.py` (`to_live_place`); the simulation's evidence is `tools/scene_lab/handoff_relocate.py` |
| How a kept state is stored, named or looked up | `slotlibrary/store.py`; `core/paths.py::slot_library_dir` says where. Nothing deletes a state |
| What counts as a new save | `slotlibrary/watcher.py::watch_step` |
| What goes into the slots on a course entry | `slotlibrary/coursesets.py` |
| The slot refit after each spawn: which slots, its trigger, what it keeps and logs | `slotlibrary/refit.py`; wired by `SceneLab.follow_spawns` (lab only, live server only) |
| The `.sm64state` file and its refusals | `slotlibrary/sharefile.py` |
| Driving it by hand | `tools/scene_lab/slot_library.py` |
| One recorded frame as a slot, the Ghost Lab's save-state strip | `tools/scene_lab/moment_slot.py` (the build and `compare`, its evidence), `server/scene_moments.py` (routes), `ui/components/scenemoment.js` (the press), `ui/components/scenestrip.js` (the strip and keys 1-9) |
| What the strip shows in each slot (picture, level, ROM, loaded last) | `server/scene_slots.py`, read-only; the pictures are the page's, sent with each write |
| Which [[re-simulated scene]]s may make save states | `SAVE_STATES` in `tools/scene_lab/ghost_resim.py`, the one switch; `scene_moments._ghost_source` asks it. A reset's scene stays off until Griffin decides |

Contracts the tests pin:

- **Data only, inside slot ranges.** Griffin's rule (2026-11-05): Ghost Handoff never adds or patches code in the
  game or Usamune.
- **Never patch only the occupied word.** In the simulation a patched word crashes the game on the load frame:
  the object loop still holds the old Mario. Both writers (`place_states`, `handoff_try.install`) run
  `relocate.to_live_place` instead: Mario swaps places inside the slot's data and every pointer follows, found by
  structure. A pool address nothing explains refuses the write. A Mario already in place keeps the bytes as they are.
- **The Mario place is read twice.** `write_slot_bytes(mario_place=)` reads gMarioObject again right before the
  occupied word goes in; a change leaves the slot empty (`MarioMoved`). Only putting a backup back passes None.
- **Relocated bytes are new bytes.** The watcher, once wired, will keep a written relocated slot as a new state.
  The slot refit writes relocated bytes on every reset, so wiring the watcher must skip the bytes the refitter
  wrote (`SlotRefitter._wrote`), or each reset adds a state per slot.
- **The slot refit touches only this place's slots.** After a spawn it refits occupied slots whose trailer level and
  area match the game's; a slot already in place is not written; a slot whose bytes changed since its first read is
  skipped (he may have just saved); a Mario that moves during a write stops the pass. His save joins the library
  as captured before its first refit; the refitter's own bytes do not, so resets add no files. One INFO line and
  one `data/slot_refit.jsonl` line per pass (counts and kinds, no addresses; `tools/what_happened.py` shows it).
  It is the Trainer's action, so it is never a journal event.
- **Capture before the first write.** A capture that fails writes nothing.
- **Translation is exact or nothing.** `for_build` refuses unless the game stands in the state's level and area and
  its main pool starts as the atlas says; a pointer it cannot map is reported (`Translation.unmapped`) and both
  writers refuse the state. Segmented addresses are read only at object rawData and gPlayerSpawnInfos. An object
  whose script the running build lacks is unloaded in the data the way `unload_object` does it, unless anything but
  its list and the scene graph holds it. The kept state stays in its own build: `keep_built` records the image's
  build, not the running game's. Evidence, simulation only (2026-11-08, `tools/scene_lab/slot_atlas.py` and the
  shipped modules end to end): BoB US to JP and JP to US, and 7 of his WF kept states US to JP, each loaded by the
  other build's own Usamune load: Mario 0.000 units off for 300 frames, 0 unmapped pointers.
- **An atlas needs both ROMs and the simulator.** The child runs `slot_atlas.py` with the venv's packages (unicorn is
  a dev dependency today), so the frozen app cannot build one yet.
- **The watcher never runs on the poller's thread.** Nine slots cost 4.3 ms to read and 0.3 ms to hash, and a new
  state adds a compress and a disk write.

The slot refit is the first wired piece: `SlotRefitter` owns its thread and reader, and the broadcaster's observer only
stamps a deadline. The seam for wiring the rest: one worker thread with its own `usamune.attach()` reader (`rom_header` keeps per-reader
state), a `SlotLibrary(slot_library_dir())`, and `watch_step` in a loop that keeps `result.seen`. A course entry
calls `install_for_course` on that same worker. Not wired yet: the watcher and course sets (no poller hook, route
or UI).
