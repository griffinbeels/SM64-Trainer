# sm64_tracker — SM64 Event API

Detects star grabs in SM64 Usamune v1.93u running in Project64 1.6 and
broadcasts them as WebSocket events. See `docs/superpowers/specs/` for design.

## Run

    uv sync
    uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064

Requires Project64 1.6 running Usamune v1.93u on the same machine.

- Events: `ws://127.0.0.1:8064/ws/events`
- Health: `http://127.0.0.1:8064/health`
- Latest snapshot: `http://127.0.0.1:8064/state`

## Behavior notes

- If a Usamune timer reset races the star touch (reset within ~1 s of the
  grab, e.g. reset-spamming between attempts), the event reports the
  *prior attempt's* time extrapolated to the exact touch frame and sets
  `igt_reconstructed: true`. Normal grabs carry `igt_reconstructed: false`.

## Known limitations

- Loading a savestate that was *saved during* a star dance re-emits that
  star's `star_collected` event (the load looks like a fresh grab edge);
  its `already_collected` flag may be wrong. Savestates saved outside a
  dance are safe. Usamune section states are typically safe.
- Bowser-stage key grabs use the same star-dance actions and may emit a
  `star_collected` with `course_id` 16/17 until a dedicated key event
  type exists.
