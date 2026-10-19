# src/sm64_events/server/broadcaster.py
"""Fan one event stream out to every connected WebSocket client.

Owns the seq counter. A failing client is dropped, never retried, and never
blocks the poll loop or other clients.
"""
import json
import logging

from sm64_events.core.events import Event, to_wire

log = logging.getLogger("sm64.events")


def _log_line(wire: dict) -> str:
    """What the log keeps of one published event: the whole wire form, except
    for `ghost_changed`. That one restates the ghost session's full state --
    ~2.9 KB, and 27% of the log's bytes (2026-09-23) -- every time any of it
    moves, and the browser already has every field. The line keeps what a
    reader of the log asks of it: which target, on or off, what
    plays and why not."""
    if wire.get("type") != "ghost_changed":
        return json.dumps(wire)
    body = wire.get("payload") or {}
    target = body.get("target") or {}
    source = body.get("source") or {}
    selection = body.get("selection") or {}
    summary = {
        "seq": wire.get("seq"), "type": "ghost_changed",
        "target": target.get("entity_key"), "strat": target.get("strat"),
        "enabled": body.get("enabled"),
        "available": source.get("available"), "reason": source.get("reason"),
        "ghosts": selection.get("ghosts"),
        "candidates": len(selection.get("candidates") or []),
        "window": body.get("window"),
    }
    return json.dumps(summary)


class Broadcaster:
    def __init__(self):
        self._clients: set = set()
        self._seq = 0
        self._observers: set = set()

    def observe(self, callback):
        """Local observers do bounded work, never network I/O on this path."""
        self._observers.add(callback)
        return lambda: self._observers.discard(callback)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def register(self, ws) -> None:
        self._clients.add(ws)

    def unregister(self, ws) -> None:
        self._clients.discard(ws)

    async def publish(self, event: Event) -> int:
        for callback in tuple(self._observers):
            try:
                callback(event)
            except Exception:  # noqa: BLE001 -- an observer cannot stop live tracking
                log.warning("A local event observer could not update; tracking continues.")
        self._seq += 1
        wire = to_wire(event, self._seq)
        log.info("event %s", _log_line(wire))
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(wire)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
        return self._seq
