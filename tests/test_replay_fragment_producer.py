"""Continuing-producer contract for fragmented replay publication, the default output.

The tail is withheld from stdin, not merely from the HTTP consumer. No emulator,
device, live recorder, codec selection policy or production output is changed.
"""
import json
import os
import subprocess
import threading
import time

import av
import numpy as np

from test_replay_picture_identity import encoder as encoder, picture, read_pictures
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay.config import ReplayConfig, RING_MAXRATE, video_quality_args, raw_picture_args, forced_idr_args
from sm64_events.replay.ffmpeg_sink import FfmpegAvSink
from sm64_events.replay.fragments import FragmentReader
from sm64_events.replay.media import MediaRun, picture_duration_filter


def send_pictures(child, directory, release, prefix_sent, ticks, errors):
    """Use the actual NUT pipe; never materialize or retain the raw recording."""
    sink = FfmpegAvSink(ReplayConfig(scratch_dir=directory), lambda _: None)
    sink._media_run = MediaRun("fixture", 1000.0)
    sink._run_epoch = 1000.0
    sink._proc = child
    try:
        sink._open_mux(640, 480)
        for number in range(60):
            stamp = 1000 + number / 30
            if number < 3:
                stamp = 1000 + [0, .000001, .000012][number]
            ticks.append(sink._mux_picture(picture(number, (640, 480)), stamp))
            # Independent 440 Hz samples, never re-timed to an observed result.
            sample = np.arange(number * 1600, (number + 1) * 1600)
            tone = np.rint(np.sin(sample * (2 * np.pi * 440 / 48000)) * 12000).astype("<i2")
            pcm = np.repeat(tone[:, None], 2, axis=1).tobytes()
            sink._mux_audio_chunk(pcm, 1_000_000_000 + round(number / 30 * 1_000_000))
            if number == 47:
                prefix_sent.set()
                if not release.wait(15):
                    raise TimeoutError("tail was never released")
    except (OSError, ValueError, av.error.FFmpegError) as error:
        errors.append(repr(error))
    finally:
        sink._close_mux()
        child.stdin.close()


def command(ffmpeg, codec):
    return [ffmpeg, "-hide_banner", "-loglevel", "warning", "-copyts",
            "-thread_queue_size", "1024", "-f", "nut", "-i", "pipe:0",
            "-map", "0:v:0", "-map", "0:a:0", "-c:v", codec,
            *video_quality_args(codec, "realtime", RING_MAXRATE), *raw_picture_args(codec),
            "-bf", "0", "-g", "60", "-force_key_frames", "expr:gte(t,n_forced*2)",
            *forced_idr_args(codec), "-fps_mode", "passthrough", "-enc_time_base", "demux",
            "-bsf:v", picture_duration_filter(), "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-af", "aresample=async=1:first_pts=0:min_hard_comp=0.1",
            "-flags", "+global_header", "-f", "tee",
            "[f=mp4:movflags=delay_moov+default_base_moof+frag_keyframe:frag_duration=100000:"
            "movie_timescale=90000:video_track_timescale=90000:avoid_negative_ts=disabled:flush_packets=1]pipe:1|"
            "[f=mpegts:mpegts_copyts=1:avoid_negative_ts=disabled]reference.ts"]


def decoded_video(path):
    with av.open(str(path)) as media:
        return [(round(frame.pts * frame.time_base * 90000), frame.to_ndarray(format="yuv420p").tobytes())
                for frame in media.decode(video=0)]


def decoded_audio(path):
    with av.open(str(path)) as media:
        frames = [(round(frame.pts * frame.time_base * 48000), frame.to_ndarray())
                  for frame in media.decode(audio=0)]
    for (start, samples), (following, _) in zip(frames, frames[1:], strict=False):
        assert following == start + samples.shape[1], "audio sample clock has a hole or overlap"
    return frames[0][0], np.concatenate([samples for _, samples in frames], axis=1)


def receive_units(child, output, units, ready, release, errors, started):
    parser = FragmentReader()
    try:
        with output.open("wb") as destination:
            while data := os.read(child.stdout.fileno(), 64 * 1024):
                for unit in parser.feed(data):
                    destination.write(unit.data)
                    destination.flush()
                    units.append({"kind": unit.kind, "offset": unit.offset, "size": len(unit.data),
                                  "ms": (time.perf_counter() - started) * 1000,
                                  "before_tail": not release.is_set()})
                    if unit.kind == "media":
                        ready.set()
        parser.finish()
    except (OSError, ValueError) as error:
        errors.append(repr(error))


