"""Test ReplayConfig defaults and construction."""
from pathlib import Path

from sm64_events.replay.config import ReplayConfig


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
