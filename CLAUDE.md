# sm64_tracker — Claude Development Guide

Read this first, every session. It is the index to the codebase and the
contract for how work happens here. This project is developed **exclusively
by Claude**; the human runs the emulator and verifies live behavior. A future
session has no memory of past ones — these docs ARE the memory.

## What this is

A Python server that reads Super Mario 64 (**Usamune v1.93u** practice ROM)
memory out of **Project64 1.6** on Windows via `ReadProcessMemory`, detects
game events (star grabs with exact Usamune timing, game resets), and
broadcasts them as JSON over WebSocket to any listener (overlays, stats
tools, the built-in viewer). PJ64 1.6 has no scripting API — external memory
polling is the only integration path, and every memory address was located
and live-verified empirically.

Stack: Python 3.12+ managed by **uv** (never pip), FastAPI + uvicorn, pymem,
pytest. Design docs from the original build: `docs/superpowers/`.

## Commands

```
uv sync                                              # install/refresh deps
uv run pytest -q                                     # MUST pass before any merge
uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064   # run server
uv run python tools/verify_addresses.py              # live gate (needs PJ64 + ROM running)
```

## Module map — where to change what

| To change... | Edit |
|---|---|
| Memory addresses, action IDs, course/star names | `src/sm64_events/memory/addresses.py` — THE registry, single source of truth |
| Endian decoding / typed reads | `src/sm64_events/memory/base.py` — the ONLY place that knows PJ64 byte order |
| Process attach / RDRAM discovery | `src/sm64_events/memory/pj64.py` |
| Object-pool decoding helpers | `src/sm64_events/memory/objects.py` |
| In-memory test double | `src/sm64_events/memory/buffer.py` |
| Fields sampled each tick | `src/sm64_events/core/snapshot.py` (GameSnapshot + SnapshotReader) |
| Event envelope / wire format | `src/sm64_events/core/events.py` |
| Star-grab detection + IGT logic | `src/sm64_events/detectors/star_grab.py` |
| game_reset detection | `src/sm64_events/detectors/lifecycle.py` |
| A NEW event type | new file in `detectors/` + wire into `main.py` (recipe below) |
| Poll loop, attach retry, layout sanity | `src/sm64_events/server/poller.py` |
| WS fan-out, seq numbers | `src/sm64_events/server/broadcaster.py` |
| HTTP/WS endpoints | `src/sm64_events/server/app.py` |
| Built-in viewer UI | `src/sm64_events/ui/index.html` (served live; edit + refresh, no restart) |
| Wiring / startup | `src/sm64_events/main.py` (composition root — the only cross-zone file) |
| Logging setup | `src/sm64_events/core/logging_setup.py` |
| Memory-hunting diagnostics | `tools/` (see docs/architecture.md → Memory hunting) |

Tests mirror modules: `tests/test_<module>.py`. Find behavior by reading the
test file first — they are the executable spec.

## Parallel work zones

Zones that can be worked **concurrently without conflicts** (one branch /
worktree each):

- **detectors zone**: `detectors/` + `tests/test_<detector>.py`
- **server zone**: `server/` + `tests/test_poller|broadcaster|app.py`
- **ui zone**: `src/sm64_events/ui/` (pure frontend; talks to the API over
  WS/HTTP only — never imports Python code)
- **memory zone**: `memory/` + `tools/` + their tests
- **docs zone**: `docs/`, README, this file

**Shared contract files — coordinate, never edit in two branches at once:**
`core/events.py`, `core/snapshot.py`, `memory/addresses.py`, `main.py`.
If a feature needs a contract change, land that change on master first, then
fan out the dependent work.

Merge discipline: `git merge --no-ff`, full `uv run pytest -q` on the merged
result, branch deleted after merge.

## Architecture in one paragraph

`Pj64Memory` (attach + endian-correct reads) → `Poller` at ~60 Hz builds an
immutable `GameSnapshot` per tick → each detector gets consecutive
`(prev, curr)` pairs and returns `Event`s → `Broadcaster` assigns seq numbers
and fans out to WebSocket clients; FastAPI serves `/` (viewer), `/health`,
`/state`, `/ws/events`. Detectors hold no I/O; the poller holds no game
logic. **Read `docs/architecture.md` before touching `memory/` or
`detectors/` — it contains domain knowledge that was expensive to learn.**

