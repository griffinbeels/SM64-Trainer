# sm64_tracker — SM64 Event API

Detects game events in **SM64 Usamune v1.93u** running in **Project64 1.6**
(Windows) by reading emulator memory, and broadcasts them as JSON over
WebSocket — star grabs with exact Usamune timing, game resets, emulator
lifecycle. Built for practice-stats tooling, stream overlays, and anything
else that wants a live feed of the game.

> Developing in this repo? **Read `CLAUDE.md` first** — module map, domain
> rules, recipes, and the documentation contract. Deep domain reference:
> `docs/architecture.md`.

## Run

    uv sync
    uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064

Requires Project64 1.6 running Usamune v1.93u on the same machine (8 MB /
expansion-pak memory). The server attaches automatically and reattaches if
the emulator restarts.

- **Viewer**: `http://127.0.0.1:8064/` — live event feed in the browser
- **Events**: `ws://127.0.0.1:8064/ws/events`
- **Health**: `http://127.0.0.1:8064/health`
- **Latest snapshot**: `http://127.0.0.1:8064/state`

## Event schema

Every WebSocket message is a versioned envelope:

```json
{"v": 1, "seq": 412, "type": "star_collected", "frame": 456052,
 "timestamp_utc": "2026-06-10T22:14:03.512000Z",
 "payload": {"course_id": 8, "course_name": "Shifting Sand Land",
             "star_id": 1, "star_name": "Shining Atop the Pyramid",
             "already_collected": true,
             "igt_frames": 595, "igt": "0'19\"83",
             "igt_source": "result", "igt_reconstructed": false}}
```

- `seq`: monotonic per server run (gap = missed events)
- `frame`: game-frame stamp (30 fps), back-computed to the exact touch frame
- `star_id`: 0-based within the course (6 = 100-coin star)
- `igt`: Usamune's overall star time — the number shown on screen, correct
  across multi-area levels; `igt_frames` is the same in raw frames
- `igt_source`: `result` (Usamune's own stored final time — exact),
  `counter` (running overall counter, back-computed), `reconstructed` (see
  Behavior notes)

Other event types, same envelope: `game_reset` (timer moved backward —
segment attempts on this), `emulator_connected`, `emulator_disconnected`.

## Tools (live diagnostics — need PJ64 + ROM running)

| Tool | Purpose |
|---|---|
| `tools/verify_addresses.py` | Address verification gate + live event watch (prints real detector output) |
| `tools/find_timer.py` | Scan RDRAM for ticking counters |
| `tools/hunt_value.py` | Find where a displayed number lives (exact-value search) |
| `tools/watch_timer.py` | Characterize a candidate address across game scenarios |

## Behavior notes

- If a Usamune timer reset races the star touch (reset within ~1 s of the
  grab, e.g. reset-spamming between attempts), the event reports the
  *prior attempt's* time extrapolated to the exact touch frame and sets
  `igt_reconstructed: true`. In that scenario the API is deliberately more
  accurate than Usamune's own frozen display.

## Known limitations

- Loading a savestate that was *saved during* a star dance re-emits that
  star's `star_collected` event (the load looks like a fresh grab edge);
  its `already_collected` flag may be wrong. Savestates saved outside a
  dance are safe. Usamune section states are typically safe.
- Bowser-stage key grabs use the same star-dance actions and may emit a
  `star_collected` with `course_id` 16/17 until a dedicated key event
  type exists.
