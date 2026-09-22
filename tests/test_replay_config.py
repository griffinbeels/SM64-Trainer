"""Test ReplayConfig defaults, construction, and the settings overlay."""
from pathlib import Path

import pytest

from sm64_events.replay import config
from sm64_events.replay.config import (CLIP_MAXRATE, RING_MAXRATE,
                                       ReplayConfig, apply_settings_file,
                                       forced_idr_args, save_settings,
                                       validate_settings, video_quality_args)


def test_defaults_wire_paths_and_stay_coherent():
    """Where the ring lives, which window it captures and at what audio rate
    are wiring other modules read back; they belong here.

    The TUNING knobs do not. `pre_pad_s` and `post_pad_s` are his to change in
    the replay settings (``save_settings`` writes exactly those, with
    ``retention_s`` and the cap), and `fps`/`segment_s` are shipped defaults --
    pinning a shipped default's contents turns a preference change into a red
    build (CLAUDE.md domain contract 7). What IS checked is that the shipped
    set is coherent: `validate_settings` accepts it and the rates are usable."""
    cfg = ReplayConfig()
    assert cfg.retention_s is None            # None = whole session (spec default)
    validate_settings(cfg.retention_s, cfg.max_buffer_bytes, cfg.pre_pad_s, cfg.post_pad_s)
    assert cfg.fps > 0 and cfg.segment_s > 0
    assert cfg.save_root == Path("replays")
    assert cfg.scratch_dir == Path("data") / "replay_buffer"
    assert cfg.window_title == "Project64"
    assert cfg.audio_rate == 48000


def test_retention_minutes_constructor():
    assert ReplayConfig(retention_s=600.0).retention_s == 600.0


def test_settings_overlay_round_trip(tmp_path):
    cfg = ReplayConfig(settings_path=tmp_path / "rs.json")
    assert apply_settings_file(cfg) is cfg            # no file -> defaults
    save_settings(cfg.settings_path, 600.0, 5 * 1024**3, 5.0, 4.0)
    out = apply_settings_file(cfg)
    assert out.retention_s == 600.0
    assert out.max_buffer_bytes == 5 * 1024**3
    assert out.pre_pad_s == 5.0 and out.post_pad_s == 4.0
    assert out.fps == cfg.fps                         # only the knobs move


def test_settings_overlay_ignores_corrupt_and_invalid(tmp_path):
    """The server must always start: bad overlay files lose to defaults."""
    cfg = ReplayConfig(settings_path=tmp_path / "rs.json")
    cfg.settings_path.write_text("{not json")
    assert apply_settings_file(cfg) is cfg
    cfg.settings_path.write_text('{"retention_s": 1, "max_buffer_bytes": 5}')
    assert apply_settings_file(cfg) is cfg            # out of range -> defaults


def test_validate_settings_bounds():
    validate_settings(None, 1024**3)                  # whole session, 1 GiB
    validate_settings(60.0, 20 * 1024**3)
    validate_settings(None, 1024**3, 0.0, 10.0)       # pad extremes are legal
    with pytest.raises(ValueError):
        validate_settings(5.0, 1024**3)               # retention below 60 s
    with pytest.raises(ValueError):
        validate_settings(None, 100)                  # cap below 1 GiB
    with pytest.raises(ValueError):
        validate_settings(None, 1024**3, 11.0, 2.0)   # pre pad above 10 s
    with pytest.raises(ValueError):
        validate_settings(None, 1024**3, 3.0, -1.0)   # negative post pad


def test_every_stage_pins_a_quality_target():
    """THE blurry-recording regression: an encoder with no rate control falls
    back to ffmpeg's ~2 Mbps default, which crushed every saved clip no matter
    how good its ring segment was (12.5 Mbps in -> 2.1 Mbps out, measured
    2026-07-23). Every supported codec and stage must name a quality target."""
    for codec, quality_flag in (("h264_nvenc", "-cq"), ("av1_nvenc", "-cq"),
                                ("libx264", "-crf"),
                                ("h264_amf", "-qp_p"), ("h264_qsv", "-global_quality")):
        for stage, maxrate in (("realtime", RING_MAXRATE),
                               ("offline", CLIP_MAXRATE)):
            args = video_quality_args(codec, stage, maxrate)
            assert quality_flag in args, f"{codec}/{stage} has no quality target"
            assert args[args.index(quality_flag) + 1].isdigit()
            assert ("-quality" if codec == "h264_amf" else "-preset") in args


def test_nvenc_quality_target_is_not_overridden_by_a_bitrate():
    """NVENC only honours -cq when the bitrate target is explicitly zero;
    with a bitrate set it reverts to average-bitrate VBR and the quality
    target is silently ignored."""
    args = video_quality_args("h264_nvenc", "offline", CLIP_MAXRATE)
    assert args[args.index("-b:v") + 1] == "0"
    assert args[args.index("-rc") + 1] == "vbr"
    assert args[args.index("-maxrate") + 1] == CLIP_MAXRATE


def test_offline_stage_may_spend_more_time_than_realtime():
    """The ring encode must beat 1/fps per frame; the clip cut is offline, so
    the presets are allowed to differ — but only in speed, never in target."""
    ring = video_quality_args("h264_nvenc", "realtime", RING_MAXRATE)
    clip = video_quality_args("h264_nvenc", "offline", CLIP_MAXRATE)
    assert ring[ring.index("-preset") + 1] != clip[clip.index("-preset") + 1]
    assert ring[ring.index("-cq") + 1] == clip[clip.index("-cq") + 1]


def test_unknown_codec_adds_no_flags():
    assert video_quality_args("hevc_qsv", "offline", CLIP_MAXRATE) == []


def test_the_ring_and_the_archive_own_their_av1_targets_separately(monkeypatch):
    """Recording in AV1 and re-encoding a saved replay in AV1 are independent
    decisions that currently agree on 36. Re-tuning the ring must not drag the
    archive with it, and both numbers must be read at the call rather than
    frozen into a table at import (the 2026-09-20 registry regression)."""
    monkeypatch.setattr(config, "VIDEO_AV1_CQ", 30)
    monkeypatch.setattr(config, "ARCHIVE_AV1_CQ", 44)
    ring = video_quality_args("av1_nvenc", "realtime", RING_MAXRATE)
    archive = video_quality_args("av1_nvenc", "archive", CLIP_MAXRATE)
    assert ring[ring.index("-cq") + 1] == "30"
    assert archive[archive.index("-cq") + 1] == "44"


def test_both_nvenc_codecs_force_idr_with_nvidias_own_spelling():
    """A forced key picture must be an independent start for the fragment
    muxer. AMF and QSV spell the option `-forced_idr`; both NVENC encoders
    spell it `-forced-idr`, and av1_nvenc was silently getting neither."""
    assert forced_idr_args("av1_nvenc") == ["-forced-idr", "1"]
    assert forced_idr_args("h264_nvenc") == ["-forced-idr", "1"]
    assert forced_idr_args("libx264") == []
