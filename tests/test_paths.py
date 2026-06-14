# tests/test_paths.py
"""Runtime path resolution: cwd-relative from source (identical to the
historical layout), %LOCALAPPDATA% when frozen into an exe."""
import sys
from pathlib import Path

from sm64_events.core import paths


def test_source_paths_match_historical_relative_layout(monkeypatch):
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert paths.db_path() == Path("data") / "tracker.db"
    assert paths.instance_lock_path() == Path("data") / "tracker.lock"
    assert paths.replay_scratch_dir() == Path("data") / "replay_buffer"
    assert paths.replays_root() == Path("replays")
    assert paths.replay_settings_path() == Path("data") / "replay_settings.json"


def test_frozen_paths_live_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = tmp_path / "sm64_tracker"
    assert paths.data_root() == root
    assert paths.db_path() == root / "data" / "tracker.db"
    assert paths.instance_lock_path() == root / "data" / "tracker.lock"
    assert paths.replays_root() == root / "replays"
    assert paths.pidfile_path() == root / "server.pid"
    assert paths.window_state_path() == root / "window.json"


def test_bundled_ffmpeg_none_from_source(monkeypatch):
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert paths.bundled_ffmpeg() is None


def test_bundled_ffmpeg_found_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    (tmp_path / "ffmpeg.exe").write_text("x")
    assert paths.bundled_ffmpeg() == str(tmp_path / "ffmpeg.exe")
