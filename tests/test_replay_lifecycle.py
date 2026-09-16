"""Startup order and process replacement use the real attach/source loops."""

import pytest

from sm64_events.replay.window import WindowInfo
from test_pluginsource import layout as layout, rdram_with
from test_replay_recorder import (
    WIN, FakeAudioSource, FakeAvSink, FakeVideoSource, make_recorder, wait_for,
)


class Video(FakeVideoSource):
    def start(self, on_frame, on_stopped):
        super().start(on_frame, on_stopped)
        self.on_stopped = on_stopped

    def stop(self):
        super().stop()
        self.on_stopped()


class Audio(FakeAudioSource):
    def stop(self):
        self.stopped = True


@pytest.mark.parametrize("game_first", [False, True])
@pytest.mark.parametrize("visible_gap", [False, True])
def test_attach_orders_and_repeated_process_replacement(tmp_path, game_first, visible_gap):
    current = [WIN if game_first else None]
    scans = []
    videos, audios, sinks = [], [], []

    def find(_title):
        scans.append(current[0])
        return current[0]

    def video_factory(win):
        video = Video()
        videos.append((win, video))
        return video

    def audio_factory(pid):
        audio = Audio()
        audios.append((pid, audio))
        return audio

    def sink_factory(*args):
        sink = FakeAvSink()
        sinks.append(sink)
        return sink

    rec = make_recorder(tmp_path, None, None, video_sink_factory=sink_factory)
    rec._window_finder = find
    rec._video_factory = video_factory
    rec._audio_factory = audio_factory
    rec.start()
    try:
        assert wait_for(lambda: len(scans) >= 2, timeout=1)
        if not game_first:
            assert videos == audios == []
            current[0] = WIN
        assert wait_for(lambda: rec._recording and len(audios) == 1, timeout=1)
        # A kept attempt is session-owned; replacing PJ64 must not reset scratch.
        kept = rec._cfg.scratch_dir / "kept-attempt.mp4"
        kept.write_bytes(b"session-owned replay")
        for generation in (1, 2):
            old_video, old_audio, old_sink = videos[-1][1], audios[-1][1], sinks[-1]
            if visible_gap:
                current[0] = None
                assert wait_for(lambda: not rec._recording, timeout=1)
            replacement = WindowInfo(hwnd=123 + generation, title=WIN.title,
                                     pid=42 + generation, visible=True)
            current[0] = replacement
            assert wait_for(lambda count=generation + 1: rec._recording and len(audios) == count,
                            timeout=1), "Replacement PJ64 must rebind video AND process audio"
            assert videos[-1][0] == replacement
            assert audios[-1][0] == replacement.pid
            assert old_video.stopped and old_audio.stopped and old_sink.stopped
            assert kept.read_bytes() == b"session-owned replay"
            # A late callback from the old source must not stop the new one.
            scans.clear()
            old_video.on_stopped()
            assert wait_for(lambda: len(scans) >= 3, timeout=1)
            assert rec._recording and len(audios) == generation + 1
        assert len(sinks) == 3
    finally:
        rec.stop()


def _input_memory(layout, game_first):
    from sm64_events.memory.base import MemoryReadError

    class Memory:
        attached = False
        running = game_first
        loaded = game_first
        attempts = 0
        detaches = 0
        backing = rdram_with(layout, frame=10)

        def attach(self):
            self.attempts += 1
            self.attached = self.running
            return self.attached

        def detach(self):
            self.attached = False
            self.detaches += 1

        def __getattr__(self, name):
            def read(*args):
                if not self.running or not self.loaded:
                    raise MemoryReadError("PJ64 absent or ROM not loaded")
                return getattr(self.backing, name)(*args)
            return read

    return Memory()


@pytest.mark.parametrize("game_first", [False, True])
def test_input_poller_reconnects_with_fresh_observations(layout, monkeypatch, game_first):
    import asyncio
    from types import SimpleNamespace
    from sm64_events.inputs.sampler import InputSampler
    from sm64_events.server import poller as module
    from test_poller import EchoDetector, RecordingBroadcaster, snap

    mem = _input_memory(layout, game_first)
    observed = []
    sampler = InputSampler(mem, layout,
        lambda frame, pad, **kw: observed.append((frame, pad, kw["observation"])))
    broadcaster = RecordingBroadcaster()
    reader = SimpleNamespace(read=lambda: snap(mem.read_u32(layout.global_timer)))
    poller = module.Poller(mem, [EchoDetector()], broadcaster, reader=reader,
                           input_sampler=sampler)
    real_sleep = asyncio.sleep
    waits = []

    async def fast_sleep(delay):
        waits.append(delay)
        await real_sleep(0)

    # Accelerate ONLY the poller's waits, keeping its attach/probe/tick logic.
    monkeypatch.setattr(module, "asyncio", SimpleNamespace(sleep=fast_sleep))

    async def until(condition):
        async with asyncio.timeout(2):
            while not condition():
                await real_sleep(0)

    async def scenario():
        task = asyncio.create_task(poller.run())
        try:
            if not game_first:
                await until(lambda: mem.attempts > 0)
                assert not observed
                mem.running = True  # PJ64 window exists, ROM not yet readable.
                await until(lambda: mem.detaches > 0)
                assert poller.latest is None and not observed
                mem.loaded = True
            for generation in range(3):
                await until(lambda: poller.latest is not None
                            and poller.latest.global_timer == 10)
                mem.backing.write_u32(layout.global_timer, 11)
                await until(lambda: poller.latest is not None
                            and poller.latest.global_timer == 11)
                mem.running = False
                await until(lambda count=generation + 1: sum(e.type == "emulator_disconnected"
                            for e in broadcaster.events) == count)
                assert poller.latest is None and not mem.attached
                if generation < 2:
                    mem.backing = rdram_with(layout, frame=10)
                    mem.running = True
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            sampler.flush()

    asyncio.run(scenario())
    _assert_input_restarts(observed, broadcaster, waits, game_first)


def _assert_input_restarts(observed, broadcaster, waits, game_first):
    assert [frame for frame, _, _ in observed] == [10, 11] * 3
    assert len({obs.source_id for _, _, obs in observed}) == 3
    assert [obs.sequence for _, _, obs in observed] == [0, 1] * 3
    assert all(pad.stick_x == 12 and pad.stick_y == -34 and pad.buttons == 0x8000
               for _, pad, _ in observed)
    assert sum(e.type == "emulator_connected" for e in broadcaster.events) == 3
    assert all(e.payload["prev"] == 10 for e in broadcaster.events if e.type == "tick")
    if not game_first:
        assert 2.0 in waits and 5.0 in waits
