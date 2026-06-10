# Architecture & Domain Knowledge

CLAUDE.md is the index; this file holds only knowledge that has no better
home. Facts that belong to one module are documented IN that module —
follow the pointers instead of duplicating here.

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
│ memory/        │                                                  overlays, stats
│ addresses.py   │  ← single registry: addresses, actions, names    consumers
└────────────────┘
```

Polling at 60 Hz against 30 fps logic observes every game frame. Detectors
hold no I/O; the poller holds no game logic; `main.py` wires everything.

## Where the deep facts live (authoritative homes)

- **Addresses, provenance, traps** (gCurrLevelNum trap, vanilla-HUD-timer
  trap, object-pool slot fragility): inline comments in
  `memory/addresses.py`. Cross-check sources are listed in its docstring.
- **Endian decode rules** (PJ64 LE-word storage, XOR offsets):
  `memory/base.py` docstring.
- **RDRAM discovery** (osBootConfig signature scan, 8 MB expansion RAM):
  `memory/pj64.py` docstring. Usamune's own globals live above 0x80400000.
- **Star-grab detection rationale** (edge detection, why re-collections
  fire, identity ordering inside the game frame) and **IGT source
  precedence** (result → counter → reconstructed, DISPLAY_TICK, reset-race
  guard): `detectors/star_grab.py` docstrings.
- **Event schema**: README → Event schema (consumer-facing single source).

## Why there are three timers (history, not derivable from code)

Usamune keeps a SECTION counter (resets on every area warp inside a level),
a running OVERALL star-time counter, and a final-result store written at
the grab. The section counter lives in object-pool behavior data and was
our first IGT source — it validated perfectly on single-area stars (where
section == overall) and failed on "Inside the Ancient Pyramid" (multi-area).
The overall counter and result store are static expansion-RAM globals and
are what events use now. Lesson encoded here: validation scenarios must
break the degeneracy between candidate interpretations, not just confirm
values match.

## Memory hunting playbook

No public RAM map exists for Usamune internals; locate values empirically:

1. **Rate scan** — `tools/find_timer.py`: keeps addresses ticking 25–65/s
   across rounds. Tick windows scale by MEASURED elapsed time between
   reads (a fixed 1 s assumption once disqualified every true counter,
   including the known-good gGlobalTimer — when a control fails, suspect
   the filter).
2. **Exact-value intersection** — `tools/hunt_value.py`: the human types
   the number displayed on screen; intersect scans across two distinct
   values. This collapsed 8 MB to the single result-store address.
3. **Characterize** — `tools/watch_timer.py ADDR:u16`: watch candidates
   (and neighbors — mod globals cluster) across level change, area warp,
   savestate, Usamune reset, display OFF. Only then promote to the
   registry, marked VERIFY until the live gate passes.

Principles:
- A scan only distinguishes quantities that DIFFER during the scan.
- Correlated "garbage" = wrong symbol at that address; random garbage =
  wrong decode.
- Multi-address reads are not atomic across a game transition; lone
  anomalies at transition instants are read races until they repeat.
- Prefer values the mod stores for its own display — calibrated by
  definition.

## Testing strategy

- Detectors: synthetic snapshot sequences (`snap(**overrides)` fixture);
  every live bug becomes a regression test carrying the trace's real
  numbers.
- Memory: `BufferMemory` (full 8 MB, loud bounds checks) exercises the real
  endian path.
- Server: `tick()` is the testable unit; endpoints via TestClient with an
  OfflineMemory stub; WS tested end-to-end through the debug emit route.
- Live gate: `tools/verify_addresses.py` Phase 2 runs the REAL detector —
  required for any memory-layer change.

## Roadmap (unbuilt)

- Stats consumer (attempt logs, last-N averages, reset counts) as a
  separate `/ws/events` client — payload already carries what it needs.
- Dedicated key / grand-star events (Bowser key grabs currently emit
  star_collected with course 16/17 — documented limitation).
- Richer tracker overlay (ui zone or a new top-level frontend/).
- More detectors: deaths, level entry, coin count.
