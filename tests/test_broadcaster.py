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
