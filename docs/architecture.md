# Architecture & Domain Knowledge

The deep reference for sm64_tracker. CLAUDE.md is the index; this file holds
the knowledge that was expensive to acquire. Update it the moment something
new is learned — with evidence, not just conclusions.

## Data flow

```
Project64 1.6 process (Windows)
      │  ReadProcessMemory, ~60 Hz poll (game logic runs at 30 fps)
┌─────▼──────────┐   GameSnapshot     ┌──────────────┐   Event     ┌───────────────┐
│ memory/pj64.py  │ ─────────────────▶ │ detectors/*  │ ──────────▶ │ server/        │
│ attach, RDRAM   │  core/snapshot.py  │ (prev,curr)→ │             │ broadcaster +  │
│ scan, endian    │                    │ events       │             │ FastAPI + WS   │
└────────▲───────┘                    └──────────────┘             └──────┬────────┘
┌────────┴───────┐                                                  ui/index.html,
│ memory/        │                                                  OBS overlays,
│ addresses.py   │  ← single registry: addresses, actions, names    stats consumers
└────────────────┘
```

Polling at 60 Hz against 30 fps logic observes every game frame; frame
computation is effectively atomic relative to our samples *except* during
multi-address reads spanning a transition (see Pitfalls).

## Memory model

- **Finding RDRAM:** enumerate committed regions of the 32-bit PJ64 process;
  a region ≥ 4 MB whose start matches the libultra osBootConfig signature
  (u32@0x80000308 == 0xB0000000, u32@0x80000318 ∈ {4MB, 8MB},
  u32@0x80000300 ≤ 2) is the RDRAM. Usamune uses the full **8 MB** —
  its own globals live above 0x80400000 (expansion-pak space).
- **Endianness:** PJ64 stores big-endian N64 RAM as little-endian 32-bit
  words. Byte at N64 offset `o` → host `o ^ 3`; aligned u16 → `o ^ 2`
  (LE); aligned u32 → `o` (LE). Implemented once in `memory/base.py`;
  `BufferMemory` mirrors the layout so tests exercise the real decode path.
- **Address provenance:** every entry in `addresses.py` cites its source
  (decomp symbol maps, STROOP configs, or our own empirical hunts) and its
  live-verification date. Useful cross-check sources: ukikipedia.net/wiki/RAM,
  STROOP's Config XMLs (offsetUS attributes), decomp US symbol maps.

### Verified address highlights (full list in addresses.py)

| Symbol | Addr | Notes |
|---|---|---|
| gMarioStates[0] | 0x8033B170 | action +0x0C (u32), actionTimer +0x1A (u16, resets per action), numStars +0xAA (s16) |
| gGlobalTimer | 0x8032D5D4 | u32, +1 per game frame — the primary clock |
| gLastCompletedCourseNum / StarNum | 0x8032DD80 / 0x8032DD84 | s8 each, 1-based, **4 bytes apart** (IDO aligns .data globals) |
| USAMUNE_OVERALL | 0x80417C72 | u16, running overall star time (survives area warps) |
| USAMUNE_STAR_RESULT | 0x80417C74 | u16, exact displayed final time, written at the grab |
| Object pool | 0x8033D488 | 240 × 0x260-byte slots; behavior ptr at +0x20C |

### Known traps (each cost a debugging session)

- **0x8032DDF8 is `gCurrLevelNum`**, not a course number. We once shipped it
  as gLastCompletedCourseNum; symptom: every star reported "Castle Secret"
  with star ids tracking LEVEL ids (SSL=8, LLL=22, WF=24).
- **The vanilla HUD timer (0x8033B26C) stays 0 under Usamune.** The manual's
  "in-game timer" means Usamune's own timer, not the engine's.
- **Object-pool addresses are slot-dependent.** Usamune's section counter
  was found at 0x8033D5DC (slot 0 +0x154) with mirrors in other slots —
  values move with object spawn order per level. Never base events on a
  pool address; identify objects by behavior pointer if pool data is ever
  needed.
- **Adjacent s8 globals sit 4 bytes apart** in IDO-compiled .data.

## Star-grab detection

The game's interaction handler (`interact_star_or_key` in the decomp) runs
`save_file_collect_star_or_key()` — updating gLastCompleted* and Mario's
numStars — **before** setting the star-dance action, on every grab including
re-collections. Therefore:

- **Edge detection**: fire when `curr.mario_action ∈ STAR_GRAB_ACTIONS` and
  `prev` was not. Catches re-grabs; immune to savestate value staleness.
  The set: STAR_DANCE_EXIT 0x1302, WATER 0x1303, NO_EXIT 0x1307,
  FALL_AFTER_STAR_GRAB 0x1904 (midair grabs pass 0x1904 → 0x130x; the edge
  fires once). Grand-star/key cutscenes are excluded by design.
- **Identity**: course = gLastCompletedCourseNum (0 is VALID — castle secret
  stars), star = gLastCompletedStarNum − 1 (game is 1-based). The boot
  sentinel (both 0) is excluded by the star_id guard.
- **Touch frame**: `global_timer − mario_actionTimer` recovers the exact
  grab frame regardless of polling latency (actionTimer resets to 0 when an
  action starts). This back-computation trick works for any per-frame
  counter paired with the event.
- **already_collected**: numStars unchanged across the edge.

## Timers (the full saga — read before touching IGT)

Usamune keeps multiple clocks; we learned this incrementally:

