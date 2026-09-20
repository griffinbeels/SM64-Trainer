"""Trace independently readable picture identities through the real replay pipeline."""
from collections import Counter
from datetime import timedelta
import json
import shutil
import subprocess
import time

import av
import numpy as np
import pytest

from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.feedmap import feed_map
from sm64_events.replay.ffmpeg_sink import FfmpegAvSink
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.ring import SegmentRing


def picture(number, size=(320, 96)):
    width, height = size
    image = np.zeros((height, width, 4), dtype=np.uint8)
    image[:, :, 3] = 255
    for bit in range(8):
        image[:, bit * width // 8:(bit + 1) * width // 8, :3] = 255 if number & (1 << bit) else 0
    return image


def read_pictures(path):
    # Ring files are MPEG-TS even when a one-picture segment is too small
    # for autodetection (which can mistake it for audio-only MPEG-PS).
    container_format = "mpegts" if str(path).endswith(".ts") else None
    with av.open(str(path), format=container_format) as container:
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 1
        result = []
        for frame in container.decode(stream):
            pixels = frame.to_ndarray(format="gray")
            cell = frame.width // 8
            number = sum(1 << bit for bit in range(8)
                         if pixels[40:56, bit * cell + cell//3:bit * cell + 2*cell//3].mean() > 128)
            result.append((float(frame.pts * frame.time_base), number))
        return result


@pytest.fixture(scope="session", params=["libx264", "h264_nvenc", "h264_amf", "h264_qsv"])
def encoder(request):
    """One real encoder, or an honest skip naming the vendor that is missing.

    SESSION scope, and the skip text carries no ffmpeg output. Twelve modules
    import this fixture, so module scope re-probed every codec twelve times,
    and the old message pasted the last 300 bytes of stderr -- which contains
    a heap POINTER, so the same missing encoder produced a dozen different
    skip reasons and `tests/skip_inventory.py` could not group them.
    """
    ff = bundled_ffmpeg() or shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg required")
    codec = request.param
    probe = subprocess.run(
        [ff, "-v", "error", "-f", "lavfi", "-i", "color=size=640x480",
         "-frames:v", "1", "-c:v", codec, "-f", "null", "-"],
        capture_output=True, timeout=15, check=False, **quiet_spawn_kwargs())
    if probe.returncode:
        pytest.skip(f"{codec} is not usable on this machine "
                    f"(ffmpeg exit {probe.returncode}); that vendor's hardware "
                    "encoder is absent, so its path stays untested here")
    return ff, codec


def record_picture_schedule(sink, ledger, schedule):
    """Feed the independent identities through capture timing edge cases."""
    sink.start()
    if schedule == "queued_audio_and_catchup":
        # The audio tap can start before the first picture. Its first chunk
        # begins in the past, and must not shift the NUT stream's origin.
        sink.submit_audio(bytes(48000 * 4 // 8))
    started = time.perf_counter()
    origin = time.time()
    try:
        for number in range(120):
            if schedule == "queued_audio_and_catchup" and number == 45:
                time.sleep(0.22)  # several real pictures then arrive in a burst
            # A normal capture size: AMF rejects the 96px-high tiny browser
            # fixture even though it works at gameplay dimensions.
            pixels = picture(number, size=(640, 480))
            # Model capture's high-resolution UTC clock. Windows time.time()
            # can repeat during the catch-up burst, creating ambiguous row
            # identities before the mapping under test even sees them.
            stamp = origin + (time.perf_counter() - started)
            if schedule == "timestamp_collision" and number < 3:
                stamp = origin + [0, 0.000001, 0.000012][number]
            assert ledger.observe(pixels, stamp, number, {"exact": True})
            sink.submit(pixels, (number, stamp))
            delay = started + (number + 1) / 30 - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
    finally:
        sink.stop()


@pytest.mark.parametrize("schedule", ["regular", "queued_audio_and_catchup", "timestamp_collision"])
def test_cut_map_names_the_picture_instead_of_only_matching_a_cadence(tmp_path, encoder, schedule):
    ff, codec = encoder
    config = ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=1.0)
    ring = SegmentRing(retention_s=None, max_bytes=10**8)
    ledger = PictureLedger()
    sink = FfmpegAvSink(config, ring.add, ffmpeg=ff, codec=codec,
                        on_fed=lambda tag, at, **clock: ledger.mark_fed(tag[1] if tag else None, at, **clock))
    record_picture_schedule(sink, ledger, schedule)
    coverage = ring.coverage("video")
    segments = ring.covering("video", *coverage)
    original = [row for segment in segments for row in read_pictures(segment.path)]
    source_pixels = {}
    for segment in segments:
        with av.open(str(segment.path)) as container:
            source_pixels.update({round(frame.pts * frame.time_base * 90000): frame.to_ndarray(format="yuv420p").tobytes()
                                  for frame in container.decode(video=0)})
    # Calibrate the pixel reader against the independent source identities.
    assert [number for _, number in original] == list(range(120)) + [119] * (len(original) - 120)
    rows = ledger.rows_between(0, 1e12)
    feeds = ledger.feeds_between(0, 1e12)
    repeated_stamps = {stamp: count for stamp, count in Counter(row["ts"] for row in rows).items()
                       if count > 1}
    assert not repeated_stamps, f"Fixture assigned identical capture timestamps: {repeated_stamps}"
    assert [round(t * 90000) for t, _ in original] == [entry["pts"] for entry in feeds]
    if schedule == "timestamp_collision":
        assert [entry["pts"] for entry in feeds[:3]] == [0, 1, 2]
    report = {"codec": codec, "schedule": schedule, "run_epoch": sink._run_epoch,
              "source_shift_s": [original[n][0] - (rows[n]["ts"] - sink._run_epoch) for n in [0, 50, 100]],
              "ring_start": coverage[0].timestamp(),
              "first_source_pts": original[0][0], "first_feed": feeds[0], "cuts": []}
    for phase in [-1.2, 0, .000017, .000028, .000039, .000050,
                  .012345, .023456, .998765, 1.010007]:
        start = coverage[0] + timedelta(seconds=1.2 + phase)
        result = ClipExtractor(cfg=config, codec=codec, ffmpeg=ff).extract(
            ring, start, start + timedelta(seconds=1.0), tmp_path / f"cut-{phase}.mp4")
        decoded = read_pictures(result.path)
        with av.open(str(result.path)) as container:
            copied_pixels = [frame.to_ndarray(format="yuv420p").tobytes() for frame in container.decode(video=0)]
        assert copied_pixels == [source_pixels[pts] for pts in result.source_pts], "native cuts must not re-encode pixels"
        mapped, _, stats = feed_map(result.source_pts, result.media_run.id,
                                    rows, feeds, lambda row: row["frame"])
        assert mapped is not None, stats
        assert mapped == [number for _, number in decoded], (phase, stats)
        # The clock itself, not a fitted residual: compare to the actual
        # captured picture's independent timestamp at every decoded slot.
        # The pathological collision case deliberately assigns distinct ticks
        # to pictures closer than the container can represent. Identity remains
        # exact; those two pictures need not retain their impossible spacing.
        if schedule != "timestamp_collision":
            assert max(abs(rows[number]["ts"] - result.start_utc.timestamp() - pts)
                       for pts, number in decoded) < 1 / 90000
        offsets = Counter(actual - expected for actual, (_, expected) in zip(mapped, decoded, strict=True)
                          if actual is not None)
        report["cuts"].append({"phase": phase, "offsets": dict(offsets), "stats": stats,
                               "first_picture": decoded[0], "first_map": mapped[0],
                               "absolute_error_ms": max(abs(rows[number]["ts"] - result.start_utc.timestamp() - pts) * 1000 for pts, number in decoded)})
    (tmp_path / "identity-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({**report, "cuts": [{"phase": c["phase"], "offsets": c["offsets"], "first_picture": c["first_picture"], "first_map": c["first_map"], "absolute_error_ms": c["absolute_error_ms"]} for c in report["cuts"]]}, indent=2))
    assert all(set(cut["offsets"]) == {0} for cut in report["cuts"]), report
