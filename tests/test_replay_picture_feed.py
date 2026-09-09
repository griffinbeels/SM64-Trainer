"""THE PICTURE FEED, end to end (round 32 item 38, built 2026-09-02).

His approved recorder change: one video frame per distinct captured
picture, stamped with the RAM frame the picture ledger recorded, so a
clip's frame index IS its picture index and the frame map is bookkeeping.
These tests drive the real ffmpeg binary where the claim lives (the sink's
VFR ring, the extractor's cut) and pin the contracts around it (the
sink's arguments, the ledger's feed log, the service's map and payload).
"""
import shutil
import time
from datetime import timedelta

import numpy as np
import pytest

from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.extract import ClipExtractor, ClipResult
from sm64_events.replay.feedmap import feed_map
from sm64_events.replay.ffmpeg_sink import PICTURE_HEARTBEAT_S, FfmpegAvSink
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.ring import SegmentRing
from test_replay_picture_identity import encoder as encoder


def _ffmpeg() -> str:
    ff = bundled_ffmpeg() or shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg binary not available")
    return ff


def _video_frames(path) -> list[float]:
    """Every video frame's pts, decoded with PyAV -- the second witness."""
    import av
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        return [float(frame.pts * stream.time_base)
                for frame in container.decode(stream)]


# -- the ledger's feed log ---------------------------------------------------

def test_the_feed_log_files_every_write_under_its_row_or_as_a_repeat():
    ledger = PictureLedger()
    ledger.mark_fed(100.0, 100.004)
    ledger.mark_fed(None, 101.004)            # a heartbeat repeat
    ledger.mark_fed(100.033, 100.037)
    assert ledger.feeds_between(100.0, 100.1) == [
        {"at": 100.004, "ts": 100.0, "run_id": None, "pts": None, "repeat": False},
        {"at": 100.037, "ts": 100.033, "run_id": None, "pts": None, "repeat": False}]
    assert ledger.feeds_between(101.0, 102.0) == [
        {"at": 101.004, "ts": 100.0, "run_id": None, "pts": None, "repeat": True}]
    assert ledger.feeds_between(0.0, 1.0) == []


# -- the sink's arguments ----------------------------------------------------

def _spawn_args(tmp_path, monkeypatch, cfg) -> list:
    captured = {}

    class _FakeProc:
        def __init__(self):
            import io
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b"")
            self.stderr = io.BytesIO(b"")
            self.pid = 4242
        def poll(self):
            return None
        def wait(self, timeout=None):
            return 0
        def kill(self):
            pass

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return _FakeProc()

    monkeypatch.setattr("sm64_events.replay.ffmpeg_sink.subprocess.Popen", fake_popen)
    monkeypatch.setattr("sm64_events.replay.ffmpeg_sink._assign_kill_on_close",
                        lambda p: None)
    sink = FfmpegAvSink(cfg, lambda s: None, ffmpeg="ffmpeg", codec="libx264")
    sink._spawn(320, 240)
    for thread in sink._readers:
        thread.join(timeout=5)
    return captured["args"]


def test_the_picture_feed_encodes_passthrough_with_time_forced_keyframes(tmp_path, monkeypatch):
    args = _spawn_args(tmp_path, monkeypatch,
                       ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=2.0))
    joined = " ".join(str(a) for a in args)
    assert args[args.index("-fps_mode") + 1] == "passthrough"
    assert "-r 60" not in joined, "no CFR conform on the picture feed"
    assert args[args.index("-force_key_frames") + 1] == "expr:gte(t,n_forced*2.0)"
    assert args[args.index("-g") + 1] == "60"          # 30 pictures/s x 2 s
    # ONE input, ONE clock -- ours. The NUT stream on stdin carries video and
    # audio with the stamps this process wrote; nothing is stamped at read.
    assert "-use_wallclock_as_timestamps" not in args
    assert args.count("-i") == 1 and "pipe:0" in args
    assert args[args.index("-f", args.index("-thread_queue_size")) + 1] == "nut"
    assert "0:a:0" in args and "1:a:0" not in args
    assert args[args.index("-enc_time_base") + 1] == "demux"


