# SM64 Event API — Design Spec

**Date:** 2026-06-10
**Status:** Approved design, pre-implementation
**Goal one:** Detect every star grab (including re-collections) in SM64 Usamune v1.93u running in Project64 1.6, identify exactly which star, and broadcast it as an event to all API listeners with frame-accurate timing.

## Context and constraints

- **Emulator:** Project64 1.6 (hard constraint — user's practice setup). PJ64 1.6 has no scripting API, so integration is via external process-memory reading (the STROOP approach).
- **ROM:** SM64 Usamune v1.93u, built on the US ROM. Core memory layout is expected to match US addresses; this is verified at startup, never assumed silently.
- **Server language:** Python (uv-managed venv, per machine conventions).
- **Consumers:** Web/browser listeners first (OBS browser-source overlays, dashboards) → WebSocket transport.
- **Platform:** Windows 11, server and emulator on the same machine.
- **Direction:** This is goal one of a larger system: practice stats (time-to-star, reset tracking, last-N averages) and a general SM64 event API. The design optimizes for adding event types and consumers without reworking the core.

## Non-goals (goal one)

- No stats computation in the server (averages, reset counts). Stats are a future *consumer* of the event stream; this design's obligation is that events carry enough data (frames, star identity, `already_collected`, reset markers) that stats need nothing more.
- No event types beyond `star_collected` and lifecycle events (`emulator_connected`, `emulator_disconnected`, `game_reset`).
- No write access to game memory. Read-only.
- No support for emulators other than PJ64 1.6.

## Timing model (foundation)

Wall-clock timestamps from a polling loop carry ±16–33 ms jitter — unacceptable for performance stats. The game provides a better clock:

- **`gGlobalTimer`** increments once per game frame (30 Hz). All events are stamped in game frames (`frame` field). Wall-clock UTC is attached as secondary metadata only.
- **Back-computation for frame accuracy:** the Mario struct's *action timer* resets to 0 on the frame an action starts and increments per frame. Even if the poll observes the star dance N frames late:

  ```
  grab_frame = global_timer − mario_action_timer
  ```

  recovers the exact frame the star-dance action began, independent of polling jitter.
- Durations (e.g., time to complete a star) are exact frame differences; frames convert to seconds at 30 fps for display.
- All future detectors follow the same pattern: stamp in game frames, back-compute true start frames from in-game counters where available.

## Architecture

```
PJ64 1.6 process
      │  ReadProcessMemory (~60 Hz poll)
┌─────▼──────────┐   GameSnapshot    ┌──────────────┐   Event    ┌───────────────┐
│ Memory client   │ ────────────────▶ │  Detectors    │ ─────────▶ │ Event bus +   │
│ (attach, find   │                   │ (pure funcs:  │            │ FastAPI/WS    │
│  RDRAM, decode) │                   │  prev,curr →  │            │ broadcast     │
└────────────────┘                   │  events)      │            └──────┬────────┘
        ▲                            └──────────────┘                    │ JSON
┌───────┴────────┐                                              OBS overlay, stats
│ Address registry│                                              loggers, anything WS
└────────────────┘
```

Four modules, composed via constructor injection, wired in `main.py`.

### Module 1: Memory client (`memory/pj64.py`)

**What it does:** Attaches to the Project64 1.6 process and exposes reads at N64 virtual addresses.

**Contract:**
```python
class N64Memory(Protocol):
    def read_u8(self, addr: int) -> int: ...
    def read_u16(self, addr: int) -> int: ...
    def read_u32(self, addr: int) -> int: ...
    def read_s8(self, addr: int) -> int: ...
    def read_s16(self, addr: int) -> int: ...
    @property
    def attached(self) -> bool: ...
```

**Responsibilities:**
- Find the `Project64.exe` process (pymem / ctypes `ReadProcessMemory`).
- Locate the emulated RDRAM base by scanning the process's committed memory regions for an SM64 signature (known boot/header words at RDRAM offset 0). Cache the base; re-scan on read failure.
- Own the endianness quirk: PJ64 stores N64 RDRAM as little-endian 32-bit words. Byte reads XOR the low address bits with 3, halfword reads with 2, aligned word reads decode directly. **Nothing outside this module knows this.**
- Translate N64 virtual addresses (`0x80000000`-based) to host process addresses.

### Module 2: Address registry (`memory/addresses.py`)

**What it does:** The single authoritative table of every named address, struct offset, action-ID constant, and course/star name used anywhere in the system (schema-driven: adding a field for a future detector touches this file plus the detector, nothing else).

**Contents:**
- Symbol addresses for the US ROM: `gMarioStates` (candidate `0x8033B170`, per STROOP/decomp), `gGlobalTimer`, `gLastCompletedCourseNum`, `gLastCompletedStarNum`, `gCurrCourseNum`, `gCurrLevelNum`, save-buffer star-flag locations. Exact values are resolved during implementation from the SM64 decompilation's US symbol map (and cross-checked against STROOP's published config); the registry stores them with the source noted per entry.
- Mario struct offsets: `action` (u32), `actionTimer` (u16).
- The set of star-dance action IDs (exit, no-exit, water variants) from the decomp's action constants. Grand-star and key grabs (Bowser stages) use different cutscene actions and are **out of scope for goal one** — they become their own event type later.
- Course/star name tables: 15 main courses × 7 stars (0-based `star_id`, index 6 = 100-coin star), castle secret-star courses, with human-readable names ("Bob-omb Battlefield" / "Shoot to the Island in the Sky").

