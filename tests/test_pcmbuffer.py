"""PCM budgets and source samples at the real mux's admission boundary."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sm64_events.replay.media import MediaRun
from sm64_events.replay.pcmbuffer import PcmBuffer, PcmBacklog


def buffer(**options):
    limits = dict(rate=48000, max_bytes=19200, max_blocks=8, max_age=1, lead_ticks=9000)
    limits.update(options)
    return PcmBuffer(MediaRun("pcm", 1789000000.123456), **limits)


def test_normal_blocks_share_bytes_and_keep_exact_source_clock():
    q = buffer()
    data = bytes(range(256)) * 15  # 960 samples, 20ms
    q.append(data, q.origin_us, now=0)
    out = []
    assert q.drain(0, lambda *args: out.append(args)) == 960
    assert out[0][0] is data and out[0][1] == q.origin_us
    assert q.bytes == 0


def test_audio_cannot_run_past_video_plus_explicit_lead():
    q = buffer(lead_ticks=0)
    data = bytes(3840)
    q.append(data, q.origin_us, now=0)
    out = []
    assert q.drain(1799, lambda *a: out.append(a)) == 0
    assert q.bytes == 3840 and not out
    assert q.drain(1800, lambda *a: out.append(a)) == 960
    q.append(data, q.origin_us + 20_000, now=0.02)
    assert q.drain(1800, lambda *a: out.append(a)) == 0
    # Ahead of the committed video it WAITS (a true pause): not stale, still
    # bounded by capacity. Only video reaching it lets it out.
    q.check_age(1.021)
    assert q.bytes == 3840
    assert q.drain(3600, lambda *a: out.append(a)) == 960


def test_pcm_with_no_committed_video_yet_is_stale_after_max_age():
    q = buffer(lead_ticks=0)
    q.append(bytes(3840), q.origin_us, now=0)
    with pytest.raises(PcmBacklog, match="age"):
        q.check_age(1.021)


def test_final_cut_preserves_every_sample_before_end_without_lead():
    q = buffer()
    data = b"".join(i.to_bytes(4, "little") for i in range(2000))
    q.append(data, q.origin_us - 1000, now=0)
    q.append(bytes(400), q.origin_us + 1_000_000, now=0)
    out = []
    # end 1/90000s: 48 preroll samples plus first in-run sample at time0.
    assert q.drain(1, lambda *a: out.append(a), final=True) == 49
    assert out == [(data[: 49 * 4], q.origin_us - 1000)]
    assert q.tail_samples == 2051 and q.closed and q.bytes == 0
    with pytest.raises(PcmBacklog, match="sealed"):
        q.append(bytes(4), q.origin_us, now=0)


def test_byte_count_and_age_budgets_refuse_without_discarding_accepted_pcm():
    for limits in (dict(max_bytes=8), dict(max_blocks=2)):
        q = buffer(**limits)
        q.append(bytes(4), q.origin_us, now=0)
        q.append(bytes(4), q.origin_us + 21, now=0.1)
        with pytest.raises(PcmBacklog, match="capacity"):
            q.append(bytes(4), q.origin_us + 42, now=0.2)
        assert q.bytes == 8 and len(q.blocks) == 2
        q.abort()
        assert q.bytes == 0 and not q.blocks


@pytest.mark.parametrize(
    "data,stamp", [(b"", 1), (bytes(3), 1), (bytearray(4), 1), (bytes(4), 1.5)]
)
def test_malformed_pcm_refused_before_queue(data, stamp):
    q = buffer()
    with pytest.raises(ValueError):
        q.append(data, stamp, now=0)
    assert q.bytes == 0 and not q.blocks


def test_failed_writer_keeps_original_block_for_explicit_run_abort():
    q = buffer()
    q.append(bytes(3840), q.origin_us, now=0)

    def fail(*_):
        raise OSError("mux failed")

    with pytest.raises(OSError, match="mux failed"):
        q.drain(0, fail)
    assert q.bytes == 3840 and len(q.blocks) == 1
    q.abort()


def test_foreign_worker_and_backward_clock_cannot_mutate_buffer():
    q = buffer()
    q.append(bytes(4), q.origin_us, now=0)
    with ThreadPoolExecutor(1) as thread:
        for call in (
            lambda: q.append(bytes(4), q.origin_us, now=0),
            lambda: q.drain(0, lambda *_: None),
            q.abort,
            lambda: q.check_age(1),
        ):
            with pytest.raises(RuntimeError, match="media worker"):
                thread.submit(call).result()
    with pytest.raises(PcmBacklog, match="backward"):
        q.append(bytes(4), q.origin_us - 1, now=0)
    assert q.bytes == 4
