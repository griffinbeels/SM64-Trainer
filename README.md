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