def read_published_prefix(output, units):
    prefix_path = output.with_name("prefix.mp4")
    length = sum(unit["size"] for unit in list(units))
    with output.open("rb") as published:
        prefix_path.write_bytes(published.read(length))
    return read_pictures(prefix_path)


def continuing_output(tmp_path, ffmpeg, codec):
    output = tmp_path / "live.mp4"
    args = command(ffmpeg, codec)
    release, ready, prefix_sent = threading.Event(), threading.Event(), threading.Event()
    errors, units, ticks = [], [], []
    started = time.perf_counter()
    stderr_path = tmp_path / "encoder.log"
    with stderr_path.open("wb") as stderr:
        # The tee receives each encoded packet once. The TS witness and fMP4
        # candidate therefore cannot differ because a second encoder ran.
        child = subprocess.Popen(args, cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=stderr, bufsize=0, **quiet_spawn_kwargs())

        writer = threading.Thread(target=send_pictures,
                                  args=(child, tmp_path, release, prefix_sent, ticks, errors), daemon=True)
        reader = threading.Thread(target=receive_units,
                                  args=(child, output, units, ready, release, errors, started), daemon=True)
        writer.start()
        reader.start()
        prefix_pictures = []
        try:
            # A continuing producer must publish before stdin closes. Waiting
            # here is bounded and distinguishes analysis buffering from EOF.
            assert prefix_sent.wait(10), f"NUT prefix stalled: {errors}"
            prefix_ready = ready.wait(3)
            if prefix_ready:
                prefix_pictures = read_published_prefix(output, units)
            release.set()
            writer.join(10)
            assert not writer.is_alive(), "NUT feed did not complete"
            assert child.wait(timeout=15) == 0, stderr_path.read_text(errors="replace")
            reader.join(5)
            assert not reader.is_alive(), "fragment reader did not reach EOF"
        finally:
            release.set()
            if child.poll() is None:
                child.kill()  # only this owned, admitted fixture child
                child.wait(timeout=5)
            writer.join(5)
            reader.join(5)
            child.stdout.close()
            if not child.stdin.closed:
                child.stdin.close()
    return {"codec": codec, "command": args, "units": units, "prefix_ready": prefix_ready,
            "prefix_pictures": prefix_pictures, "errors": errors, "expected_ticks": ticks}


def test_continuing_encoder_prefix(tmp_path, encoder):
    ffmpeg, codec = encoder
    result = continuing_output(tmp_path, ffmpeg, codec)

    output = tmp_path / "live.mp4"
    decoded = read_pictures(output)
    reference = tmp_path / "reference.ts"
    actual_video, reference_video = decoded_video(output), decoded_video(reference)
    actual_start, actual_audio = decoded_audio(output)
    reference_start, reference_audio = decoded_audio(reference)
    overlap_start = max(actual_start, reference_start)
    overlap_end = min(actual_start + actual_audio.shape[1], reference_start + reference_audio.shape[1])
    actual_overlap = actual_audio[:, overlap_start-actual_start:overlap_end-actual_start]
    reference_overlap = reference_audio[:, overlap_start-reference_start:overlap_end-reference_start]
    audio_equal = np.array_equal(actual_overlap, reference_overlap)
    with av.open(str(output)) as media:
        packets = [{"kind": packet.stream.type, "pts": packet.pts, "dts": packet.dts,
                    "duration": packet.duration, "tb": str(packet.time_base), "key": packet.is_keyframe}
                   for packet in media.demux() if packet.size]
    report = {**result,
              "picture_ticks": [round(pts * 90000) for pts, _ in decoded],
              "picture_ids": [number for _, number in decoded], "packets": packets,
              "withheld_pictures": 12, "output_bytes": output.stat().st_size,
              "video_matches_same_encoded_ts": actual_video == reference_video,
              "audio_same_samples": audio_equal, "audio_overlap": [overlap_start, overlap_end],
              "audio_mp4_span": [actual_start, actual_start + actual_audio.shape[1]],
              "audio_ts_span": [reference_start, reference_start + reference_audio.shape[1]]}
    (tmp_path / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("codec", "prefix_ready", "prefix_pictures", "errors", "output_bytes")}))
    assert not result["errors"], result["errors"]
    assert [number for _, number in decoded] == list(range(60)), report
    assert report["picture_ticks"] == result["expected_ticks"], report
    assert report["video_matches_same_encoded_ts"], report
    assert overlap_end - overlap_start >= 96000 and audio_equal, report
    assert result["prefix_ready"] and result["prefix_pictures"], report
    assert result["prefix_pictures"][0][1] == 0, report
    # This proves retained decoded samples on the encoded sample clock; it does
    # not measure browser or speaker presentation timing under live load.
