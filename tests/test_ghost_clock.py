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
from sm64_events.ghost.session import DEFAULT_TINT
from sm64_events.memory.addresses import STAR_GRAB_ACTIONS
from sm64_events.server.ghost_clock import (GhostClockFeed, clock_message,
                                            create_ghost_clock_router)
from sm64_events.server.poller import Poller


def snap(timer: int, igt: int = 0, level: int = 24, area: int = 1,
         action: int = 0) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 9, 18, tzinfo=timezone.utc),
        global_timer=timer, mario_action=action, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
        igt_overall=igt, curr_level=level, curr_area=area)


def walking_action() -> int:
    """An action that is NOT a grab, taken from the registry rather than typed:
    a hand-picked id agrees with itself whichever way the code spells things."""
    for candidate in range(0x1000):
        if candidate not in STAR_GRAB_ACTIONS:
            return candidate
    raise AssertionError("every action id is a star grab?")


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
    message = clock_message(snap(1234, igt=57, level=8, area=2))
    # The FIELDS this test owns, not the whole dict: the message is a shape
    # other work extends (the integrator's `play_mode` is already promised),
    # and whole-dict equality here claims ownership of everything anyone adds.
    assert message["global_timer"] == 1234
    assert message["igt"] == 57
    assert message["level"] == 8
    assert message["area"] == 2
    assert message["nudge_frames"] == 0


def test_the_message_carries_the_nudge_beside_the_clock():
    """The nudge is part of the SAME rule -- the picture whose timer equals the
    live timer plus the nudge -- so it rides the same message. Its own value
    travelling separately is what would put the overlay a moment out of step
    with the number the player just asked for."""
    assert clock_message(snap(1234, igt=57), -12)["nudge_frames"] == -12


def test_the_message_says_when_he_has_just_touched_a_star():
    """HIS OWN GRAB TAKES THE GHOST DOWN, and this is the signal it rides.

    His words: *"The ghost should close when I grab the star (sometimes I might
    be faster than the ghost!)."* The page already reads this socket every game
    frame, so the answer is a boolean here rather than a second feed -- and it
    is Mario's ACTION, already in the snapshot, rather than the journal's
    `star_collected`, which is published after the fact.

    Every grab action is walked out of `memory/addresses.py::STAR_GRAB_ACTIONS`
    rather than typed in: the registry is the project's one answer to "this
    action means a star was just grabbed", and a copy here would agree with
    itself while disagreeing with the detector.
    """
    assert STAR_GRAB_ACTIONS, "the grab registry is empty -- did the scan break?"
    for action in sorted(STAR_GRAB_ACTIONS):
        assert clock_message(snap(1234, action=action))["star_grab"] is True, (
            f"action {action:#x} is in the registry and did not read as a grab")
    assert clock_message(snap(1234, action=walking_action()))["star_grab"] is False


def test_the_message_carries_how_the_ghost_is_coloured():
    """The colour treatment travels the way the nudge does, and for the same
    reason: it is a preference the RUNNING page can take, so a change must
    never re-point the window at a URL (which is a page load)."""
    message = clock_message(snap(1234), 0, {"enabled": True, "hue_degrees": 200})
    assert message["tint"] == {"enabled": True, "hue_degrees": 200}
    off = clock_message(snap(1234), 0, {"enabled": False, "hue_degrees": 120})
    assert off["tint"] == {"enabled": False, "hue_degrees": 120}


def test_a_message_nobody_has_stated_a_colour_for_carries_the_shipped_one():
    """The page ships NO default of its own -- a second copy of a preference is
    a second opinion -- so the message always carries one. Read from the
    session's own constant rather than restated, which is the whole point."""
    assert clock_message(snap(1234))["tint"] == {
        "enabled": bool(DEFAULT_TINT["enabled"]),
        "hue_degrees": int(DEFAULT_TINT["hue_degrees"])}


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
        assert {key: one.messages[-1][key]
                for key in ("global_timer", "igt", "level", "area")} == {
            "global_timer": 900, "igt": 42, "level": 8, "area": 2}
        assert two.messages[-1] == one.messages[-1]
        await feed.disconnect(one)
        await feed.disconnect(two)

    asyncio.run(run())


def test_a_new_nudge_reaches_every_overlay_on_the_next_sample():
    """His timing key writes the nudge into the session, the session hands it
    here, and the very next game snapshot carries it out. One game frame, and
    no page load anywhere -- which is the whole reason it travels this way
    instead of in the overlay page's URL."""
    async def run():
        feed = GhostClockFeed()
        client = StubClient()
        await feed.connect(client)
        feed.note(snap(900, igt=42))
        assert await _settle(lambda: bool(client.messages))
        assert client.messages[-1]["nudge_frames"] == 0

        feed.set_nudge(-12)
        feed.note(snap(901, igt=43))
        assert await _settle(lambda: len(client.messages) == 2)
        assert {key: client.messages[-1][key]
                for key in ("global_timer", "igt", "level", "area",
                            "nudge_frames")} == {
            "global_timer": 901, "igt": 43, "level": 24, "area": 1,
            "nudge_frames": -12}
        # It rides EVERY message, not only the one that changed it: an overlay
        # that just loaded, or that missed a sample, converges on its next one.
        feed.note(snap(902, igt=44))
        assert await _settle(lambda: len(client.messages) == 3)
        assert client.messages[-1]["nudge_frames"] == -12
        await feed.disconnect(client)

    asyncio.run(run())


def test_setting_the_nudge_costs_an_unwatched_feed_nothing():
    """The early return still comes first: a nudge with no overlay open must
    not start a pump or make the poll loop read a snapshot."""
    feed = GhostClockFeed()
    feed.set_nudge(30)
    feed.set_tint({"enabled": True, "hue_degrees": 90})
    feed.note(Untouchable())
    assert feed.running is False and feed.noted == 0


def test_a_new_colour_reaches_every_overlay_on_the_next_sample():
    """Same wire, same one-game-frame answer, and the same reason: he tunes
    this by dragging, and a page load per drag event is what the nudge already
    taught this feed not to do."""
    async def run():
        feed = GhostClockFeed()
        client = StubClient()
        await feed.connect(client)
        feed.set_tint({"enabled": True, "hue_degrees": 200})
        feed.note(snap(900))
        assert await _settle(lambda: bool(client.messages))
        assert client.messages[-1]["tint"] == {"enabled": True,
                                               "hue_degrees": 200}
        feed.set_tint({"enabled": False, "hue_degrees": 200})
        feed.note(snap(901))
        assert await _settle(lambda: len(client.messages) == 2)
        assert client.messages[-1]["tint"] == {"enabled": False,
                                               "hue_degrees": 200}
        await feed.disconnect(client)

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
    feed.set_nudge(5)
    with TestClient(_app_with_feed(feed)) as client:
        with client.websocket_connect("/ws/ghost-clock") as ws:
            client.post("/test/note?timer=4242&igt=17")
            message = ws.receive_json()
    assert {key: message[key]
            for key in ("global_timer", "igt", "level", "area",
                        "nudge_frames")} == {
        "global_timer": 4242, "igt": 17, "level": 24, "area": 1,
        "nudge_frames": 5}


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
