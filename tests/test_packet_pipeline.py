"""Actual GPU-encoded bytes through ordered metadata, AAC and progressive archive."""

import av
from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetorder import (
    ExistingTickAllocator,
    OrderedPackets,
    PacketReady,
    Ticket,
)
from sm64_events.replay.packetmux import EncodedPicture, PacketFragmentMux
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


def emit(q, mux):
    emitted = []
    while packet := q.take_packet():
        assert isinstance(packet, PacketReady)
        mux.write_video(
            EncodedPicture(
                packet.source.occurrence,
                packet.pts,
                packet.duration,
                packet.keyframe,
                packet.payload,
            )
        )
        receipt = q.ack_muxed(packet.ticket)
        assert receipt.kind == "muxed"
        emitted.append(packet)
    return emitted


def test_ordered_completion_reaches_playable_prefix_before_later_footage(tmp_path):
    pictures = fixture_packets()
    raw_ticks = [0, 3001, 3001, 179999, 180000, 720000, 720000, 720000, 720000]
    run = MediaRun("ordered-fragments", 1000)
    q = OrderedPackets(
        run, ExistingTickAllocator(run), encoder_duration=3000, max_age=10
    )
    out = Output(tmp_path, run)
    with av.open(str(FIXTURE / "witness.h264")) as source:
        mux = PacketFragmentMux(
            out,
            source.streams.video[0],
            run,
            audio_rate=RATE,
            audio_bitrate=160000,
            packet_limit=8192,
            pcm_limit=384000,
        )
        requests = []
        for index in range(5):
            stamp = run.origin_ts + raw_ticks[index] / 90000
            ticket = q.offer(
                index + 1, stamp, index, {"pad": index}, packet_limit=8192, now=0
            )
            assert isinstance(ticket, Ticket)
            assert q.resolve(ticket, "selected")
            requests.append(q.take_encode())
        # Newer packet completion cannot publish past the unfinished head.
        for index in [1, 2, 3, 4, 0]:
            req = requests[index]
            assert q.complete(
                req.ticket,
                pictures[index].data,
                pts=req.pts,
                duration=req.encoder_duration,
                keyframe=pictures[index].key,
            )
            if index != 0:
                assert q.take_packet() is None
        emitted = emit(q, mux)
        assert [p.source.occurrence for p in emitted] == [1, 2, 3, 4]
        assert q.pending_count == 1  # Last picture retained; no guessed final duration.
        split_sample = 115200
        for first in range(-2400, split_sample, 960):
            mux.write_pcm(
                pcm(first, min(960, split_sample - first)),
                round(run.origin_ts * 1e6 + first / RATE * 1e6),
            )
        coverage = out.archive.coverage()
        assert coverage is not None and coverage[0] == 0 and coverage[1] > 0
        assert decode_video(out.path) and decode_audio(out.path)[1].shape[1] > 0
        assert q.pending_count == 1 and mux.video_count == 4
        for index in range(5, 9):
            ticket = q.offer(
                index + 1,
                run.origin_ts + raw_ticks[index] / 90000,
                index,
                {"pad": index},
                packet_limit=8192,
                now=0,
            )
            assert q.resolve(ticket, "selected")
            req = q.take_encode()
            assert q.complete(
                ticket,
                pictures[index].data,
                pts=req.pts,
                duration=req.encoder_duration,
                keyframe=pictures[index].key,
            )
            emit(q, mux)
        end_tick = pictures[-1].pts + pictures[-1].duration
        q.advance_frontier(run.origin_ts + end_tick / 90000)
        assert q.seal(q.frontier)
        emit(q, mux)
        end_sample = round(end_tick / 90000 * RATE)
        for first in range(split_sample, end_sample, 960):
            mux.write_pcm(
                pcm(first, min(960, end_sample - first)),
                round(run.origin_ts * 1e6 + first / RATE * 1e6),
            )
        mux.close()
        out.finish()
    assert q.pending_count == q.pending_bytes == 0
    verify_video(out, pictures, pictures, None)
