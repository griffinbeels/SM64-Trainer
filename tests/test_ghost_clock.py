"""The ghost clock feed: one tiny message per game snapshot, only while
someone is listening.

The overlay page does not play a video; it FOLLOWS the live attempt's clock.
So the server's whole job here is to hand that clock over as fast as the poll
loop reads it, and two properties are load-bearing:

- **With nobody connected the poll loop pays one set-membership test.** No
  payload is built and the snapshot is not even read -- `Untouchable` below
  RAISES on every attribute, so a `note` that looks at the snapshot with no
  clients goes red. No task exists either, so an idle tracker runs exactly
  what it ran before this feature.
- **A slow client cannot back the poll loop up.** `note` is publish-and-forget
  into ONE slot, so a burst collapses to its NEWEST sample. A clock consumer
  wants the current value; a backlog of stale ones is worse than a gap.
"""
import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.server.ghost_clock import (GhostClockFeed, clock_message,
                                            create_ghost_clock_router)
from sm64_events.server.poller import Poller


def snap(timer: int, igt: int = 0, level: int = 24, area: int = 1) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 9, 18, tzinfo=timezone.utc),
        global_timer=timer, mario_action=0, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
        igt_overall=igt, curr_level=level, curr_area=area)


class Untouchable:
    """A snapshot nobody may read. Reading one costs the poll loop the work
    this feed exists to avoid paying when no overlay is open."""

    def __getattr__(self, name):
        raise AssertionError(f"the idle feed read {name!r} off the snapshot")


class StubClient:
    def __init__(self, fail: bool = False):
        self.messages: list[dict] = []
        self.fail = fail

    async def send_json(self, data) -> None:
        if self.fail:
            raise RuntimeError("client gone")
        self.messages.append(data)


