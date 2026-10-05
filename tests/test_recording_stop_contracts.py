"""CPU-inert timed shutdown witnesses: no threads, devices or children start."""
import queue
import threading
from types import SimpleNamespace

import pytest

from sm64_events.replay.audio import ProcessAudioSource
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.ffmpeg_sink import FfmpegAvSink
from sm64_events.replay.pluginsource import DesktopUntilLayerPresents
from sm64_events.replay.video import DwmSurfaceVideoSource, GdiBitBltVideoSource
from test_replay_startup_order import WIN


class Thread:
    ident = 123
    def __init__(self, alive=True):
        self.alive = alive
        self.joins = []
    def join(self, timeout):
        self.joins.append(timeout)
    def is_alive(self):
        return self.alive


@pytest.mark.parametrize("kind,attribute", [
    (DwmSurfaceVideoSource, "_thread"), (DwmSurfaceVideoSource, "_geom_thread"),
    (DwmSurfaceVideoSource, "_deliver_thread"),
    (GdiBitBltVideoSource, "_thread"), (GdiBitBltVideoSource, "_deliver_thread"),
])
def test_video_timeout_retains_exact_thread_for_retry(kind, attribute):
    source = kind(WIN)
    source._stop, source._resync = threading.Event(), threading.Event()
    source._queue = queue.Queue(maxsize=2)
    worker = Thread()
    setattr(source, attribute, worker)
    with pytest.raises(RuntimeError, match="cleanup pending"):
        source.stop()
    assert getattr(source, attribute) is worker
    worker.alive = False
    source.stop()
    assert getattr(source, attribute) is None


@pytest.mark.parametrize("kind", [DwmSurfaceVideoSource, GdiBitBltVideoSource])
def test_video_full_queue_does_not_block_or_discard_delivery_owner(kind):
    source = kind(WIN)
    source._stop, source._resync = threading.Event(), threading.Event()
    source._queue = queue.Queue(maxsize=1)
    source._queue.put_nowait("queued picture")
    source._deliver_thread = Thread()
    with pytest.raises(RuntimeError, match="queue cleanup pending"):
        source.stop()
    assert source._deliver_thread is not None and not source._deliver_thread.joins


@pytest.mark.parametrize("kind", [DwmSurfaceVideoSource, GdiBitBltVideoSource])
def test_dead_video_delivery_thread_can_close_with_full_queue(kind):
    source = kind(WIN)
    source._queue = queue.Queue(maxsize=1)
    source._queue.put_nowait("queued picture")
    source._deliver_thread = Thread(alive=False)
    source.stop()
    assert source._deliver_thread is None


def test_layer_watch_timeout_still_stops_desktop_and_keeps_watch():
    stops = []
    source = DesktopUntilLayerPresents(SimpleNamespace(stop=lambda: stops.append("desktop")))
    worker = source._thread = Thread()
    with pytest.raises(RuntimeError, match="watch cleanup pending"):
        source.stop()
    assert source._thread is worker and stops == ["desktop"]
    worker.alive = False
    source.stop()
    assert source._thread is None


def test_extra_audio_follower_timeout_retains_thread_and_its_tap():
    source = ProcessAudioSource(42)
    worker = source._extra_thread = Thread()
    tap = source._extra_tap = SimpleNamespace(stop=lambda: None)
    with pytest.raises(RuntimeError, match="follower cleanup pending"):
        source.stop()
    assert source._extra_thread is worker and source._extra_tap is tap
    worker.alive = False
    source.stop()
    assert source._extra_thread is None and source._extra_tap is None


def test_extra_audio_failed_native_close_retains_tap_for_retry():
    source = ProcessAudioSource(42)
    allowed = False
    def stop():
        if not allowed:
            raise RuntimeError("fixture tap still owns native audio")
    tap = source._extra_tap = SimpleNamespace(stop=stop)
    source._extra_tapped = 73
    with pytest.raises(RuntimeError, match="still owns native"):
        source.stop()
    assert source._extra_tap is tap and source._extra_tapped == 73
    allowed = True
    source.stop()
    assert source._extra_tap is None and source._extra_tapped is None