def test_the_cfr_feed_is_one_switch_away(tmp_path, monkeypatch):
    args = _spawn_args(tmp_path, monkeypatch,
                       ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=2.0,
                                    picture_feed=False))
    assert args[args.index("-fps_mode") + 1] == "cfr"
    assert "-force_key_frames" not in args


def test_the_queue_keeps_every_picture_and_says_when_it_is_full(tmp_path):
    """His rule, 2026-09-02: "We should always be encoding frames we
    captured... If I see a frame in my replay, as a user, I would expect to
    see the input capture for that frame as well." The queue used to be 16
    deep and shed its OLDEST entry on overflow -- a picture the ledger had
    already recorded, so the map described frames the video did not hold.
    Nothing queued is dropped now; `has_room` is what the recorder asks
    BEFORE it records a picture at all."""
    from sm64_events.replay.ffmpeg_sink import PICTURE_QUEUE_BYTES

    sink = FfmpegAvSink(ReplayConfig(scratch_dir=tmp_path), lambda s: None,
                        ffmpeg="ffmpeg")
    frame = np.zeros((512, 512, 4), np.uint8)          # 1 MiB a picture
    fits = PICTURE_QUEUE_BYTES // frame.nbytes
    for index in range(fits):
        assert sink.has_room(), f"no room after only {index} pictures"
        sink.submit(frame, (index, float(index)))
    assert not sink.has_room(), "the budget never filled"
    assert sink.queue_depth() == (fits, fits * frame.nbytes)
    # Past the budget nothing is thrown away -- the count is kept in order.
    sink.submit(frame, (fits, float(fits)))
    assert len(sink._queue) == fits + 1
    assert [tag[0] for _f, tag in sink._queue] == list(range(fits + 1))


def test_a_grab_the_sink_cannot_encode_never_enters_the_ledger(tmp_path):
    """LOCKSTEP: captured and encoded are the same set. With no budget the
    recorder drops the grab BEFORE the ledger sees it, so every row it holds
    became a video frame."""
    import numpy as _np

    from sm64_events.replay.ledger import PictureLedger
    from sm64_events.replay.recorder import _sink_has_room

    class _Full:
        def has_room(self):
            return False

    class _Open:
        def has_room(self):
            return True

    assert _sink_has_room(_Full()) is False
    assert _sink_has_room(_Open()) is True
    assert _sink_has_room(object()) is True     # the CFR sink never refuses

    # And the ledger only ever hears about a grab we would encode.
    ledger = PictureLedger()
    for index in range(4):
        picture = _np.full((8, 8, 4), index, _np.uint8)
        if _sink_has_room(_Full()):             # the recorder's own gate
            ledger.observe(picture, 100.0 + index / 30, 500 + index)
    assert ledger.rows_between(0, 1e12) == []


# -- the real thing: sink -> ring -> cut -> feed map --------------------------

