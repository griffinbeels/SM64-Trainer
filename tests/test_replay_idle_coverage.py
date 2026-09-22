"""Long automatic idle -> immediate reset through real retained media.

The recorder and source's demand policy run with inert OS/device boundaries.
One bounded CPU H.264 encoder feeds identical compressed bytes into the actual
recorder's FragmentMedia and an unfiltered reference archive. Decoder barcodes,
source IDs, exact packet ticks, and AAC samples independently check the cut.
Synthetic clocks cover 58 seconds without real-time sleeps or live input.
"""
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import io
import json
import math
from types import SimpleNamespace

import av
import numpy as np
import pytest

from sm64_events.memory.layout import US
from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.feedmap import feed_map
from sm64_events.replay.fragmentmedia import FragmentMedia
from sm64_events.replay.gpucapture import GpuCapture
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.media import MEDIA_TIME_BASE, MediaRun
from sm64_events.replay.packetmux import EncodedPicture, NativeFormat, PacketFragmentMux
from sm64_events.replay.recorder import ReplayRecorder
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.window import WindowInfo

HZ, RATE, WIDTH, HEIGHT = 90000, 48000, 160, 96
RESET = 56 * HZ + HZ // 5
END = RESET + 2 * HZ
ORIGIN = 1000.0
WIN = WindowInfo(hwnd=123, title="isolated long-idle fixture", pid=42, visible=True)


def utc(ticks):
    return datetime.fromtimestamp(ORIGIN + ticks / HZ, timezone.utc)


class Source(GpuCapture):
    """Keep real demand checks and paired sink; replace native worker start."""

    def __init__(self):
        super().__init__(WIN.pid, US, nominal_rate=30)
        self.starts = 0

    def start(self, on_frame, on_stopped):
        self.starts += 1


class Audio:
    mode = "process"

    def start(self, on_pcm):
        pass

    def stop(self):
        pass


class Lock:
    def close(self):
        pass


class ForkOutput(io.RawIOBase):
    def __init__(self, captured, reference):
        self.captured, self.reference = captured, reference
        self.bytes = 0

    def writable(self):
        return True

    def write(self, data):
        for offset in range(0, len(data), 317):
            part = data[offset:offset + 317]
            self.captured.feed(part)
            self.reference.feed(part)
        self.bytes += len(data)
        return len(data)


def pixels(number, frame, pad_x):
    """Three independently readable bands: occurrence, raw frame, stick X."""
    image = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
    image[:, :, 3] = 255
    for band, value in enumerate((number, frame & 255, pad_x + 128)):
        for bit in range(8):
            image[band * 32:(band + 1) * 32,
                  bit * 20:(bit + 1) * 20, :3] = 255 if value & (1 << bit) else 0
    return image


def read_video(path, *, first_picture=None, reset_picture=None, reset_tick=None):
    decoded = []
    with av.open(str(path)) as media:
        media.streams.video[0].thread_count = 1
        for frame in media.decode(video=0):
            tick = round(frame.pts * frame.time_base * HZ)
            if not decoded and first_picture is not None:
                frame.to_image().save(first_picture)
            if tick == reset_tick and reset_picture is not None:
                frame.to_image().save(reset_picture)
            gray = frame.to_ndarray(format="gray")
            bands = [sum(1 << bit for bit in range(8)
                         if gray[band * 32 + 8:band * 32 + 24,
                                 bit * 20 + 6:bit * 20 + 14].mean() > 128)
                     for band in range(3)]
            decoded.append((tick, bands,
                            hashlib.sha256(frame.to_ndarray(format="yuv420p")).hexdigest()))
    return decoded


def read_audio(path):
    with av.open(str(path)) as media:
        media.streams.audio[0].thread_count = 1
        decoded = [(round(frame.pts * frame.time_base * RATE), frame.to_ndarray())
                   for frame in media.decode(audio=0)]
    assert decoded
    for (start, data), (following, _) in zip(decoded, decoded[1:], strict=False):
        assert following == start + data.shape[1], "AAC clock hole/overlap"
    return decoded[0][0], np.concatenate([data for _, data in decoded], axis=1)


