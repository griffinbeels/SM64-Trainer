---
paths:
  - "src/sm64_events/ghost/**"
  - "src/sm64_events/server/ghost_api.py"
  - "src/sm64_events/server/ghost_sharing_api.py"
  - "src/sm64_events/ui/ghostsession.js"
  - "src/sm64_events/ui/ghostlibrary*.js"
  - "src/sm64_events/ui/components/ghostswitch.js"
  - "src/sm64_events/ui/components/ghostlibrary*.js"
  - "plugin/gfxwrap/ghost_*"
---

# Ghost — where to change what

Runtime contracts, format versions and witnesses: [ghost-native-runtime](../../docs/ghost-native-runtime.md).

| To change... | Edit |
|---|---|
| A ghost setting (default, range, sentence, db key, native field) | One row in `ghost/settings.py` `ROWS`. It drives validation, load/apply, the payload, `limits` and the native command. |
| A range or limit the card shows | The row's `limits`. `GET /api/ghost` publishes them and the card reads `session.limits` (`ui/ghostsession.js`). JS never restates a Python number. |
| The words of an on/off switch | `SWITCH_LABELS` in `ui/ghostsession.js`, keyed by the row name |
| The [[Lineup]] on the practice card (seats, track, loading, notes, swap popover, lock) | `ui/lineupmodel.js` turns `session.rivals` (`state`, `reason`, rows with `pending`) into a model; `ui/lineup.js` owns its DOM so seats slide without Preact reconciling mid-move; `ui/components/lineup.js` wires it. Same lineup slides; a different one keeps your seat and exchanges the rest. Loading holds `MIN_LOADING_MS`. Tests: `tests/test_ui_lineup*.py`, `tests/test_ui_rivals_review.py` |
| The on/off control | `GhostToggle` in the card head (`ui/components/ghostswitch.js`); every ghost setting lives in the one Ghost settings sheet. The ghost hotkey flips the same `enabled` |
| What the switch does to a race | Off hides, never revokes: `native_session.py` keeps the roster, origin and activation and sends `hidden`; the renderer runs the race unseen and unheard (`gp_request.hidden`, `GIPC_CAP_HIDDEN`; an older renderer gets the old playback off, `native_channel.py`). On with nobody racing joins this run once ready. Tests: `tests/test_ghost_native_session.py` "switch" cases, the playback and IPC witnesses |
| The ghost hotkey flipping the Ghost Overlay in game | `ghost/dpad_toggle.py`: `hotkey_for` (D-pad Up, or all four C buttons while Usamune's save state byte reads `SAVESTATE_ON_DPAD_UP`), the press rule (a rising edge of the hotkey, other buttons held or not, never in Usamune's menu) and the flip through the card's own GET/PUT; wired as the last poller chain member in `main.build`, which tells `GhostSession.note_hotkey` on change for the payload's `hotkey` and the toggle's hover |
| Which ghosts play | Adaptive `ghost/rivals.py` uses `ghost/adaptive.py` and the current strategy saved PB from `ghost/pb.py`; legacy choices use `ghost/selection.py`. Which ROM's recordings may serve is the strategy's region scope (`ranks/region_scope.py`): shared serves both ROMs, regional only the running one; providers fetch by `PracticeIdentity.region_scope`, `adaptive.ghost_region_scope` decides. Native sets are built in `ghost/native_session.py`. A PB with no poses races your [[best recorded run]] in its seat (`Rivals._pb_seat`); the PB stays the rank. |
| Historical bests and surviving recordings in Ghost Library | `ghost/library_bests.py` resolves winners before joining `sharing_catalog.py` availability. It never changes the current saved PB slot or promotes a slower recording when a winner expires. |
| Reviewing and importing only selected ghosts | `ghost/library_imports.py` owns bounded staging leases; `sharing.py` revalidates selected clips before publishing the collection index; `server/ghost_sharing_api.py` mounts preview/commit/discard jobs. |
| Remembered export credit and saved searches | `ghost/library_preferences.py` validates and stores the existing author key plus versioned dynamic criteria; `ui/ghostlibraryflow.js` keeps pending choices local. |
| The contextual library and guided import/export steps | `ui/components/ghostlibrary*.js`, using the shared Modal and entity picker. `ui/ghostlibrary.js` owns pure selection/filter/group derivations. Browsing never writes the practice target. |
| Inputs an older local clip shows (its playback copy) | `ghost/padfill.py`: the join, its calibration and every refusal. Never write filled pads into the committed clip |
| What the renderer draws, and how it fades | `plugin/gfxwrap/ghost_*`. The fade rule and its constants are in `ghost_occlusion.h`; see `native-plugin.md` |
| A held or ridden object's identity on each cartridge | `plugin/gfxwrap/ghost_behaviours.h`: one row per decomp symbol with its US and JP offset, read through the clip's `rom_layout`; `tests/test_ghost_behaviours.py` checks it against `data/behaviours_*.tsv`. A ROM block the model reads on JP: `jp_blocks` in `renderer/model/assets.c` |
| Where ghost sound plays, and which recording hears it | `ghost/sound_player.py` plays the renderer's shared ring (`ghost_audio.h`) outside Project64; `replay/audio.py` taps it only while Ghosts in recording is on, onto a `GhostTimeline` that only the ghost copy mixes in. The clean recording never hears it |
| The ghost copy (the second recording while Ghosts in recording is on) | `replay/ghostcopy.py` (lane), `gpu_delivery.cpp` twin lane, `fragmentmedia.py` "ghostcopy:" archive, `service.py` cut/save; contract in `docs/replay-gpu-runtime.md`, "The ghost copy" |

**A new on/off setting touches four files:**
1. A `switch(name, db_key, default, sentence, native_field)` row in `ghost/settings.py`.
2. A `SWITCH_LABELS` row in `ui/ghostsession.js`.
3. When the renderer reads it: the field in `ghost/native_protocol.py`, which the channel encodes from `native_switches()`.
4. The native side itself.

Adaptive local PB visibility follows Practice's `current_pbs_by_strat`; strict
`eligible_attempt` certificates govern population admission separately. The
resolved strategy ladder can include live library fits absent from the bundled
seed. `tests/test_ghost_rival_strategy.py` and `tests/test_ui_rivals_review.py`
cover the reported null-ROM Wild Blue PB and the resolved Waluigi1 range.
Practice's Strategy/Overall tabs select the ghost scope: both grade that saved
strategy PB, while Overall admits other compatible strategies and uses the full
Overall curve. `tests/test_ghost_rival_scope.py` pins scope-separated choices,
search guards and native target propagation; the active card offers both tabs
even when their rank ladders coincide.

`tests/test_ghost_settings.py` covers the row with no edit: refused, applied, remembered, carried to the native command, and read by the card. The fixture and the API docs follow the registry. A setting of another kind also needs its sample pair in that test's `SAMPLES`, plus its control in `ui/components/ghostswitch.js`.