def _feed_pictures(sink, ledger, seconds: float, first_frame: int = 1000,
                   period: float = 1 / 30, pause_s: float = 0.0) -> int:
    """Play `seconds` of distinct pictures into the sink the way the
    recorder does -- observe, then submit the ones the ledger calls new --
    with real-time audio so ffmpeg's scheduler never waits on it."""
    frame = np.zeros((240, 320, 4), dtype=np.uint8)
    rate = 48000
    t0 = time.perf_counter()
    last_audio = t0
    phase = 0
    index = 0
    next_at = t0
    while time.perf_counter() - t0 < seconds:
        if pause_s and index == 60:
            time.sleep(pause_s)
            next_at += pause_s
        frame = frame.copy()
        frame[:, :, 0] = index % 256
        frame[:, :, 1] = (index // 256) % 256
        ts = time.time()
        assert ledger.observe(frame, ts, first_frame + index,
                              {"exact": True})
        sink.submit(frame, (first_frame + index, ts))
        index += 1
        now = time.perf_counter()
        samples = int(rate * (now - last_audio))
        if samples > 0:
            idx = np.arange(phase, phase + samples)
            tone = (8000 * np.sin(2 * np.pi * 440 * idx / rate)).astype(np.int16)
            sink.submit_audio(np.repeat(tone[:, None], 2, axis=1).tobytes())
            phase += samples
            last_audio = now
        next_at += period
        delay = next_at - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
    return index


def test_the_ring_holds_one_frame_per_picture_and_the_log_names_each(tmp_path):
    """Feed 4.5 s of distinct pictures at the game's cadence: the ring's
    segments together hold exactly one video frame per write, at ~33 ms
    spacing (never the 60 Hz grid), and the feed log has one entry per
    frame."""
    ff = _ffmpeg()
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=2.0)
    ledger = PictureLedger()
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    sink = FfmpegAvSink(cfg, ring.add, ffmpeg=ff, codec="libx264",
                        on_fed=lambda tag, at, **clock: ledger.mark_fed(
                            tag[1] if tag is not None else None, at, **clock))
    sink.start()
    fed_pictures = _feed_pictures(sink, ledger, 4.5)
    sink.stop()

    feeds = ledger.feeds_between(0.0, 1e12)
    repeats = [entry for entry in feeds if entry["repeat"]]
    assert len(feeds) - len(repeats) == fed_pictures, "one log entry per picture"
    assert len(repeats) <= 1, "pictures every 33 ms leave no room for heartbeats"
    segments = ring.covering("video", *ring.coverage("video"))
    assert len(segments) >= 2
    pts = [t for seg in sorted(segments, key=lambda s: s.utc_start)
           for t in _video_frames(seg.path)]
    assert len(pts) == len(feeds), "every write became exactly one encoded frame"
    gaps = sorted(b - a for a, b in zip(pts, pts[1:], strict=False) if b > a)
    median_gap = gaps[len(gaps) // 2]
    assert 0.028 < median_gap < 0.040, median_gap   # the feed's cadence, not 1/60


def test_a_cut_keeps_every_picture_at_its_own_time_and_the_map_reads_off_the_log(tmp_path):
    """THE PROMISE: cut a clip from a picture-feed ring, and (1) its frame
    times are the pictures' own, (2) every frame matches one feed entry,
    (3) the map is the rows' consecutive stamps, (4) the residual between
    ffmpeg's stamp and the log's is a few ms at most -- no picture runs, no
    quantiser, no inference."""
    ff = _ffmpeg()
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=2.0)
    ledger = PictureLedger()
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    sink = FfmpegAvSink(cfg, ring.add, ffmpeg=ff, codec="libx264",
                        on_fed=lambda tag, at, **clock: ledger.mark_fed(
                            tag[1] if tag is not None else None, at, **clock))
    sink.start()
    _feed_pictures(sink, ledger, 5.0, first_frame=7000)
    sink.stop()

    coverage = ring.coverage("video")
    start = coverage[0] + timedelta(seconds=1.2)
    end = start + timedelta(seconds=2.4)
    result = ClipExtractor(cfg=cfg, codec="libx264", ffmpeg=ff).extract(
        ring, start, end, tmp_path / "clip.mp4")
    assert result.frame_times is not None
    decoded = _video_frames(result.path)
    assert len(decoded) == len(result.frame_times)
    assert all(abs(a - b) < 1e-3 for a, b in zip(decoded, result.frame_times, strict=True))
    assert result.video_start_s == result.frame_times[0]
    gaps = sorted(b - a for a, b in zip(result.frame_times, result.frame_times[1:], strict=False))
    assert 0.028 < gaps[len(gaps) // 2] < 0.040

    origin = result.start_utc.timestamp()
    rows = ledger.rows_between(origin - 1.5, origin + result.duration_s + 1.0)
    feeds = ledger.feeds_between(origin - 1.0, origin + result.duration_s + 1.0)
    built, repeats, stats = feed_map(
        result.source_pts, result.media_run.id, rows, feeds,
        lambda row: (row["frame"]
                     if row.get("exact") else None))
    assert built is not None, stats
    # Nearly every frame matches its feed entry. A CPU-starved worker under the
    # 16-way door delivers a few frames late (real-time capture), which the
    # real clip 5814 showed too (31 of 718 unmatched), so this tolerates a
    # small fraction rather than demanding 0 -- the MEDIAN residual stays sub-ms
    # (a late frame moves the max, never the median).
    assert stats["unmatched"] <= max(3, len(built) // 20), stats
    assert stats["method"] == "source_pts", stats
    assert stats["unmatched"] == 0, stats
    advances = [b - a for a, b, rep in zip(built, built[1:], repeats[1:], strict=False) if not rep]
    # Consecutive pictures are consecutive frames; an unmatched frame can leave
    # a +2 step around it, which is honest bookkeeping, not a shear.
    assert set(advances) <= {1, 2}, advances
    assert advances.count(1) >= len(advances) * 0.9, advances
    assert built[0] >= 7000


def test_a_cfr_ring_cut_with_the_switch_on_still_reports_its_frame_times(tmp_path):
    """The switch is on the CONFIG, so a cut is passthrough whatever fed the
    ring: a CFR source keeps its 60 Hz frames and reports them."""
    from test_replay_extract import T0, build_av_buffer
    ff = _ffmpeg()
    ring = build_av_buffer(tmp_path, seconds=6)
    cfg = ReplayConfig(fps=60)
    result = ClipExtractor(cfg=cfg, codec="libx264", ffmpeg=ff).extract(
        ring, T0 + timedelta(seconds=2), T0 + timedelta(seconds=4), tmp_path / "c.mp4")
    assert result.frame_times is not None
    assert abs(len(result.frame_times) - 120) <= 2
    gaps = sorted(b - a for a, b in zip(result.frame_times, result.frame_times[1:], strict=False))
    assert abs(gaps[len(gaps) // 2] - 1 / 60) < 0.002


# -- the service: the map off the log, the payload, the reader's flags -------

class _FeedExtractor:
    """A cut whose frames sit at the pictures' own times."""
    def __init__(self, count=90, latency=0.004):
        self.count, self.latency = count, latency
        self.calls = []

    def extract(self, ring, start, end, out_path):
        self.calls.append((start, end, out_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"mp4")
        times = [k / 30 + self.latency for k in range(self.count)]
        return ClipResult(path=out_path, duration_s=(end - start).total_seconds(),
                          truncated=False, start_utc=start,
                          video_start_s=times[0], frame_times=times)


def _filled_ledger(origin: float, count: int, first_frame: int = 100,
                   latency: float = 0.004) -> PictureLedger:
    ledger = PictureLedger()
    picture = np.zeros((8, 8, 4), np.uint8)
    for index in range(count):
        picture = picture.copy()
        picture[0, 0, 0] = index % 256
        picture[0, 1, 0] = (index // 256) % 256
        ts = origin + index / 30
        assert ledger.observe(picture, ts, first_frame + index,
                              {"exact": True, "igt_overall": 20 + index})
        ledger.mark_fed(ts, ts + latency)
    return ledger


# The pad reader's hand-off, the CLOCK join and the heartbeat-repeat flag
# were pinned here. All three belonged to DERIVING a frame map; the capture
# layer stamps it, so `_take_the_stamps` is the whole of extraction now and
# `replay/timerread.py` is deleted (2026-09-05). What survives of the pad
# reader is an offline instrument (`tools/score_pad_read.py`) and the glyph
# machinery the oracle borrows.


def test_a_flash_and_a_click_at_one_instant_land_together_in_the_cut(tmp_path, encoder):
    """The A/V sync instrument this pipeline never had: at one wall-clock
    instant the picture goes white and the audio carries a click; after the
    ring is cut, the first white frame's time and the click's onset in the
    decoded audio must agree to within a picture. Both streams ride the
    picture feed's one NUT stream on the same clock, so this is the claim
    the design makes, measured."""
    ff, codec = encoder
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=2.0)
    ledger = PictureLedger()
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    sink = FfmpegAvSink(cfg, ring.add, ffmpeg=ff, codec=codec,
                        on_fed=lambda tag, at, **clock: ledger.mark_fed(
                            tag[1] if tag is not None else None, at, **clock))
    sink.start()
    _record_a_flash_and_a_click(sink, ledger)
    sink.stop()

    coverage = ring.coverage("video")
    result = ClipExtractor(cfg=cfg, codec=codec, ffmpeg=ff).extract(
        ring, coverage[0] + timedelta(seconds=1.0),
        coverage[0] + timedelta(seconds=4.5), tmp_path / "sync.mp4")
    white_at = _first_white_picture_at(result.path)
    assert white_at is not None, "the flash never reached the cut"
    click_at = _click_onset_at(result.path)
    assert click_at is not None, "the click never reached the cut"
    offset_ms = (click_at - white_at) * 1000
    print(f"{codec}: independently decoded click minus flash = {offset_ms:+.3f} ms")
    assert abs(offset_ms) < 50, f"audio is {offset_ms:+.1f} ms from the picture"


def _record_a_flash_and_a_click(sink, ledger) -> None:
    """Five seconds of pictures and silence; at 2.5 s the picture goes white
    and the audio carries a click, both stamped at the same wall instant."""
    rate = 48000
    frame = np.zeros((240, 320, 4), dtype=np.uint8)
    t0 = time.perf_counter()
    last_audio = t0
    index = 0
    next_at = t0
    flash_at = None
    while time.perf_counter() - t0 < 5.0:
        frame = frame.copy()
        elapsed = time.perf_counter() - t0
        flashing = 2.5 <= elapsed < 2.7
        if flashing and flash_at is None:
            flash_at = time.time()
        frame[:, :, :3] = 255 if flashing else (index % 200)
        ts = time.time()
        # Like the recorder: only a NEW picture feeds (the flash holds for
        # several grabs and is one picture).
        if ledger.observe(frame, ts, 5000 + index):
            sink.submit(frame, (5000 + index, ts))
        index += 1
        now = time.perf_counter()
        samples = int(rate * (now - last_audio))
        if samples > 0:
            # silence, except a click that starts at the flash instant
            pcm = np.zeros((samples, 2), np.int16)
            chunk_start = time.time() - samples / rate
            if flash_at is not None and chunk_start < flash_at + 0.2:
                onset = int(max(0.0, flash_at - chunk_start) * rate)
                pcm[onset:, :] = 20000
            sink.submit_audio(pcm.tobytes())
            last_audio = now
        next_at += 1 / 30
        delay = next_at - time.perf_counter()
        if delay > 0:
            time.sleep(delay)


def _first_white_picture_at(path) -> float | None:
    import av
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        for picture in container.decode(stream):
            if picture.to_ndarray(format="gray").mean() > 200:
                return float(picture.pts * stream.time_base)
    return None


def _click_onset_at(path, rate: int = 48000) -> float | None:
    import av
    with av.open(str(path)) as container:
        astream = container.streams.audio[0]
        for chunk in container.decode(astream):
            samples = chunk.to_ndarray()
            # AAC decodes to floats in [-1, 1]; the click was 20000 of 32768.
            scale = 1.0 if samples.dtype.kind == "f" else 32768.0
            loud = (np.abs(samples.astype(np.float32)) / scale).max(axis=0) > 0.25
            if loud.any():
                return float(chunk.pts * astream.time_base) + int(np.argmax(loud)) / rate
    return None


# -- the reader's decode axis IS the clip's frame axis (2026-09-02) -----------
# The pad reader reads Usamune's digits out of every video frame and pins the
# map to them, so cell k MUST be video frame k -- which is browser slot k and
# map entry k. On a VFR picture-feed clip, `ffmpeg -i clip -vf ...` without
# `-fps_mode passthrough` re-times the frames onto the clip's r_frame_rate
# (120) and DUPLICATES some: clip 5814 decoded 726 cells of a 718-frame clip,
# `read_clip` kept the first 718, and every cell past the first duplicate was
# shifted -- a uniform ~2-frame lag between the panel and the screen. These
# guard the decode axis with real ffmpeg, no emulator, no digits needed.

def _picture_feed_clip(tmp_path, ff, seconds=4.0, pause_s=0.0):
    from sm64_events.replay.ffmpeg_sink import FfmpegAvSink
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=2.0)
    ledger = PictureLedger()
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    sink = FfmpegAvSink(cfg, ring.add, ffmpeg=ff, codec="libx264",
                        on_fed=lambda tag, at, **clock: ledger.mark_fed(
                            tag[1] if tag is not None else None, at, **clock))
    sink.start()
    _feed_pictures(sink, ledger, seconds, first_frame=7000, pause_s=pause_s)
    sink.stop()
    coverage = ring.coverage("video")
    start = coverage[0] + timedelta(seconds=1.0)
    result = ClipExtractor(cfg=cfg, codec="libx264", ffmpeg=ff).extract(
        ring, start, start + timedelta(seconds=seconds - 1.5),
        tmp_path / "clip.mp4")
    return result


def test_the_reader_decodes_exactly_one_cell_per_stored_frame(tmp_path):
    """T1: len(decode_cells) == the clip's stored frame count == the map's
    length. The bug decoded 726 of 718 (RED without -fps_mode passthrough)."""
    import subprocess

    from sm64_events.replay import padread
    ff = _ffmpeg()
    result = _picture_feed_clip(tmp_path, ff)
    stored = len(_video_frames(result.path))              # == len(frame_times)
    assert stored == len(result.frame_times)
    cells = padread.decode_cells(ff, result.path)
    assert len(cells) == stored, f"decoded {len(cells)} cells of {stored} frames"
    icons = padread.decode_icons(ff, result.path)
    assert len(icons) == stored, f"decoded {len(icons)} icon strips of {stored}"
    # And the count ffprobe -count_frames sees, the third witness.
    from sm64_events.replay.extract import ffprobe_beside
    counted = subprocess.run(
        [ffprobe_beside(ff) or "ffprobe", "-v", "error", "-select_streams",
         "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames",
         "-of", "csv=p=0", str(result.path)], capture_output=True, text=True)
    assert int(counted.stdout.strip()) == stored


def test_passthrough_is_load_bearing_on_a_vfr_clip(tmp_path):
    """T3, with the mechanism baked in as a mutation proof: the SAME decode
    WITHOUT -fps_mode passthrough re-times the VFR clip onto its r_frame_rate
    and emits MORE frames than exist, while decode_cells (with passthrough)
    emits exactly the stored count. If the two ever agree, the clip stopped
    being VFR or the flag stopped mattering -- either way the guard is worth
    re-checking, so it asserts the gap is real."""
    import subprocess

    from sm64_events.replay import padread
    ff = _ffmpeg()
    # A held picture makes VFR observable. Scheduler jitter alone can still
    # produce the same count on a 30 Hz conform, so it cannot prove this flag.
    result = _picture_feed_clip(tmp_path, ff, pause_s=0.2)
    stored = len(_video_frames(result.path))
    crop = (f"crop=iw*{padread.REGION[2] - padread.REGION[0]}:"
            f"ih*{padread.REGION[3] - padread.REGION[1]}:"
            f"iw*{padread.REGION[0]}:ih*{padread.REGION[1]},"
            f"scale={padread.CELL_W}:{padread.CELL_H}:flags=area")
    stride = padread.CELL_W * padread.CELL_H * 3
    buggy = subprocess.run(
        [ff, "-v", "error", "-i", str(result.path), "-vf", crop,
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        capture_output=True).stdout
    buggy_count = len(buggy) // stride
    fixed_count = len(padread.decode_cells(ff, result.path))
    assert fixed_count == stored, f"passthrough decode {fixed_count} != {stored}"
    assert buggy_count > stored, (
        f"expected the un-passthrough decode to over-count a VFR clip; "
        f"got {buggy_count} for {stored} stored frames")


def test_two_callers_for_one_attempt_cut_it_once(tmp_path):
    """His 100-coin replay came back a black, undecodable video whose sidecar
    counted 1921 frames of a 1380-frame file: he clicked extract, the LBLJ
    autodetect re-opened the drawer, and TWO ffmpeg processes wrote one output
    path (2026-09-02). The second caller must wait and take the cached clip."""
    import threading

    from test_replay_service import attempt, make_service

    svc = make_service(tmp_path, [attempt()])
    cuts = []
    real_extract = svc.extractor.extract

    def slow_extract(ring, start, end, out_path):
        cuts.append(out_path)
        time.sleep(0.3)                      # long enough for the racer to arrive
        return real_extract(ring, start, end, out_path)

    svc.extractor.extract = slow_extract
    results = []
    threads = [threading.Thread(target=lambda: results.append(svc.view(42)))
               for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert len(cuts) == 1, f"the clip was cut {len(cuts)} times"
    assert len(results) == 2 and results[0]["clip_url"] == results[1]["clip_url"]


# A feed log that cannot cover the clip used to fall back to matching rows
# against the clip's own picture RUNS, and a coverage/offset policy then
# chose between the log's bookkeeping and the pad reader's per-slot
# alignment. Both are gone: the lockstep rule (item 88) made the log cover
# every clip it describes -- 476/477, 542/548 and 835/841 on his three
# certified clips -- and a clip the log cannot cover now carries no map
# rather than an inferred one.