1. **Section counter** (object pool, e.g. 0x8033D5DC): the on-screen running
   timer during play. Resets on *area* warps within a level (per the manual:
   "Section Timer resets the in-game timer each time you enter a different
   area"). Single-area stars made it indistinguishable from the overall time
   — which is why early validation passed and the SSL pyramid star exposed
   it (reported 2 s instead of 20 s).
2. **USAMUNE_OVERALL (0x80417C72, u16)**: running overall star time. Ticks
   with the section counter but does NOT reset at area warps; resets with
   Usamune level resets. Static expansion-RAM global → trustworthy address.
3. **USAMUNE_STAR_RESULT (0x80417C74, u16)**: written at the star grab with
   the EXACT final time Usamune displays; 0 until the first grab; persists
   afterwards. Neighbor 0x80417C76 also gets a small write at the grab;
   0x80417C70 is constant 256.

**IGT source precedence** (in `star_grab.py::_igt_at`):
1. `result` — STAR_RESULT when freshly written (value changed within
   RESULT_FRESH_FRAMES of the touch and ≠ 0). Exact by construction; no
   calibration.
2. `counter` — OVERALL back-computed to the touch frame, plus
   `DISPLAY_TICK = 1`: live comparison showed Usamune's frozen display is
   exactly one frame ahead of the internal counter sampled at the touch
   (its timer object ticks once more before the freeze).
3. `reconstructed` — reset-race guard, applied over BOTH sources: if the
   overall counter dropped within RESET_GRACE_FRAMES (30 ≈ 1 s) of the
   touch, the player's reset raced the grab and even Usamune's own result
   store holds a near-zero time; report the prior attempt's clock,
   extrapolated from the detector's sample history to the touch frame
   (`igt_reconstructed: true`). The 1 s threshold encodes a domain fact: no
   star is humanly grabbable within 30 frames of an attempt start.

The detector keeps a ~5 s history of `(global_timer, overall, result)`
samples for freshness/reset analysis; history clears itself when
global_timer jumps backward (savestate/console reset).

## Lifecycle & error handling

- `game_reset` event: global_timer moved backward between ticks.
- `emulator_connected` / `emulator_disconnected`: attach lifecycle; the
  poller retries attach every 2 s, probes layout plausibility after attach
  (refuses + 5 s backoff on impossible values — wrong ROM or wrong registry,
  both have happened), and clears `_prev` on every discontinuity so
  detectors never see a stale pair.
- Poller task death is logged CRITICAL via a done-callback; `/health`
  exposes attach state, client count, last frame.

## Event schema

Versioned envelope (`core/events.py`), seq assigned by the broadcaster:

```json
{"v": 1, "seq": 412, "type": "star_collected", "frame": 456052,
 "timestamp_utc": "2026-06-10T22:14:03.512000Z",
 "payload": {"course_id": 8, "course_name": "Shifting Sand Land",
             "star_id": 1, "star_name": "Shining Atop the Pyramid",
             "already_collected": true,
             "igt_frames": 595, "igt": "0'19\"83",
             "igt_source": "result", "igt_reconstructed": false}}
```

Other types: `game_reset` (frame = post-reset timer), `emulator_connected`,
`emulator_disconnected` (empty payloads). `frame` is always game frames
(30 fps); `igt` format is Usamune's M'SS"CC (centiseconds = frames×100/30).

## Memory hunting playbook

When a new value must be located (no public RAM map for Usamune):

1. **Rate scan** — `tools/find_timer.py`: snapshots all RDRAM, keeps
   addresses ticking 25–65/s across rounds. The tick window scales by the
   MEASURED elapsed time between reads (Python processing time between
   samples once silently disqualified every true counter, including the
   known-good gGlobalTimer — that control failing is what exposed the bug).
2. **Exact-value intersection** — `tools/hunt_value.py`: the human types the
   number displayed on screen; scan for it (±2 frames); repeat with a second
   distinct value; the intersection collapses 8 MB to a handful. This is how
   USAMUNE_STAR_RESULT was found (2 values → 1 address).
3. **Characterize** — `tools/watch_timer.py`: watch the candidate (and
   neighbors — mod globals cluster) across scenarios: level change, area
   warp, savestate, Usamune reset, display OFF. Only then promote it to the
   registry.

Principles learned:
- A scan can only distinguish quantities that DIFFER during the scan —
  design observations to break degeneracies (section vs overall were
  identical until an area warp).
- Correlated "garbage" means wrong symbol at that address (level ids in the
  star field); random garbage means wrong decode.
- Multi-address read batches are not atomic across a game transition —
  treat single-sample anomalies at transition instants as read races.
- Prefer values the mod stores for its own display: they are calibrated by
  definition.

## Testing strategy

- Detectors: synthetic `GameSnapshot` sequences via the `snap(**overrides)`
  fixture; every live bug becomes a regression test with the trace's real
  numbers (see test_star_grab.py for three examples).
- Memory: `BufferMemory` (full 8 MB image, loud bounds checks) exercises the
  real endian path; `looks_like_rdram` is a pure function with fake readers.
- Server: `tick()` is the testable unit (injectable reader); endpoint tests
  use FastAPI TestClient with an `OfflineMemory` stub; the WS path is tested
  end-to-end via a `debug_hooks`-gated emit route.
- The live gate (`tools/verify_addresses.py`) is part of acceptance for any
  memory-layer change: its Phase 2 runs the REAL StarGrabDetector, so what
  it prints is exactly what API clients receive.

## Roadmap (from the original spec; unbuilt)

- Stats consumer (per-star attempt logs, last-N averages, reset counts) —
  a separate process consuming `/ws/events`; the payload already carries
  everything needed (igt_frames, igt_source, game_reset segmentation).
- Dedicated key / grand-star event types (currently Bowser key grabs may
  emit star_collected with course 16/17 — documented limitation).
- Richer tracker overlay UI (ui zone or a new top-level frontend/).
- More detectors: deaths, level entry, coin count.
