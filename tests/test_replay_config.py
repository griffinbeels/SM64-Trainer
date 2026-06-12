"""Test ReplayConfig defaults, construction, and the settings overlay."""
from pathlib import Path

import pytest

from sm64_events.replay.config import (ReplayConfig, apply_settings_file,
                                       save_settings, validate_settings)


def test_defaults_match_spec():
    cfg = ReplayConfig()
    assert cfg.enabled is True
    assert cfg.retention_s is None            # None = whole session (spec default)
    assert cfg.pre_pad_s == 3.0 and cfg.post_pad_s == 2.0
    assert cfg.fps == 60
    assert cfg.segment_s == 2.0
    assert cfg.max_buffer_bytes == 20 * 1024**3
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
