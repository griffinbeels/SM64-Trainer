# src/sm64_events/server/broadcaster.py
"""Fan one event stream out to every connected WebSocket client.

Owns the seq counter. A failing client is dropped, never retried, and never
blocks the poll loop or other clients.
"""
import json
import logging

from sm64_events.core.events import Event, to_wire

log = logging.getLogger("sm64.events")


class Broadcaster:
    def __init__(self):
        self._clients: set = set()
        self._seq = 0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def register(self, ws) -> None:
        self._clients.add(ws)

    def unregister(self, ws) -> None:
        self._clients.discard(ws)

    async def publish(self, event: Event) -> None:
        self._seq += 1
        wire = to_wire(event, self._seq)
        log.info("event %s", json.dumps(wire))
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(wire)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
