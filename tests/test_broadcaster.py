# tests/test_broadcaster.py
import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster


def make_event() -> Event:
    return Event(type="star_collected", frame=1,
                 timestamp_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
                 payload={})


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class DeadWS:
    async def send_json(self, data):
        raise RuntimeError("client gone")


def test_publish_sends_to_all_clients_with_increasing_seq():
    b = Broadcaster()
    ws1, ws2 = FakeWS(), FakeWS()
    b.register(ws1)
    b.register(ws2)
    asyncio.run(b.publish(make_event()))
    asyncio.run(b.publish(make_event()))
    assert [m["seq"] for m in ws1.sent] == [1, 2]
    assert [m["seq"] for m in ws2.sent] == [1, 2]
    assert ws1.sent[0]["type"] == "star_collected"


def test_dead_client_is_dropped_without_blocking_others():
    b = Broadcaster()
    dead, alive = DeadWS(), FakeWS()
    b.register(dead)
    b.register(alive)
    asyncio.run(b.publish(make_event()))
    assert len(alive.sent) == 1
    assert b.client_count == 1


def test_unregister():
    b = Broadcaster()
    ws = FakeWS()
    b.register(ws)
    b.unregister(ws)
    asyncio.run(b.publish(make_event()))
    assert ws.sent == []
    assert b.client_count == 0


def test_ghost_changed_is_logged_as_one_short_line(caplog):
    """The full session state is ~2.9 KB and was 27% of the log's bytes; the
    log keeps what a reader asks of it, and the client still gets it all."""
    import json
    import logging
    payload = {"enabled": True,
               "target": {"kind": "star", "entity_key": "2-2", "strat": "Standard"},
               "source": {"available": True, "reason": None, "reason_text": "x" * 2000},
               "selection": {"ghosts": {"pb": True, "attempts": []},
                             "candidates": [{"attempt_id": n} for n in range(5)]},
               "window": {"running": True, "visible": True}}
    ws = FakeWS()
    b = Broadcaster()
    b.register(ws)
    with caplog.at_level(logging.INFO, logger="sm64.events"):
        asyncio.run(b.publish(Event(type="ghost_changed", frame=0,
                                    timestamp_utc=datetime(2026, 9, 23, tzinfo=timezone.utc),
                                    payload=payload)))
        asyncio.run(b.publish(make_event()))
    ghost_line, star_line = [r.getMessage() for r in caplog.records]
    logged = json.loads(ghost_line.removeprefix("event "))
    assert logged == {"seq": 1, "type": "ghost_changed", "target": "2-2", "strat": "Standard",
                      "enabled": True, "available": True, "reason": None,
                      "ghosts": {"pb": True, "attempts": []}, "candidates": 5,
                      "window": {"running": True, "visible": True}}
    assert len(ghost_line) < 400
    assert ws.sent[0]["payload"] == payload        # the client still gets everything
    assert json.loads(star_line.removeprefix("event "))["type"] == "star_collected"