async def _settle(predicate, within_s: float = 2.0) -> bool:
    """Yield until `predicate` holds. A deadline on the loop's own clock,
    never a counted number of sleeps: Windows' 15.6 ms timer makes a
    sleep-counted wait expire before the work it waits on can run."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + within_s
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.001)
    return predicate()


# -- the message ------------------------------------------------------------


def test_the_message_carries_the_clock_and_where_it_is_running():
    """I2's four fields. The follower matches the template on `igt`; `level`
    and `area` are what tells it the player left the template's world, and
    `global_timer` is the one clock that keeps moving while the game is
    paused -- which is how a stall is told apart from a dead feed."""
    assert clock_message(snap(1234, igt=57, level=8, area=2)) == {
        "global_timer": 1234, "igt": 57, "level": 8, "area": 2}


# -- the idle path ----------------------------------------------------------


def test_an_unwatched_feed_never_reads_the_snapshot():
    feed = GhostClockFeed()
    feed.note(Untouchable())          # would raise if it looked at anything
    assert feed.listeners == 0
    assert feed.running is False


def test_an_unwatched_feed_starts_no_task():
    async def run():
        feed = GhostClockFeed()
        for timer in range(200):
            feed.note(snap(timer))
        assert feed.running is False
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(run())


# -- delivery ---------------------------------------------------------------


def test_every_listener_gets_the_sample():
    async def run():
        feed = GhostClockFeed()
        one, two = StubClient(), StubClient()
        await feed.connect(one)
        await feed.connect(two)
        feed.note(snap(900, igt=42, level=8, area=2))
        assert await _settle(lambda: one.messages and two.messages)
        assert one.messages[-1] == {"global_timer": 900, "igt": 42,
                                    "level": 8, "area": 2}
        assert two.messages[-1] == one.messages[-1]
        await feed.disconnect(one)
        await feed.disconnect(two)

    asyncio.run(run())


def test_a_burst_collapses_to_the_newest_sample():
    """The poll loop hands over a sample per game frame and never waits. If
    the pump is behind, the ghost wants where the game IS, not every place it
    has been."""
    async def run():
        feed = GhostClockFeed()
        client = StubClient()
        await feed.connect(client)
        for timer in range(1000, 1010):
            feed.note(snap(timer, igt=timer - 1000))
        assert await _settle(lambda: bool(client.messages))
        # Ten samples handed over without the loop yielding once, so the pump
        # could not have run between them: exactly ONE message, and it is the
        # newest sample rather than the oldest.
        assert feed.noted == 10
        assert len(client.messages) == 1, client.messages
        assert client.messages[0]["global_timer"] == 1009
        await feed.disconnect(client)

    asyncio.run(run())


def test_a_broken_client_is_dropped_and_the_others_keep_their_feed():
    async def run():
        feed = GhostClockFeed()
        good, bad = StubClient(), StubClient(fail=True)
        await feed.connect(good)
        await feed.connect(bad)
        feed.note(snap(1))
        assert await _settle(lambda: feed.listeners == 1)
        feed.note(snap(2))
        assert await _settle(lambda: len(good.messages) == 2)
        assert good.messages[-1]["global_timer"] == 2
        await feed.disconnect(good)

    asyncio.run(run())


def test_the_last_listener_leaving_stops_the_pump():
    async def run():
        feed = GhostClockFeed()
        client = StubClient()
        await feed.connect(client)
        assert feed.running is True
        await feed.disconnect(client)
        assert feed.running is False
        feed.note(Untouchable())      # back to costing nothing
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(run())


# -- the route --------------------------------------------------------------


def _app_with_feed(feed: GhostClockFeed) -> FastAPI:
    app = FastAPI()
    app.include_router(create_ghost_clock_router(feed))

    @app.post("/test/note")
    async def _note(timer: int = 7, igt: int = 3):
        feed.note(snap(timer, igt=igt))
        return {"ok": True}

    return app


def test_the_socket_delivers_a_noted_snapshot():
    feed = GhostClockFeed()
    with TestClient(_app_with_feed(feed)) as client:
        with client.websocket_connect("/ws/ghost-clock") as ws:
            client.post("/test/note?timer=4242&igt=17")
            message = ws.receive_json()
    assert message == {"global_timer": 4242, "igt": 17, "level": 24, "area": 1}


def test_a_closed_socket_leaves_no_listener_behind():
    feed = GhostClockFeed()
    with TestClient(_app_with_feed(feed)) as client:
        with client.websocket_connect("/ws/ghost-clock"):
            pass
        # The disconnect is handled inside the app's loop; give it a turn.
        for _ in range(50):
            if feed.listeners == 0:
                break
            client.get("/openapi.json")
    assert feed.listeners == 0
    assert feed.running is False


# -- the poll side ----------------------------------------------------------


class StubMemory:
    attached = True

    def detach(self):
        pass


class ScriptedReader:
    def __init__(self, snapshots):
        self._snaps = list(snapshots)

    def read(self):
        return self._snaps.pop(0)


class SilentBroadcaster:
    async def publish(self, event):
        return 0


def test_the_poll_loop_hands_every_snapshot_to_the_feed():
    """The one hunk this track owns in `server/poller.py`: the tick's last
    act, after the detectors have had their pair, is to hand the reading to
    whoever is following the clock -- synchronously, and without awaiting."""
    seen = []
    poller = Poller(StubMemory(), [], SilentBroadcaster(),
                    reader=ScriptedReader([snap(10, igt=1), snap(11, igt=2)]),
                    on_snapshot=seen.append)
    asyncio.run(poller.tick())
    asyncio.run(poller.tick())
    assert [(s.global_timer, s.igt_overall) for s in seen] == [(10, 1), (11, 2)]


def test_a_poller_with_no_feed_still_ticks():
    poller = Poller(StubMemory(), [], SilentBroadcaster(),
                    reader=ScriptedReader([snap(10), snap(11)]))
    asyncio.run(poller.tick())
    asyncio.run(poller.tick())
    assert poller.latest.global_timer == 11
