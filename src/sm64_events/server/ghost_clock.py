# src/sm64_events/server/ghost_clock.py
"""THE GHOST CLOCK: the live attempt's timer, pushed to whoever is following it.

The ghost overlay is not a video that plays. It is a function of the live
attempt's clock: each game frame it shows the template picture whose game
timer equals the live timer plus the player's nudge. So the only thing the
server owes it is that clock, as soon as the poll loop has read it.

**Nothing heavy on the poll loop.** `note()` is what `server/poller.py` calls
at the end of every tick, and it is publish-and-forget: with no listener it
does one set-membership test and returns without reading the snapshot at all;
with a listener it drops the snapshot into ONE slot and wakes the pump. The
payload is built, and every `send_json` awaited, on the pump's own task.

**One slot, not a queue.** A clock consumer wants where the game IS. If the
pump falls behind, the samples it skipped are already wrong -- a backlog would
make the ghost late and then make it catch up, which is the one thing a
follower must never do. So a burst collapses to its newest sample and the
overlay sees the current value one wake later.

**No listener, no task.** The pump exists only while at least one socket is
open, so a tracker nobody is practising against runs exactly what it ran
before this module existed.

**The nudge rides this message too.** How far ahead or behind the ghost runs
is a property of the SAME rule -- show the picture whose timer equals the live
timer plus the nudge -- so it belongs on the same wire as the timer. The
session sets it here (`set_nudge`) and every open overlay reads it off its very
next sample, which means a change takes effect within one game frame and
WITHOUT the page being navigated. It used to ride the page's URL instead, and
every press of his timing key therefore re-pointed the window at a new URL: a
full page load, which blinks the overlay off and re-seeks it from scratch. A
held key fired a stream of them. Carrying it here costs nothing when nobody is
listening (the early return happens first) and nothing at all on the poll path.
"""
import asyncio
import logging
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("sm64.ghost")


def clock_message(snapshot, nudge_frames: int = 0) -> dict:
    """The whole wire format. `igt` is Usamune's counter exactly as sampled --
    the same number every template picture is stamped with -- so the follower
    matches raw counter against raw counter with nothing in between.

    That premise is checkable and was checked: both sides read the SAME
    halfword. The poller takes `usamune_overall` through
    `core/snapshot.py` (`read_u16`), and the capture layer copies the same
    address into every picture's stamp through
    `replay/pluginsource.py::table_for`, which `replay/service.py` projects as
    `picture_igt`. Nothing derives, calibrates or reconstructs in between, so
    equality here is exactness rather than an approximation. `level`
    and `area` say which world that counter is running in, which is how the
    page knows the player left the template's course. `global_timer` keeps
    moving while the game is paused, so it is what tells a stalled attempt
    apart from a stalled feed.

    `nudge_frames` is the ghost session's current offset, carried on every
    message rather than only on the ones that change it: a page that missed a
    message, or one that loaded a moment ago, converges on the next sample
    instead of waiting for the player to press the key again.

    The integrator adds `play_mode` here once the pause signal lands.
    """
    return {"global_timer": snapshot.global_timer,
            "igt": snapshot.igt_overall,
            "level": snapshot.curr_level,
            "area": snapshot.curr_area,
            "nudge_frames": int(nudge_frames)}


class GhostClockFeed:
    """Fan the live clock out to every open overlay, without ever making the
    poll loop wait for one."""

    def __init__(self):
        self._clients: set = set()
        self._pending = None
        self._wake: asyncio.Event | None = None
        self._pump: asyncio.Task | None = None
        #: How far ahead or behind the ghost is being run, in game frames. The
        #: ghost session owns the value; this is where it is published from.
        self.nudge_frames = 0
        #: samples handed over by the poll loop, and messages actually sent.
        #: `noted - sent` is how far behind the pump ran, i.e. how many
        #: samples collapsed. Read by tests and worth a /health line later.
        self.noted = 0
        self.sent = 0

    @property
    def listeners(self) -> int:
        return len(self._clients)

    @property
    def running(self) -> bool:
        # `done()` matters: a pump that died still leaves a task object, and a
        # feed that reports healthy while delivering nothing is the one failure
        # nobody would look for.
        return self._pump is not None and not self._pump.done()

    def note(self, snapshot) -> None:
        """THE POLL-LOOP PATH. Called once per game snapshot, never awaited.

        The early return is the whole point: with nothing listening this costs
        one truthiness test on a set and does not touch `snapshot`.
        """
        if not self._clients:
            return
        self._pending = snapshot
        self.noted += 1
        self._wake.set()

    def set_nudge(self, frames: int) -> None:
        """The ghost session's current offset, from now on.

        Deliberately does NOT wake the pump: the next game snapshot carries it,
        which is within one game frame while the poll loop is reading the
        emulator at all -- and while it is not, there is no clock for the
        overlay to follow and nothing for a nudge to move it against.
        """
        self.nudge_frames = int(frames)

    async def connect(self, client) -> None:
        self._clients.add(client)
        if self._pump is None:
            self._wake = asyncio.Event()
            self._pump = asyncio.create_task(self._run())

    async def disconnect(self, client) -> None:
        self._clients.discard(client)
        if self._clients or self._pump is None:
            return
        pump, self._pump = self._pump, None
        self._pending = None
        pump.cancel()
        with suppress(asyncio.CancelledError):
            await pump
        self._wake = None

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            snapshot, self._pending = self._pending, None
            if snapshot is None:
                continue
            try:
                await self._send(clock_message(snapshot, self.nudge_frames))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad sample must not end the feed
                log.exception("ghost clock feed skipped a sample")

    async def _send(self, message: dict) -> None:
        for client in list(self._clients):
            try:
                await client.send_json(message)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a dead socket may raise anything
                # Same contract as the event broadcaster: a failing client is
                # dropped, never retried, and never costs the other overlays
                # their clock.
                self._clients.discard(client)
        self.sent += 1


def create_ghost_clock_router(feed: GhostClockFeed) -> APIRouter:
    """`/ws/ghost-clock`. The socket carries nothing upstream -- a receive is
    only how a disconnect is noticed. Everything the overlay is told, the
    timer and the nudge alike, is the server's own state; the page never
    answers back."""
    router = APIRouter()

    @router.websocket("/ws/ghost-clock")
    async def ws_ghost_clock(websocket: WebSocket):
        await websocket.accept()
        await feed.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await feed.disconnect(websocket)

    return router
