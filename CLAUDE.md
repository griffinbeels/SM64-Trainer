# sm64_tracker — Claude Development Guide

This project is developed **exclusively by Claude**; the human runs the
emulator and verifies live behavior. Future sessions have no memory of past
ones — this file and `docs/architecture.md` ARE the memory. Keep them lean
and current: stale documentation is a broken build.

## What this is

A Python server that reads Super Mario 64 (**Usamune v1.93u** practice ROM)
memory out of **Project64 1.6** (Windows) via `ReadProcessMemory`, detects
game events — star grabs with exact Usamune timing, game resets — and
broadcasts JSON over WebSocket. PJ64 1.6 has no scripting API; external
memory polling is the only path, and every address was located and
live-verified empirically. Stack: Python 3.12+ via **uv** (never pip),
FastAPI + uvicorn, pymem, pytest.

## Commands

```
uv sync
uv run pytest -q                                     # MUST pass before any merge
uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064
uv run python tools/verify_addresses.py              # live gate (needs PJ64 + ROM)
```

## Module map — where to change what

| To change... | Edit |
|---|---|
| Memory addresses, action IDs, course/star names, traps | `memory/addresses.py` — THE registry; richly commented, read it |
| Endian decoding / typed reads | `memory/base.py` — the ONLY place that knows PJ64 byte order |
| Process attach / RDRAM discovery | `memory/pj64.py` |
| Object-pool decoding | `memory/objects.py` · test double: `memory/buffer.py` |
| Fields sampled each tick | `core/snapshot.py` |
| Event envelope / wire format | `core/events.py` |
| Star-grab + IGT logic | `detectors/star_grab.py` — docstrings carry the domain rationale |
| game_reset | `detectors/lifecycle.py` |
| Poll loop, attach retry, layout sanity | `server/poller.py` |
| WS fan-out, seq numbers | `server/broadcaster.py` |
| HTTP/WS endpoints | `server/app.py` |
| Built-in viewer UI | `ui/index.html` — served per request: edit + refresh, no restart |
| Wiring / startup / logging | `main.py` (composition root), `core/logging_setup.py` |
| Memory-hunting diagnostics | `tools/` — playbook in docs/architecture.md |

(All paths under `src/sm64_events/` unless noted.) Tests mirror modules:
`tests/test_<module>.py` — read the test file first; it's the executable spec.

## Parallel work zones

Safe to work concurrently (one branch/worktree each): **detectors/**,
**server/**, **ui/**, **memory/ + tools/**, **docs/** — each with its tests.
**Shared contracts — never edit in two branches at once:** `core/events.py`,
`core/snapshot.py`, `memory/addresses.py`, `main.py`. Contract changes land
on master first, then dependent work fans out. Merge with `--no-ff`; run the
full suite on the merged result; delete the branch.

## Domain rules

1. New memory address → `addresses.py` only, with source comment, marked
   `VERIFY` until it passes the live gate with the human.
2. Star grabs MUST fire on re-collection: action-EDGE detection, never
   save-flag diffing.
3. IGT comes from the Usamune expansion-RAM globals (see star_grab.py for
   the result → counter → reconstructed precedence). Never the vanilla HUD
   timer, never object-pool addresses (slot-dependent — see addresses.py).
4. Detectors get consecutive (prev, curr) pairs, may keep bounded internal
   state, must self-heal when global_timer jumps backward.
5. Calibration constants (DISPLAY_TICK etc.) encode live-measured behavior —
   don't "simplify" them; their evidence is in the docstrings.
6. Read-only: never write to emulator memory.
7. Timestamps UTC; the primary clock is game frames (30 fps).
8. Keep the poller's implausible-read refusal — it has caught bugs in our
   own registry.

## Recipes

**Add a new event type:** tests first (`snap(**overrides)` fixture pattern
from test_star_grab.py) → `detectors/<name>.py` with
`process(prev, curr) -> list[Event]` → new memory fields go through
addresses.py (+VERIFY) and a defaulted GameSnapshot field → wire into
`main.py` (resets before grabs) → render in `ui/index.html` if user-visible
→ document payload in README → full pytest + live check.

**Locate an unknown memory value:** `tools/find_timer.py` (ticking
counters) → `tools/hunt_value.py` (exact displayed values) →
`tools/watch_timer.py ADDR:u16` (characterize across scenarios). Methodology
and pitfalls: docs/architecture.md → Memory hunting.

**Build a UI / consumer:** speak only to the API — `ws://…/ws/events`
(schema in README), `GET /state` for initial state, `GET /health` for
liveness. Heavier frontends go in the ui zone or a new top-level dir.

## Definition of done — every merge

- `uv run pytest -q` passes; new behavior has tests
- new memory reads live-verified with the human via the harness
- this module map updated if files were added/moved; README updated if the
  consumer-facing surface changed; docs/architecture.md updated if domain
  knowledge was gained (record hard-won facts WITH their evidence,
  immediately — the next session has no memory of this one)
- one fact, one authoritative place: code docstrings for module-local
  knowledge, addresses.py for memory facts, README for the API surface,
  architecture.md only for cross-cutting knowledge — link, don't duplicate
- commit messages explain WHY (follow the style in `git log`)