## Domain rules — do not break these

1. `addresses.py` is the only home for memory addresses. A new address needs
   a source comment and must pass `tools/verify_addresses.py` (or a watch
   session) before events may depend on it. Mark unverified entries `VERIFY`.
2. PJ64 stores N64 RAM as little-endian 32-bit words. The XOR address math
   lives in `memory/base.py` only. Never duplicate it.
3. Star grabs MUST fire on re-collection: detection is an action-EDGE into
   `STAR_GRAB_ACTIONS`, never save-flag diffing.
4. IGT comes from the Usamune expansion-RAM globals (`USAMUNE_STAR_RESULT`
   preferred, `USAMUNE_OVERALL` fallback) — never the vanilla HUD timer
   (stays 0) and never the object-pool section counter (resets on area
   warps; slot-dependent). Full story: docs/architecture.md → Timers.
5. Detectors receive consecutive pairs from one session, may keep bounded
   internal state, and must self-heal when `global_timer` jumps backward.
6. Events report the number the player SEES (display conventions like
   `DISPLAY_TICK` are calibrated in `star_grab.py` — don't "simplify" them).
7. Read-only: never write to emulator memory.
8. Timestamps UTC; the primary clock is game frames (30 fps). Wall clock is
   metadata only.
9. The poller refuses to emit events on implausible reads (layout mismatch)
   — keep that guard; it has caught real bugs in our own registry.

## Recipes

### Add a new event type
1. Write tests first: `tests/test_<name>.py` with synthetic `GameSnapshot`
   pairs (copy the `snap(**overrides)` fixture pattern from
   `tests/test_star_grab.py`).
2. Create `detectors/<name>.py` with `process(prev, curr) -> list[Event]`.
3. Need new memory fields? `addresses.py` (with `VERIFY` + source) →
   defaulted field on `GameSnapshot` → read in `SnapshotReader` → live-verify
   before trusting.
4. Wire the detector into the list in `main.py` (order: resets before grabs).
5. Render it in `ui/index.html` if user-visible; document the payload in
   README → Event schema.
6. `uv run pytest -q`; live check via the harness; update the module map
   above if you added files.

### Locate an unknown memory value (e.g., for a new event)
Use the three-tool playbook, in order:
1. `tools/find_timer.py` — finds steadily *ticking* counters (rate-scan).
2. `tools/hunt_value.py` — Cheat-Engine-style exact-value intersection for
   *displayed* numbers (ask the human to type what's on screen).
3. `tools/watch_timer.py` — characterize a candidate across scenarios
   (level change, savestate, area warp, display off) before trusting it.
Methodology, war stories, and traps: docs/architecture.md → Memory hunting.

### Build a new UI / consumer
Connect to `ws://127.0.0.1:8064/ws/events`; every message is the versioned
envelope in README → Event schema. Initial state: `GET /state`; liveness:
`GET /health`. A richer frontend should live in the ui zone (or a new
top-level `frontend/` directory) and must only speak to the HTTP/WS API.

## Definition of done — every change

- [ ] `uv run pytest -q` passes (and new behavior has tests)
- [ ] new memory reads live-verified with the human via the harness
- [ ] module map above updated if files were added/moved
- [ ] README updated if the user-facing surface changed (endpoints, payload)
- [ ] `docs/architecture.md` updated if domain knowledge was gained
- [ ] commit messages explain WHY (follow the style in `git log`)

## Documentation contract (this is enforced, not optional)

Because every future session starts from zero, **stale documentation is a
broken build**. Any merge that adds a module, memory address, event type,
endpoint, or tool MUST update this file's module map, the README, and (for
domain knowledge) `docs/architecture.md` in the same branch. When something
is learned the hard way — a wrong address, a race, a calibration constant, a
trap — write it into `docs/architecture.md` immediately, with the evidence.
The registry pattern applies to docs too: one fact, one authoritative place,
linked from here.