@pytest.mark.parametrize("attribute", ["_feeder", "_audio_thread", "_readers"])
def test_ffmpeg_thread_timeout_retains_owner(attribute):
    sink = FfmpegAvSink(ReplayConfig(), lambda _: None)
    worker = Thread()
    setattr(sink, attribute, [worker] if attribute == "_readers" else worker)
    with pytest.raises(RuntimeError, match="cleanup pending"):
        sink.stop()
    assert getattr(sink, attribute) == ([worker] if attribute == "_readers" else worker)
    worker.alive = False
    sink.stop()
    assert getattr(sink, attribute) == ([] if attribute == "_readers" else None)


def test_ffmpeg_timeout_kills_owned_child_before_retrying_blocked_feeder():
    sink = FfmpegAvSink(ReplayConfig(), lambda _: None)
    worker = sink._feeder = Thread()
    calls = []
    def kill():
        calls.append("kill")
        worker.alive = False
    sink._proc = SimpleNamespace(kill=kill, wait=lambda timeout: calls.append(("wait", timeout)),
                                 stdin=None)
    sink.stop()
    assert calls[0] == "kill" and worker.joins == [10, 5]
    assert sink._proc is None and sink._feeder is None


def test_ffmpeg_failed_kill_wait_retains_process_and_feeder():
    sink = FfmpegAvSink(ReplayConfig(), lambda _: None)
    sink._feeder = Thread()
    def fail(timeout):
        raise RuntimeError("fixture child still running")
    proc = sink._proc = SimpleNamespace(kill=lambda: None, wait=fail, stdin=None)
    with pytest.raises(RuntimeError, match="still running"):
        sink.stop()
    assert sink._proc is proc and sink._feeder is not None


def test_ffmpeg_stop_closes_exited_child_jobs_exactly_once(monkeypatch):
    sink = FfmpegAvSink(ReplayConfig(), lambda _: None)
    closed = []
    monkeypatch.setattr("sm64_events.replay.ffmpeg_sink._k32",
                        SimpleNamespace(CloseHandle=lambda job: (closed.append(job), 1)[1]))
    sink._jobs = [(73, SimpleNamespace(poll=lambda: 0)), (74, SimpleNamespace(poll=lambda: 0))]
    sink.stop()
    sink.stop()
    assert closed == [73, 74] and not sink._jobs


@pytest.mark.parametrize("live", [False, True])
def test_ffmpeg_stop_retains_failed_or_live_child_job(monkeypatch, live):
    sink = FfmpegAvSink(ReplayConfig(), lambda _: None)
    can_close, closed = [False], []
    monkeypatch.setattr("sm64_events.replay.ffmpeg_sink._k32",
                        SimpleNamespace(CloseHandle=lambda job: (closed.append(job), can_close[0])[1]))
    proc = SimpleNamespace(poll=lambda: None if live else 0)
    pair = (73, proc)
    sink._jobs = [pair]
    with pytest.raises(RuntimeError, match="job ownership cleanup pending"):
        sink.stop()
    assert sink._jobs == [pair] and closed == ([] if live else [73])
    can_close[0] = True
    proc.poll = lambda: 0
    sink.stop()
    assert not sink._jobs and closed[-1] == 73


def test_ffmpeg_respawn_job_retirement_keeps_failed_pairs_only(monkeypatch):
    sink = FfmpegAvSink(ReplayConfig(), lambda _: None)
    closed = []
    def close(job):
        closed.append(job)
        if job == 74:
            raise OSError("fixture failed handle close")
        return job != 75
    monkeypatch.setattr("sm64_events.replay.ffmpeg_sink._k32", SimpleNamespace(CloseHandle=close))
    exited = SimpleNamespace(poll=lambda: 0)
    live = SimpleNamespace(poll=lambda: None)
    sink._jobs = [(73, exited), (74, exited), (75, exited), (76, live)]
    sink._close_jobs(require_closed=False)
    assert closed == [73, 74, 75]
    assert sink._jobs == [(74, exited), (75, exited), (76, live)]
