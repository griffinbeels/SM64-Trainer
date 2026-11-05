---
paths:
  - "src/sm64_events/slotlibrary/**"
  - "tools/scene_lab/slot_library.py"
  - "tools/scene_lab/handoff_slot.py"
  - "tools/scene_lab/handoff_try.py"
  - "tests/test_slot_library.py"
  - "tests/test_handoff_try.py"
---

# The Slot Library — where to change what

| To change... | Edit |
|---|---|
| Slot format, cartridge checks, the one write into game memory | `slotlibrary/usamune.py`. Every write goes through `write_slot_bytes`, inside one slot's range |
| How a kept state is stored, named or looked up | `slotlibrary/store.py`; `core/paths.py::slot_library_dir` says where. Nothing deletes a state |
| What counts as a new save | `slotlibrary/watcher.py::watch_step` |
| What goes into the slots on a course entry | `slotlibrary/coursesets.py` |
| The `.sm64state` file and its refusals | `slotlibrary/sharefile.py` |
| Driving it by hand | `tools/scene_lab/slot_library.py` |

Contracts the tests pin:

- **Never patch the occupied word.** Usamune refuses a load when gMarioObject differs, and that is reported per
  state. In the simulation a patched word crashes the game on the load frame (2026-11-05, cause undiagnosed).
- **Capture before the first write.** A capture that fails writes nothing.
- **The watcher never runs on the poller's thread.** Nine slots cost 4.3 ms to read and 0.3 ms to hash, and a new
  state adds a compress and a disk write.

The seam for wiring: one worker thread with its own `usamune.attach()` reader (`rom_header` keeps per-reader
state), a `SlotLibrary(slot_library_dir())`, and `watch_step` in a loop that keeps `result.seen`. A course entry
calls `install_for_course` on that same worker. Not wired yet: no poller hook, route or UI.
