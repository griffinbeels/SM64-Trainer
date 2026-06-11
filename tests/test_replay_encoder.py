from datetime import datetime, timedelta, timezone

import av
import numpy as np
import pytest

from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.encoder import SegmentWriter, pick_video_codec

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
CFG = ReplayConfig(fps=30)  # tests pin the 30fps math; capture default is 60
CLK = CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0)


def frame(i):
    arr = np.zeros((480, 640, 4), dtype=np.uint8)
    arr[:, :, 0] = i % 256  # vary content so the encoder has work
    return arr


def make_writer(tmp_path, collected):
    return SegmentWriter(cfg=CFG, clock=CLK, out_dir=tmp_path,
                         codec="libx264", on_segment=collected.append)


def test_video_rotates_every_segment_and_stamps_utc(tmp_path):
    segs = []
    w = make_writer(tmp_path, segs)
    for i in range(150):                      # 5 s at 30 fps
        w.write_video(frame(i), frame_index=i)
    w.close()
    video = [s for s in segs if s.kind == "video"]
    assert len(video) == 3                    # 2 s + 2 s + 1 s partial
    assert video[0].utc_start == T0
    assert video[0].utc_end == T0 + timedelta(seconds=2)
    assert video[2].utc_end == T0 + timedelta(seconds=5)
    with av.open(str(video[0].path)) as c:    # decodable, full GOP
        # Exactly 60: zerolatency (libx264) / bf=0 (nvenc) mean no encoder
        # delay, and _close_video_segment drains via encode(None). The
        # extractor maps frame i to utc_start + i/fps, so "exactly
        # seg_frames decodable frames per full segment" IS the contract.
        assert len([f for f in c.decode(video=0)]) == 60


def test_audio_chunks_carry_sample_accurate_ranges(tmp_path):
    segs = []
    w = make_writer(tmp_path, segs)
    w.start_audio(t0_utc=T0)
    pcm = np.zeros((48000, 2), dtype=np.int16)   # 1 s of silence
    for _ in range(5):
        w.write_audio(pcm)
    w.close()
    audio = [s for s in segs if s.kind == "audio"]
    assert len(audio) == 3                    # 2 s + 2 s + 1 s partial
    assert audio[0].utc_start == T0
    assert audio[1].utc_start == T0 + timedelta(seconds=2)
    assert audio[0].size_bytes == 48000 * 2 * 2 * 2  # 2 s * stereo * s16


def test_dimension_change_rotates_segment(tmp_path):
    segs = []
    w = make_writer(tmp_path, segs)
    for i in range(30):
        w.write_video(frame(i), frame_index=i)
    big = np.zeros((600, 800, 4), dtype=np.uint8)
    w.write_video(big, frame_index=30)        # resize mid-segment
    w.close()
    video = [s for s in segs if s.kind == "video"]
    assert len(video) == 2                    # early rotation at the resize


def test_pick_video_codec_returns_known_codec():
    assert pick_video_codec() in ("h264_nvenc", "libx264")


def test_odd_dimensions_are_cropped_not_fatal(tmp_path):
    segs = []
    w = make_writer(tmp_path, segs)
    odd = np.zeros((479, 641, 4), dtype=np.uint8)
    for i in range(60):
        w.write_video(odd, frame_index=i)
    w.close()
    video = [s for s in segs if s.kind == "video"]
    assert len(video) == 1
    with av.open(str(video[0].path)) as c:
        f = next(iter(c.decode(video=0)))
        assert (f.width, f.height) == (640, 478)   # cropped to even


def test_index_gap_forces_rotation_and_exact_spans(tmp_path):
    segs = []
    w = make_writer(tmp_path, segs)
    for i in range(30):
        w.write_video(frame(i), frame_index=i)      # 0..29
    for i in range(45, 60):
        w.write_video(frame(i), frame_index=i)      # gap: 30..44 missing
    w.close()
    video = [s for s in segs if s.kind == "video"]
    assert len(video) == 2
    assert video[0].utc_start == T0
    assert video[0].utc_end == T0 + timedelta(seconds=1.0)    # 30 frames
    assert video[1].utc_start == T0 + timedelta(seconds=1.5)  # index 45
    assert video[1].utc_end == T0 + timedelta(seconds=2.0)    # 15 frames


def test_audio_before_start_raises(tmp_path):
    w = SegmentWriter(cfg=CFG, clock=CLK, out_dir=tmp_path,
                      codec="libx264", on_segment=lambda s: None)
    with pytest.raises(RuntimeError, match="start_audio"):
        w.write_audio(np.zeros((48000, 2), dtype=np.int16))


def test_backwards_frame_index_is_dropped_not_rotated(tmp_path):
    segs = []
    w = make_writer(tmp_path, segs)
    for i in range(10):
        w.write_video(frame(i), frame_index=i)
    w.write_video(frame(5), frame_index=5)      # duplicate of an old index
    for i in range(10, 60):
        w.write_video(frame(i), frame_index=i)
    w.close()
    video = [s for s in segs if s.kind == "video"]
    assert len(video) == 1                       # no spurious rotation
    assert video[0].utc_start == T0
    assert video[0].utc_end == T0 + timedelta(seconds=2)


def test_nvenc_segments_start_at_pts_zero(tmp_path):
    if pick_video_codec() != "h264_nvenc":
        pytest.skip("nvenc not available")
    segs = []
    w = SegmentWriter(cfg=CFG, clock=CLK, out_dir=tmp_path,
                      codec="h264_nvenc", on_segment=segs.append)
    for i in range(60):
        w.write_video(frame(i), frame_index=i)
    w.close()
    with av.open(str(segs[0].path)) as c:
        assert (c.streams.video[0].start_time or 0) == 0