def tone(first, count):
    sample = np.arange(first, first + count)
    envelope = np.where((sample // 4800) % 2 == 0, 0.7, 0.3)
    left = np.rint(10000 * envelope * np.sin(sample * (2 * np.pi * 440 / RATE)))
    right = np.rint(9000 * envelope * np.sin(sample * (2 * np.pi * 660 / RATE)))
    return np.column_stack((left, right)).astype("<i2").tobytes()


def export(media, start, end, path):
    with media.open(utc(start), utc(end)) as (virtual, result, descriptor):
        path.write_bytes(b"".join(virtual.chunks()))
        return result, descriptor


class LegacySource:
    """The legacy producer keeps sending pictures during explicit pause."""

    def set_idle_check(self, check):
        self.idle_check = check

    def start(self, on_frame, on_stopped):
        pass

    def stop(self):
        pass


class LegacySink:
    def publish_fragments(self, publish):
        self.publish = publish
        return True

    def start(self):
        pass

    def stop(self):
        pass


def prepared_recorder(tmp_path, patch, pre_pad_s, *, legacy=False):
    # Patch only this module's bindings, never the global time module used by
    # the shared resource budget or any actual application.
    import sys
    recorder_module = sys.modules[ReplayRecorder.__module__]
    clock = SimpleNamespace(tick=0)

    class DateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc(clock.tick)

    patch.setattr(recorder_module, "time", SimpleNamespace(monotonic=lambda: clock.tick / HZ))
    patch.setattr(recorder_module, "datetime", DateTime)
    source = LegacySource() if legacy else Source()
    cfg = ReplayConfig(scratch_dir=tmp_path / "bounded", pre_pad_s=pre_pad_s,
                       post_pad_s=1, max_buffer_bytes=2 * 1024**2, picture_feed=True)
    sink_factory = (lambda *args: LegacySink()) if legacy else None
    rec = ReplayRecorder(cfg, lambda title: WIN, lambda win: source, lambda pid: Audio(),
                         codec="h264_nvenc", recorder_lock_factory=Lock,
                         video_sink_factory=sink_factory,
                         clock_factory=lambda: CaptureClock(0, utc(0)))
    rec._begin_capture(WIN)
    return clock, rec, source, cfg


def fixture_encoder():
    encoder = av.CodecContext.create("libx264", "w")
    encoder.width, encoder.height, encoder.pix_fmt = WIDTH, HEIGHT, "yuv420p"
    encoder.time_base, encoder.framerate = MEDIA_TIME_BASE, Fraction(30)
    encoder.thread_count, encoder.max_b_frames, encoder.gop_size = 1, 0, 1000
    encoder.options = {"preset": "ultrafast", "tune": "zerolatency", "crf": "18",
                       "x264-params": "scenecut=0:open-gop=0:keyint=1000:min-keyint=1000"}
    encoder.open()
    return encoder


class Recording:
    """One owned recording and its same-byte unfiltered comparison archive."""

    def __init__(self, tmp_path, patch, pre_pad_s, *, legacy=False):
        self.directory = tmp_path
        self.clock, self.rec, self.source, self.cfg = prepared_recorder(
            tmp_path, patch, pre_pad_s, legacy=legacy)
        self.ownership_bytes = self.rec.ring.total_bytes
        self.run = MediaRun("idle-attempt-one-encoder-run", ORIGIN)
        reference_ring = SegmentRing(None, 16 * 1024**2, scratch_root=tmp_path / "reference")
        self.reference = FragmentMedia(tmp_path / "reference", reference_ring, PictureLedger())
        self.archive = self.rec.fragments.create(self.run, (WIDTH, HEIGHT))
        self.reference_archive = self.reference.create(self.run, (WIDTH, HEIGHT))
        self.output = ForkOutput(self.archive, self.reference_archive)
        self.mux = PacketFragmentMux(self.output, NativeFormat("h264", WIDTH, HEIGHT, 30), self.run,
                                    audio_rate=RATE, audio_bitrate=128000,
                                    packet_limit=64 * 1024, pcm_limit=960 * 4)
        self.encoder = fixture_encoder()
        self.expected = {}
        self.pcm_cursor = 0
        self.peak_bytes = self.peak_samples = self.peak_extents = 0
        self.first_reset_demand = None
        self.automatic_idle_samples = []

    def observe_activity(self, tick):
        self.clock.tick = tick
        self.rec._maybe_idle_pause()
        if tick == RESET:
            self.first_reset_demand = self.source.want_capture()
            assert self.rec.is_idle(), "fixture did not reach long automatic idle"
        # Input observation trails the actual reset by three source pictures.
        if tick >= RESET + 9000:
            self.rec.set_player_active()
        if self.rec.is_idle():
            self.automatic_idle_samples.append(tick)

    def picture(self, number, tick, duration):
        reset_index = round((tick - RESET) / 3000)
        frame = 1000 + number if tick < RESET else reset_index
        pad_x = 0 if tick < RESET else 84 - reset_index % 16
        image = pixels(number, frame, pad_x)
        source_id = f"gpu:idle-fixture:1:1:{number + 1}"
        stamp = {"exact": True, "source_id": source_id, "pad": [pad_x, -5, 32768]}
        assert self.rec.ledger.observe(image, ORIGIN + tick / HZ, frame, stamp)
        frame_image = av.VideoFrame.from_ndarray(image, format="bgra")
        frame_image.pts, frame_image.time_base = tick, MEDIA_TIME_BASE
        if tick % (2 * HZ) == 0:
            frame_image.pict_type = av.video.frame.PictureType.I
        packets = self.encoder.encode(frame_image)
        assert len(packets) == 1 and packets[0].pts == tick and packets[0].dts == tick
        packet = packets[0]
        self.mux.write_video(EncodedPicture(number + 1, tick, duration,
                                            packet.is_keyframe, bytes(packet)))
        self.rec.ledger.mark_fed(ORIGIN + tick / HZ, ORIGIN + tick / HZ,
                                media_run=self.run, pts=tick, source_id=source_id)
        self.expected[tick] = dict(number=number, frame=frame, pad_x=pad_x, source_id=source_id)
        self.audio_through(round((tick + duration) / HZ * RATE))
        self.measure()

    def audio_through(self, end):
        while self.pcm_cursor < end:
            count = min(960, end - self.pcm_cursor)
            self.mux.write_pcm(tone(self.pcm_cursor, count),
                               round(ORIGIN * 1e6 + self.pcm_cursor * 1e6 / RATE))
            self.pcm_cursor += count

    def measure(self):
        self.rec.fragments.maintain()
        self.peak_bytes = max(self.peak_bytes, self.rec.ring.total_bytes)
        self.peak_samples = max(self.peak_samples, self.archive.sample_count)
        self.peak_extents = max(self.peak_extents, len(self.archive._extents))

    def finish(self):
        assert not self.encoder.encode(), "fixture unexpectedly buffered video"
        self.mux.close()
        self.archive.finish()
        self.reference_archive.finish()
        self.measure()
        assert self.archive.error is None and self.reference_archive.error is None
        self.rec.ledger.flush()

    def close(self):
        if not self.mux.closed:
            self.mux.abort()
        self.archive.finish()
        self.reference_archive.finish()
        self.rec.stop(cleanup=False)


def compare_window(recording, start, end, *, older=False):
    directory = recording.directory
    selected = directory / ("older.mp4" if older else "selected.mp4")
    reference = directory / ("older-oracle.mp4" if older else "oracle.mp4")
    result, descriptor = export(recording.rec.fragments, start, end, selected)
    oracle, _ = export(recording.reference, start, end, reference)
    actual_video = read_video(selected,
        first_picture=directory / ("older-first-picture.png" if older else "first-picture.png"),
        reset_picture=None if older else directory / "first-reset-picture.png",
        reset_tick=None if older else RESET - descriptor["start"])
    oracle_video = read_video(reference,
        first_picture=None if older else directory / "oracle-first-picture.png")
    audio_start, audio = read_audio(selected)
    oracle_audio_start, oracle_audio = read_audio(reference)
    return dict(result=result, oracle=oracle, descriptor=descriptor,
                actual_video=actual_video, oracle_video=oracle_video,
                audio_start=audio_start, oracle_audio_start=oracle_audio_start,
                audio_shape=list(audio.shape), oracle_audio_shape=list(oracle_audio.shape),
                video_equal=actual_video == oracle_video,
                audio_equal=(audio_start == oracle_audio_start and np.array_equal(audio, oracle_audio)))


def resource_limits(recording):
    extent_seconds = max(2, 2 * math.ceil(recording.archive._extent_ticks / (2 * HZ)))
    active_extents = math.ceil((recording.automatic_idle_samples[0] / HZ) / extent_seconds)
    extent_bound = active_extents + math.ceil(recording.cfg.pre_pad_s / extent_seconds) + 3
    # Aligned video/AAC samples plus crossing packets in retained extents.
    samples_per_extent = math.ceil(extent_seconds * (RATE / 1024 + 30)) + 4
    return dict(ring_bytes=recording.cfg.max_buffer_bytes, index_samples=recording.archive._index_limit,
                extent_ticks=recording.archive._extent_ticks, derived_extent_bound=extent_bound,
                derived_index_bound=extent_bound * samples_per_extent,
                ledger_rows=recording.rec.ledger._rows.maxlen, ledger_feeds=recording.rec.ledger._feeds.maxlen)


def verify_reader_cleanup(recording, descriptor):
    with recording.rec.fragments.read(descriptor) as leased:
        before = b"".join(leased.chunks())
        recording.rec.ring.set_limits(None, 0)
        assert recording.rec.ring.total_bytes > 0
        assert b"".join(leased.chunks()) == before
    return dict(bytes_after_last_reader=recording.rec.ring.total_bytes,
                protected_after_last_reader=len(recording.rec.ring.protected_paths()),
                ownership_bytes=recording.ownership_bytes,
                media_groups_after_last_reader=sum(group.startswith("fragments:")
                    for group in recording.rec.ring.temporary_groups()),
                media_files_after_last_reader=len(list(recording.cfg.scratch_dir.glob("fragments_*.bin"))))


def coverage_report(recording, ticks):
    r = recording
    requested_start = RESET - round(r.cfg.pre_pad_s * HZ)
    current = compare_window(r, requested_start, END)
    older = compare_window(r, 0, HZ, older=True)
    result, oracle = current["result"], current["oracle"]
    rows = r.rec.ledger.rows_between(ORIGIN, ORIGIN + 100)
    feeds = r.rec.ledger.feeds_between(ORIGIN, ORIGIN + 100)
    mapped, _, stats = feed_map(result.source_pts, r.run.id, rows, feeds,
        lambda row: {k: row[k] for k in ("frame", "pad", "source_id")})
    report = dict(pre_pad_s=r.cfg.pre_pad_s, simulated_seconds=(ticks[-1] + 3000) / HZ,
        automatic_idle_seconds=(r.automatic_idle_samples[-1] - r.automatic_idle_samples[0]) / HZ,
        first_reset_demand=r.first_reset_demand, source_starts=r.source.starts,
        source_count=len(ticks), audio_samples=r.pcm_cursor, encoded_bytes=r.output.bytes,
        peak_bytes=r.peak_bytes, peak_index_samples=r.peak_samples, peak_extents=r.peak_extents,
        retained_ledger_rows=len(rows), retained_feeds=len(feeds),
        retained_extents=len(r.archive._extents), retained_bytes=r.rec.ring.total_bytes,
        limits=resource_limits(r), cached_rows=len(r.rec.ledger._rows), cached_feeds=len(r.rec.ledger._feeds),
        older_active_video_equal=older["video_equal"],
        older_active_pts_equal=older["result"].source_pts == older["oracle"].source_pts,
        older_active_audio_equal=older["audio_equal"],
        requested_start=requested_start, actual_start=current["descriptor"]["start"],
        oracle_start=oracle.source_pts[0], reset_tick=RESET,
        first_actual=current["actual_video"][0], first_oracle=current["oracle_video"][0],
        video_equal=current["video_equal"], audio_equal=current["audio_equal"],
        audio_start=current["audio_start"], oracle_audio_start=current["oracle_audio_start"],
        audio_shape=current["audio_shape"], oracle_audio_shape=current["oracle_audio_shape"],
        source_pts=result.source_pts, oracle_pts=oracle.source_pts, mapping=mapped, feed_stats=stats,
        expected={str(t): r.expected[t] for t in r.expected if t >= oracle.source_pts[0]},
        decoded_bands=[bands for _, bands, _ in current["actual_video"]])
    report.update(verify_reader_cleanup(r, current["descriptor"]))
    return report


def record(tmp_path, patch, pre_pad_s):
    recording = Recording(tmp_path, patch, pre_pad_s)
    ticks = [*range(0, 56 * HZ + 1, HZ // 2),
             *(RESET + number * 3000 for number in range(61))]
    try:
        for number, tick in enumerate(ticks):
            recording.observe_activity(tick)
            duration = ticks[number + 1] - tick if number + 1 < len(ticks) else 3000
            recording.picture(number, tick, duration)
        recording.finish()
        report = coverage_report(recording, ticks)
        (tmp_path / "coverage.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        recording.close()


@pytest.fixture(scope="module", params=[0, 3, 10], ids=lambda value: f"prepad-{value}")
def idle_attempt(request, tmp_path_factory):
    directory = tmp_path_factory.mktemp(f"idle-attempt-prepad-{request.param}")
    with pytest.MonkeyPatch.context() as patch:
        report = record(directory, patch, request.param)
    print("R33 coverage report:", directory / "coverage.json")
    return report


def test_automatic_idle_does_not_close_source_before_immediate_reset(idle_attempt):
    assert idle_attempt["automatic_idle_seconds"] > 4 * idle_attempt["limits"]["extent_ticks"] / HZ
    assert idle_attempt["source_starts"] == 1
    assert idle_attempt["first_reset_demand"], "automatic idle disabled first-reset capture demand"


def test_idle_preroll_first_reset_pixels_inputs_and_audio_match_source(idle_attempt):
    r = idle_attempt
    assert r["actual_start"] == r["oracle_start"], "idle retention discarded requested preroll"
    assert r["video_equal"], "virtual clip changed source picture pixels/ticks"
    assert r["audio_equal"], "idle eviction lost or changed AAC preroll/clock"
    assert r["older_active_video_equal"] and r["older_active_pts_equal"]
    assert r["older_active_audio_equal"], "automatic idle evicted earlier active history"
    assert r["feed_stats"]["matched"] == len(r["source_pts"])
    assert r["reset_tick"] in r["source_pts"], "first actual reset picture is absent"
    for tick, bands, mapped in zip(r["source_pts"], r["decoded_bands"], r["mapping"], strict=True):
        expected = r["expected"][str(tick)]
        assert bands == [expected["number"], expected["frame"] & 255, expected["pad_x"] + 128]
        assert mapped == {"frame": expected["frame"], "pad": [expected["pad_x"], -5, 32768],
                          "source_id": expected["source_id"]}


def test_long_idle_media_and_reader_resources_remain_bounded(idle_attempt):
    r = idle_attempt
    # The bounds below only mean something over a long run: the fixture's own
    # cadence (half-second ticks across ~56 s, then 61 reset pictures) decides
    # the exact number, so require the scale, not the count.
    assert r["source_count"] >= 150, "the fixture stopped simulating a long idle"
    assert r["peak_bytes"] <= r["limits"]["ring_bytes"]
    assert r["peak_index_samples"] <= min(r["limits"]["index_samples"], r["limits"]["derived_index_bound"])
    assert r["peak_extents"] <= r["limits"]["derived_extent_bound"]
    assert r["cached_rows"] <= r["limits"]["ledger_rows"]
    assert r["cached_feeds"] <= r["limits"]["ledger_feeds"]
    assert r["bytes_after_last_reader"] == r["ownership_bytes"]
    assert r["protected_after_last_reader"] == r["media_groups_after_last_reader"] == 0
    assert r["media_files_after_last_reader"] == 0


def test_explicit_pause_keeps_prior_auto_idle_tail_but_discards_new_legacy_extents(tmp_path, monkeypatch):
    recording = Recording(tmp_path, monkeypatch, 3, legacy=True)
    rec = recording.rec
    explicit_pause = 12 * HZ + HZ // 2
    automatic_since = None
    try:
        for number, tick in enumerate(range(0, 34 * HZ + 1, HZ // 2)):
            recording.observe_activity(tick)
            if tick == explicit_pause:
                automatic_since = rec._idle_since
                assert automatic_since is not None and automatic_since < utc(explicit_pause)
                rec.set_session_paused(True)
            if tick == 14 * HZ:
                rec.set_session_paused(True)  # repeated pause must not move its boundary
                rec.set_player_active()  # input cannot resume explicit pause
            recording.picture(number, tick, HZ // 2)
        recording.finish()
        assert rec._idle_since == automatic_since
        assert rec._session_paused_since == utc(explicit_pause)
        assert rec._fragment_idle_window() is None
        tail = compare_window(recording, 10 * HZ, 12 * HZ)
        assert tail["video_equal"] and tail["audio_equal"]
        assert tail["result"].source_pts == tail["oracle"].source_pts
        # A continuing legacy source produced these bytes, but the completed
        # extents were born after explicit pause and must not be exportable.
        with pytest.raises((LookupError, ValueError)):
            with rec.fragments.open(utc(18 * HZ), utc(20 * HZ)):
                pytest.fail("pause-born legacy footage remains exportable")
        with recording.reference.open(utc(18 * HZ), utc(20 * HZ)) as (media, _, _):
            assert b"".join(media.chunks()), "negative query lacked a real source witness"
        rec.set_session_paused(False)
        assert rec._session_paused_since is None
        assert not rec._fragment_paused(utc(18 * HZ))
        report = dict(automatic_since=automatic_since.isoformat(),
                      explicit_pause=utc(explicit_pause).isoformat(),
                      tail_source_pts=tail["result"].source_pts,
                      tail_video_equal=tail["video_equal"], tail_audio_equal=tail["audio_equal"],
                      paused_extents_discarded=True)
        (tmp_path / "explicit-pause.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        recording.close()
