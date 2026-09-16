"""Real compressed output, selector, PCM and retained source identity together."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import av
import pytest

from sm64_events.replay.gpumedia import GpuMedia
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.media import MediaRun
from sm64_events.replay.feedmap import feed_map
from sm64_events.replay.packetmux import PacketFragmentMux
from sm64_events.replay.pixels import SampledPicture
from test_packetmux import (
    FIXTURE,
    RATE,
    Output,
    fixture_packets,
    pcm,
    decode_video,
    decode_audio,
    verify_video,
)


def sample(value):
    return SampledPicture(16, 16, bytes([value, 17, 93, 255]) * 4, 8)


def stamp(frame):
    return SimpleNamespace(
        frame=frame, extras=lambda: {"exact": True, "pad": [frame, 0, 1]}
    )


class FakeMux:
    rate = RATE

    def __init__(self):
        self.video = []
        self.audio = []
        self.closed = self.failed = False

    def write_video(self, picture):
        self.video.append(picture)

    def write_pcm(self, data, first_us):
        self.audio.append((data, first_us))

    def close(self):
        self.closed = True

    def abort(self):
        self.failed = True


def media(mux=None, ledger=None, **limits):
    args = dict(
        source_namespace="capture-epoch-1",
        encoder_duration=3000,
        packet_limit=8192,
        pending_count=8,
        pending_bytes=128 * 1024,
        max_age=2,
        pcm_bytes=RATE * 4,
        pcm_blocks=64,
        lead_ticks=9000,
    )
    args.update(limits)
    return GpuMedia(
        MediaRun("gpu-media", 1000), ledger or PictureLedger(), mux or FakeMux(), **args
    )


def complete(q, decision, data=b"packet", key=True):
    request = decision.request
    q.complete(
        request.ticket,
        data,
        pts=request.pts,
        duration=request.encoder_duration,
        keyframe=key,
    )


def test_folded_sample_retains_original_encoded_source_and_new_run_gets_picture():
    ledger = PictureLedger()
    q = media(ledger=ledger)
    a = q.offer(sample(1), stamp(10), occurrence=1, capture_ts=1000, now=0)
    b = q.offer(sample(2), stamp(10), occurrence=2, capture_ts=1000.010, now=0.01)
    c = q.offer(sample(2), stamp(11), occurrence=3, capture_ts=1000.033, now=0.033)
    assert a.kind == "selected" and a.force_idr
    assert b.kind == c.kind == "coalesced"
    assert b.retained == c.retained == a.ticket
    assert b.retained_occurrence == 1 and q.order.pending_count == 1
    complete(q, a)
    q.frontier(1000.2)
    repeat = q.heartbeat(1000.2, now=0.2)
    assert repeat.request.repeat and repeat.request.source.occurrence == 1
    complete(q, repeat)
    assert q.seal(1000.2 + 1 / 90000) is False  # beyond proven frontier
    q.frontier(1000.3)
    assert q.seal(1000.3) and q.finish(audio_drained=True)
    feeds = ledger.feeds_between(0, 2000)
    values, repeats, stats = feed_map(
        [0, 18000],
        q.run.id,
        ledger.rows_between(0, 2000),
        feeds,
        lambda row: row["frame"],
    )
    assert values == [10, 10] and repeats == [False, True] and stats["matched"] == 2
    q2 = media(ledger=ledger, source_namespace="capture-epoch-2")
    # Even within the old fold interval and with identical bytes/counter.
    assert (
        q2.offer(sample(2), stamp(10), occurrence=1, capture_ts=1000.001, now=0).kind
        == "selected"
    )


def test_unfinished_packet_and_missing_audio_stay_bounded():
    q = media(pending_count=2)
    q.offer(sample(1), stamp(1), occurrence=1, capture_ts=1000, now=0)
    q.offer(sample(2), stamp(2), occurrence=2, capture_ts=1000.033, now=0.03)
    with pytest.raises(RuntimeError, match="capacity"):
        q.offer(sample(3), stamp(3), occurrence=3, capture_ts=1000.066, now=0.06)
    assert q.closed and q.mux.failed and not q.mux.video
    assert q.order.pending_bytes <= q.order.max_bytes


def test_packet_mismatch_and_audio_overflow_are_terminal():
    q = media()
    d = q.offer(sample(1), stamp(1), occurrence=1, capture_ts=1000, now=0)
    with pytest.raises(RuntimeError, match="PTS mismatch"):
        q.complete(d.ticket, b"packet", pts=1, duration=3000, keyframe=True)
    assert q.closed and q.mux.failed and not q.ledger.feeds_between(0, 2000)
    q = media(pcm_bytes=8)
    with pytest.raises(RuntimeError, match="capacity"):
        q.audio(bytes(12), 1000000000, now=0)
    assert q.closed and q.pcm.bytes == 0


def test_foreign_worker_cannot_abort_or_select():
    q = media()
    with ThreadPoolExecutor(1) as t:
        for fn in (
            lambda: q.abort("bad"),
            lambda: q.audio(bytes(4), 1000000000, now=0),
            lambda: q.offer(sample(1), stamp(1), occurrence=1, capture_ts=1000, now=0),
        ):
            with pytest.raises(RuntimeError, match="worker"):
                t.submit(fn).result()
    assert not q.closed and not q.order.pending_count


def test_real_packets_pcm_and_exact_ids_reach_progressive_archive(tmp_path):
    packets = fixture_packets()
    run = MediaRun("gpu-media", 1000)
    out = Output(tmp_path, run)
    ledger = PictureLedger()
    ledger.open_archive(tmp_path / "identities.db")
    with av.open(str(FIXTURE / "witness.h264")) as source:
        mux = PacketFragmentMux(
            out,
            source.streams.video[0],
            run,
            audio_rate=RATE,
            audio_bitrate=160000,
            packet_limit=8192,
            pcm_limit=RATE * 4,
        )
        q = media(mux, ledger)
        decisions = []
        cursor = -2400
        # Each native picture has a distinct frame and sample, even equal UTC.
        raw_ticks = [0, 3001, 3001, 179999, 180000, 720000, 720000, 720000, 720000]
        for index, packet in enumerate(packets):
            d = q.offer(
                sample(index),
                stamp(index),
                occurrence=index + 1,
                capture_ts=run.origin_ts + raw_ticks[index] / 90000,
                now=index * 0.01,
            )
            decisions.append(d)
            complete(q, d, packet.data, packet.key)
            # Advance PCM only as footage becomes committed, never beyond it.
            end_sample = max(cursor, round((q.video_end or 0) / 90000 * RATE))
            while cursor + 960 <= end_sample:
                q.audio(
                    pcm(cursor),
                    round(run.origin_ts * 1e6 + cursor / RATE * 1e6),
                    now=index * 0.01,
                )
                cursor += 960
            q.drain()
            if index == 4:
                assert out.archive.coverage() is not None
                assert decode_video(out.path) and decode_audio(out.path)[1].size
        end_tick = packets[-1].pts + packets[-1].duration
        q.frontier(run.origin_ts + end_tick / 90000)
        assert q.seal(q.order.frontier)
        q.drain(final=True)
        end_sample = round(end_tick / 90000 * RATE)
        while cursor < end_sample:
            count = min(960, end_sample - cursor)
            q.audio(
                pcm(cursor, count),
                round(run.origin_ts * 1e6 + cursor / RATE * 1e6),
                now=0.2,
            )
            cursor += count
        assert q.finish(audio_drained=True)
        out.finish()
    verify_video(out, packets, packets, None)
    rows = ledger.rows_between(0, 2000)
    feeds = ledger.feeds_between(0, 2000)
    values, repeat, stats = feed_map(
        [p.pts for p in packets], run.id, rows, feeds, lambda r: r["frame"]
    )
    assert values == list(range(9)) and not any(repeat) and stats["matched"] == 9
    assert q.pcm.bytes == q.order.pending_bytes == 0
    ledger.detach()


def test_first_idr_must_be_acknowledged_and_audio_handoff_must_drain():
    q = media()
    a = q.offer(sample(1), stamp(1), occurrence=1, capture_ts=1000, now=0)
    assert a.force_idr
    with pytest.raises(RuntimeError, match="requested IDR"):
        complete(q, a, key=False)
    assert q.closed and q.mux.failed
    q = media()
    a = q.offer(sample(1), stamp(1), occurrence=1, capture_ts=1000, now=0)
    complete(q, a)
    q.frontier(1000.04)
    assert q.seal(1000.04)
    assert q.finish() is False and not q.mux.closed
    q.audio(bytes(7680), 1000000000, now=0.04)
    assert q.finish(audio_drained=True) and q.mux.closed
    assert sum(len(data) for data, _ in q.mux.audio) == 7680


def test_late_first_picture_cannot_deadlock_against_queued_audio():
    q = media()
    a = q.offer(sample(1), stamp(1), occurrence=1, capture_ts=1001, now=0)
    b = q.offer(sample(2), stamp(2), occurrence=2, capture_ts=1001.033, now=0.03)
    complete(q, a)
    assert q.mux.video[0].pts == 90000 and q.video_end == 92970
    # The first bounded video packet establishes coverage so PCM may drain.
    q.audio(bytes(3840), 1001000000, now=0.04)
    assert q.mux.audio and q.pcm.bytes == 0
    complete(q, b)