**Startup verification:** before serving events, the client sanity-checks known-constant values through these addresses (e.g., RDRAM signature words, plausible ranges for course number and Mario action). On mismatch it refuses to run with a clear error — it never silently emits wrong star IDs. This is the guard for the "Usamune matches US layout" assumption.

### Module 3: Detectors (`detectors/`)

**Contract:**
```python
class Detector(Protocol):
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]: ...
```

Pure functions over snapshots — no I/O, no clock access, fully unit-testable. `GameSnapshot` (in `core/snapshot.py`) is a frozen dataclass holding one poll's coherent reads: `global_timer`, `mario_action`, `mario_action_timer`, `last_completed_course`, `last_completed_star`, `curr_course`, save-flag word for the current course, plus wall-clock capture time.

**Star-grab detector (`detectors/star_grab.py`), goal one:**
1. Edge condition: `prev.mario_action` not in star-dance set AND `curr.mario_action` in star-dance set.
2. On edge: identity = (`curr.last_completed_course`, `curr.last_completed_star`) — the game updates these on every collection, including re-collections.
3. `already_collected` = whether the save-flag bit for that star was already set in `prev` (read pre-commit).
4. `frame = curr.global_timer − curr.mario_action_timer` (back-computed grab frame).
5. Emit one `star_collected` event.

Edge detection (not level detection) is what makes consecutive re-grabs of the same star produce distinct events, and makes the detector immune to stale values after savestate loads.

**Polling margin:** the star dance lasts ~2–3 seconds (~60–90 game frames); at ~60 Hz polling the edge cannot be missed. Detection latency is 1–2 frames; the back-computed `frame` is exact regardless.

### Module 4: Server (`server/app.py` + `main.py`)

- Asyncio poll loop (~60 Hz): build `GameSnapshot` → run all detectors → push events to the broadcaster. Skips ticks cleanly while unattached.
- FastAPI app:
  - `WS /ws/events` — every event broadcast as JSON to all connected clients; slow/dead clients are dropped, never block the loop.
  - `GET /health` — process status, emulator attachment state, last poll time, client count.
  - `GET /state` — latest snapshot (overlay initialization, debugging).
- `main.py` wires registry → memory client → snapshot reader → detectors → broadcaster.

## Event schema

Versioned envelope; all event types share it:

```json
{
  "v": 1,
  "seq": 412,
  "type": "star_collected",
  "frame": 81234,
  "timestamp_utc": "2026-06-10T22:14:03.512Z",
  "payload": {
    "course_id": 1,
    "course_name": "Bob-omb Battlefield",
    "star_id": 2,
    "star_name": "Shoot to the Island in the Sky",
    "already_collected": true
  }
}
```

- `seq`: monotonic per server run; listeners detect gaps.
- `frame`: game-frame stamp (back-computed where applicable).
- `star_id`: 0-based index within the course (6 = 100-coin star). Castle secret stars are identified the same way — the game assigns secret-star areas their own `course_id`s, so (`course_id`, `star_id`) is a complete star identity everywhere.
- Lifecycle events use the same envelope: `emulator_connected`, `emulator_disconnected` (empty payloads), and `game_reset` when `gGlobalTimer` decreases between polls (console reset, savestate to earlier point, ROM reload) — stats consumers use it to segment attempts.

## Error handling

- **PJ64 not running / closed mid-session:** server stays up, emits `emulator_disconnected`, retries attachment every few seconds, emits `emulator_connected` on success. `/health` reflects the state. Listeners never restart.
- **Savestate loads / Usamune level resets:** no false events (edge detection); backward `gGlobalTimer` jump → `game_reset` event.
- **Read failure mid-poll** (emulator closing, region moved): discard the partial snapshot — detectors only ever see coherent snapshots; trigger re-attach.
- **Address verification failure:** hard refusal at startup with an actionable message (wrong ROM / layout mismatch).
- **Logging:** persistent file log (all events + errors), UTC timestamps — observability standard, and a crude stats log from day one.

## Testing

- **Unit (core value):** detectors are pure `(prev, curr) → events`. Synthetic snapshot sequences cover: first grab; immediate re-grab of the same star (duplicate case); grab of an already-collected star (`already_collected: true`); savestate load mid-dance (no event); global-timer backward jump (`game_reset`); no-edge steady states.
- **Unit:** endianness decoding in the memory client against fixture byte buffers (the XOR-3/XOR-2 logic).
- **Boundary mock:** `N64Memory` Protocol is the mock seam; nothing else is mocked.
- **Manual live gate (required before goal one closes):** with Usamune in PJ64 1.6 — verify startup address checks pass; collect a new star, a repeated star, the same star twice in a row; confirm event identity, `frame` stability, and `already_collected` correctness; kill and relaunch the emulator mid-session.

## Future direction (informational, not in scope)

- New detectors (reset, death, level entry, coin count) = one new file each implementing `Detector`, plus registry entries.
- Stats engine and tracker overlay are separate consumers of `WS /ws/events`; the `game_reset` + `frame` + `already_collected` fields exist specifically so they can compute attempt segmentation and last-N timing averages without server changes.

## Decisions log

| Decision | Choice | Why |
|---|---|---|
| Integration method | External memory polling | PJ64 1.6 has no scripting API; STROOP-proven approach |
| Detection strategy | Action-edge + last-completed globals | Fires on re-collections; savestate-safe |
| Primary clock | `gGlobalTimer` (game frames) | Jitter-free, speedrun-native timing; wall clock is metadata |
| Transport | WebSocket (FastAPI) | Browser/OBS consumers; `/health` per standards |
| Stats location | Out of server, future consumer | YAGNI; event schema carries what stats need |
| Language | Python | User's ecosystem; pymem + FastAPI |
