"""Failure/reconnect proof through the real loop, never a live emulator."""
import asyncio
from dataclasses import replace

import pytest

from sm64_events.detectors.level import LevelChangeDetector
from sm64_events.memory.base import MemoryReadError
from sm64_events.server.poller import Poller
from sm64_events.server.pollsupervisor import PollSupervisor
from test_poller import RecordingBroadcaster, snap


class Game:
    def __init__(self, available=True):
        self.available = available
        self.attached = False
        self.frame = 100
        self.level = 9
        self.fault = None
        self.attaches = 0

    def attach(self):
        self.attaches += 1
        self.attached = self.available
        return self.attached

    def detach(self):
        self.attached = False

    def read(self):
        if self.fault:
            fault, self.fault = self.fault, None
            raise fault
        if not self.available:
            raise MemoryReadError("process exited")
        self.frame += 1
        return replace(snap(self.frame), curr_level=self.level)


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():  # noqa: ASYNC110 -- observe real loop state with a deadline
            await asyncio.sleep(0.001)


def configured(game, broadcaster, **kwargs):
    poller = Poller(game, [LevelChangeDetector()], broadcaster, reader=game,
                    detector_factory=lambda: [LevelChangeDetector()], **kwargs)
    poller.interval = poller.ATTACH_RETRY_S = poller.LAYOUT_RETRY_S = 0.001
    supervisor = PollSupervisor(poller)
    supervisor.RETRY_INITIAL_S = 0.005
    supervisor.RETRY_MAX_S = 0.02
    return poller, supervisor


@pytest.mark.parametrize("initially_open", [False, True])
def test_startup_order_and_repeated_process_relaunch(initially_open):
    async def exercise():
        game, broadcaster = Game(initially_open), RecordingBroadcaster()
        gaps = []

        async def gap(reason):
            gaps.append(reason)

        poller, supervisor = configured(game, broadcaster, on_gap=gap)
        task = asyncio.create_task(supervisor.run())
        try:
            if not initially_open:
                await until(lambda: game.attaches >= 2)
                assert poller.latest is None
                game.available = True
            await until(lambda: any(e.type == "level_changed" for e in broadcaster.events))
            for level in [24, 12, 9]:
                game.available = False
                await until(lambda: not game.attached and poller.latest is None)
                game.frame, game.level, game.available = 100, level, True
                await until(lambda level=level: poller.latest is not None and poller.latest.curr_level == level)
                await until(lambda level=level: any(e.type == "level_changed" and e.payload["to"] == level
                                        for e in broadcaster.events[-5:]))
            assert len(gaps) == 3
            assert supervisor.failures == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(exercise())


def test_unexpected_read_failure_rebuilds_detectors_and_course():
    async def exercise():
        game, broadcaster = Game(), RecordingBroadcaster()
        poller, supervisor = configured(game, broadcaster)
        original = poller.detectors[0]
        task = asyncio.create_task(supervisor.run())
        try:
            await until(lambda: poller.latest is not None)
            game.fault = OSError("transient reader failure")
            await until(lambda: supervisor.failures == 1)
            assert supervisor.health()["state"] == "recovering"
            assert poller.latest is None
            game.level = 24
            await until(lambda: supervisor.health()["state"] == "running")
            await until(lambda: any(e.type == "level_changed" and e.payload["to"] == 24
                                    for e in broadcaster.events))
            assert poller.detectors[0] is not original
            assert supervisor.restarts == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(exercise())


def test_partial_publish_is_not_replayed_and_shutdown_cancels_backoff():
    async def exercise():
        game = Game()

        class InterruptedSink(RecordingBroadcaster):
            async def publish(self, event):
                await super().publish(event)
                if event.type == "level_changed":
                    raise RuntimeError("failed after broadcasting event")

        sink = InterruptedSink()
        poller, supervisor = configured(game, sink)
        task = asyncio.create_task(supervisor.run())
        await until(lambda: supervisor.failures >= 2)
        levels = [e for e in sink.events if e.type == "level_changed"]
        assert len({e.frame for e in levels}) == len(levels)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        count = len(sink.events)
        await asyncio.sleep(0.03)
        assert len(sink.events) == count
        assert supervisor.child.done()
    asyncio.run(exercise())


def test_disconnect_discards_real_pending_star_before_reattach():
    from sm64_events.detectors.star_grab import StarGrabDetector
    from sm64_events.memory import addresses as A
    from test_star_grab import snap as star_snap
    from test_poller import ScriptedReader, StubMemory

    before = star_snap(global_timer=1000, igt_overall=230)
    dance = star_snap(global_timer=1001, mario_action=A.ACT_STAR_DANCE_EXIT,
                      mario_action_timer=0, igt_overall=231, igt_result=0)
    resumed = star_snap(global_timer=5000, igt_overall=100, curr_level=24)
    sink = RecordingBroadcaster()
    detector = StarGrabDetector()
    poller = Poller(StubMemory(), [detector], sink,
                    detector_factory=lambda: [StarGrabDetector()],
                    reader=ScriptedReader([before, dance, MemoryReadError("closed"),
                                           resumed, replace(resumed, global_timer=5001)]))

    async def exercise():
        await poller.tick()
        await poller.tick()
        assert detector._pending is not None
        await poller.tick()
        assert poller.detectors[0] is not detector
        await poller.tick()
        await poller.tick()
        assert [e.type for e in sink.events] == ["emulator_disconnected"]
    asyncio.run(exercise())
