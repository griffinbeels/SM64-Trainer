---
paths:
  - "src/sm64_events/slotlibrary/**"
  - "tools/scene_lab/slot_library.py"
  - "tools/scene_lab/handoff_slot.py"
  - "tools/scene_lab/handoff_try.py"
  - "tools/scene_lab/moment_slot.py"
  - "src/sm64_events/server/scene_moments.py"
  - "src/sm64_events/ui/components/scenemoment.js"
  - "tests/test_slot_library.py"
  - "tests/test_handoff_try.py"
  - "tests/test_slot_relocate.py"
  - "tests/test_slot_refit.py"
  - "tests/test_slot_builds.py"
  - "tests/test_slot_translate.py"
  - "tests/test_star_sets.py"
  - "tests/test_slot_clocks.py"
  - "tools/scene_lab/slot_atlas.py"
  - "tools/scene_lab/handoff_relocate.py"
---

# The Slot Library — where to change what

| To change... | Edit |
|---|---|
| Slot format, cartridge checks, the one write into game memory | `slotlibrary/usamune.py`. Every write goes through `write_slot_bytes`, inside one slot's range; emptying a slot is `empty_slot` (its occupied word to zero) |
| A save state from the other build (US in a JP game, or back) | `slotlibrary/translate.py::for_build`, the one door: `coursesets.place_states` and `handoff_try.install` call it when the state's build differs from the running one. `slotlibrary/atlas.py` owns the per-place atlas (format, `AtlasStore`, the hidden child); `tools/scene_lab/slot_atlas.py` builds one from cold boots of both ROMs (`cold_boot.py`). Static memory moves by `data/build_shifts.tsv` (`tools/import_stroop_maps.py`) |
| Which Usamune build (US or JP) a slot belongs to | `slotlibrary/usamune.py`: `check_cartridge` returns the running `Build`, built from that version's layout rows; every reader and writer takes it. Module constants are slot-format offsets, the same in both builds. `KeptState.build` reads the cartridge name the record already holds, so no record changed |
| Moving Mario inside a slot's data to the running game's place | `slotlibrary/relocate.py` (`to_live_place`); the simulation's evidence is `tools/scene_lab/handoff_relocate.py` |
| How a kept state is stored, named or looked up | `slotlibrary/store.py`; `core/paths.py::slot_library_dir` says where. Nothing deletes a state |
| What counts as a new save | `slotlibrary/keeper.py::SetKeeper.watch` on the slot worker (every `refit.WATCH_SECONDS`); `watcher.py::watch_step` for the command line |
| The course default and each star's set (a slot -> kept state map), the file | `slotlibrary/coursesets.py::CourseSets` (`course-sets.json` version 2; version 1 is read and converted) |
| Which set applies (course, star), when it is written, his saves joining it, the Ghost Lab strip's answer | `slotlibrary/keeper.py::SetKeeper`; routes in `server/scene_lab_api.py::_mount_slot_set_routes` (shape in `docs/api.md`) |
| The slot refit after each spawn: which slots, its trigger, what it keeps and logs | `slotlibrary/refit.py`; wired by `SceneLab.follow_spawns` (lab only, live server only) |
| The `.sm64state` file and its refusals | `slotlibrary/sharefile.py` |
| Driving it by hand | `tools/scene_lab/slot_library.py` |
| One recorded frame as a slot, the Scene viewer's Save states row | `tools/scene_lab/moment_slot.py` (the build and `compare`, its evidence), `server/scene_moments.py` (routes), `ui/components/scenemoment.js` (the row) |
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
- **Relocated bytes are new bytes.** A slot's hash never names its kept state once the Trainer moved or translated
  it, so `keeper.SlotLedger` holds what the Trainer last wrote into each slot (the kept state and the bytes' hash).
  The set write, the refit and the Ghost Lab write record there; the watch, the refit's keeping and the capture
  before a write skip those bytes, or each reset would add a state per slot and every write would join a set.
- **One worker, one lock.** The set write on a context change, the refit after a spawn and the watch for his saves
  run on the refitter's thread with its one reader, under `SetKeeper.lock`; the Ghost Lab's write takes the same
  lock. The set write runs before the refit in the same pass.
- **Star sets.** Context = (the course Mario stands in, a star). Entering a course takes the target's star when it is
  in this course, else the course default; a star target pick (`target_changed`) or a star grab (`star_collected`) in
  this course switches the star. A context change writes every slot the resolved set names (the course default
  with the star's slots over it), empties a slot holding the Trainer's bytes for another set of this course, and
  leaves anything else. A new save of his joins the current set at its slot; the first watch of a game only takes
  stock. Every set starts empty, and nothing is written while none names anything. Simulation evidence
  (2026-11-08): Usamune's own save into slot 5 of a cold-booted US BoB joined the star set at slot 5 on the next
  watch (48 ms with the new state's compress; 0.2 ms idle).
- **The strip's marks.** `loaded_last`: a `state_loaded` only sets a flag (no pass); the next watch reads
  `usamune.load_slot` (state block +0x118, the slot Usamune's load reads; simulation, both builds: that byte alone
  chose the slot, 0.00 units off). VERIFY live that his own load sets it. `picture`: the Ghost Lab write asks
  `SceneLab.frame_picture` for the open scene's frame before it writes, and the library keeps it as
  `pictures/<id>.jpg`; a state from anywhere else has none.
- **The slot refit touches only this place's slots.** After a spawn it refits occupied slots whose trailer level and
  area match the game's; a slot already in place is not written; a slot whose bytes changed since its first read is
  skipped (he may have just saved); a Mario that moves during a write stops the pass. His save joins the library
  as captured before its first refit; the refitter's own bytes do not, so resets add no files. One INFO line and
  one `data/slot_refit.jsonl` line per pass (counts and kinds, no addresses; `tools/what_happened.py` shows it).
  It is the Trainer's action, so it is never a journal event.
- **Capture before the first write.** A capture that fails writes nothing.
- **His session clocks stay his.** Usamune's save copies its state block's +0x90 and +0x94 (60 a second since power
  on) and +0xEC (the game time on the pause screen) into the trailer at +0x100, +0x104 and +0x120; its load puts
  them back. Every Trainer write (`place_states`, the refit, `handoff_try.install` and `restore`) runs
  `usamune.with_live_clocks` last, so a state from another session never sets his timer (Griffin, 2026-11-08: "we
  shouldn't copy over the "overall session timer" clock"). The kept state keeps its own clocks.
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

`SlotRefitter` owns the one thread and reader; the broadcaster's observer only stamps a deadline (a spawn, a star pick
or grab, an edit of the sets). The Ghost Lab strip reads `GET /api/scene-lab/slot-sets` and `slot_sets_changed`.
