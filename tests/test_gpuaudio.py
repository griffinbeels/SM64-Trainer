"""PCM handoff pressure is explicit; capture-clock bytes and final tail survive."""

from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from sm64_events.replay.gpuaudio import PcmHandoff, GpuAudio
from sm64_events.replay.audiopacing import AudioBacklog


def queue(**overrides):
    limits = dict(max_bytes=48000 * 4, max_blocks=16, max_age=1)
    limits.update(overrides)
    return PcmHandoff(**limits)


@pytest.mark.parametrize("cause", ["bytes", "count", "contention", "age"])
def test_handoff_never_waits_or_discards_old_pcm_silently(cause):
    q = queue(max_bytes=16, max_blocks=2)
    data = b"\0" * 8
    assert q.submit(data, 1000, now=0)
    if cause == "bytes":
        assert not q.submit(b"\0" * 12, 1000, now=0)
    elif cause == "count":
        assert q.submit(b"\0" * 4, 1000, now=0)
        assert not q.submit(b"\0" * 4, 1000, now=0)
    elif cause == "contention":
        q._lock.acquire()
        try:
            assert not q.submit(data, 1000, now=0)
        finally:
            q._lock.release()
    else:
        with pytest.raises(AudioBacklog, match="age"):
            q.take(1.001)
    assert q.fault
    assert (
        q._queue[0].data is data
    )  # failure never pretends the earlier PCM was delivered
    with pytest.raises(AudioBacklog):
        q.take(0.1)


def test_handoff_charges_until_consumed_and_close_requires_drain():
    q = queue()
    data = b"abcd" * 200
    assert q.submit(data, 1000, now=1)
    assert q.bytes == len(data)
    q.close_input()
    assert not q.submit(data, 1000, now=1) and not q.drained()
    taken = q.take(1.001)
    assert taken.data is data and taken.ends_at == 1000 and taken.arrived == 1
    assert q.bytes == 0 and q.drained()


def audio(q):
    chunks, now = [], [0.0]
    media = SimpleNamespace(
        mux=SimpleNamespace(rate=48000),
        run=SimpleNamespace(origin_ts=1000),
        audio=lambda data, pts, **kw: chunks.append((data, pts, kw["now"])),
    )
    worker = GpuAudio(
        media, q, monotonic=lambda: now[0], wall_time=lambda: 1000 + now[0]
    )
    return worker, chunks, now


def test_real_arrival_clock_survives_late_worker_and_final_tail_waits_for_source():
    q = queue()
    worker, chunks, now = audio(q)
    worker.drain()  # first pacing origin
    data = b"abcd" * 480
    assert q.submit(data, 1000.010, now=0.010)
    now[0] = 0.050  # worker delay must not become this picture/audio's clock
    worker.drain()
    assert chunks == [(data, 1000000000, 0.05)] and chunks[0][0] is data
    assert not worker.finish(1000.020)
    assert len(chunks) == 1
    q.close_input()
    assert worker.finish(1000.020)
    assert chunks[1][:2] == (b"\0" * 480 * 4, 1000010000)
    with pytest.raises(RuntimeError, match="finished"):
        worker.drain()


def test_final_audio_never_adds_silence_after_real_pcm_covers_end():
    q = queue()
    worker, chunks, now = audio(q)
    q.submit(b"abcd" * 960, 1000.020, now=0)
    q.close_input()
    assert worker.finish(1000.010)
    assert len(chunks) == 1  # media's known final cut owns trimming


def test_final_long_gap_is_refused_before_allocating_and_worker_owner_is_pinned():
    q = queue()
    worker, chunks, now = audio(q)
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(RuntimeError, match="worker"):
            pool.submit(worker.drain).result()
    q.close_input()
    with pytest.raises(AudioBacklog, match="final silent tail"):
        worker.finish(10000)
    assert chunks == [] and not worker.finished
