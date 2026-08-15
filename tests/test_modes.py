"""Mode + version config: the EMU/N64 overlay (core/modes.py).

Same resilience contract as replay/config.py::apply_settings_file: absent,
corrupt, or unknown-valued files lose to per-field defaults with one warning
line — the server must always start, so load never raises."""
import dataclasses
import json
import logging

import pytest

from sm64_events.core.modes import (GameVersion, ModeConfig, TrackerMode,
                                    effective_version, load_mode_config,
                                    save_mode_config)
from sm64_events.core.paths import mode_settings_path


def warnings_from(caplog):
    return [r for r in caplog.records
            if r.name == "sm64.modes" and r.levelno >= logging.WARNING]


def test_defaults_are_emu_auto():
    cfg = ModeConfig()
    assert cfg.mode is TrackerMode.EMU
    assert cfg.version is GameVersion.AUTO


def test_mode_config_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ModeConfig().mode = TrackerMode.N64


def test_round_trip(tmp_path):
    path = tmp_path / "tracker_mode.json"
    save_mode_config(ModeConfig(TrackerMode.N64, GameVersion.JP), path)
    assert load_mode_config(path) == ModeConfig(TrackerMode.N64, GameVersion.JP)


def test_full_matrix_round_trips(tmp_path):
    """Every (mode x version) pair stores and loads — JP+EMU included. Whether
    a pair is SUPPORTED is the wiring's concern; the setting must never refuse
    to represent it."""
    path = tmp_path / "tracker_mode.json"
    for mode in TrackerMode:
        for version in GameVersion:
            save_mode_config(ModeConfig(mode, version), path)
            assert load_mode_config(path) == ModeConfig(mode, version)


def test_save_creates_parent_dirs(tmp_path):
    path = tmp_path / "data" / "tracker_mode.json"
    save_mode_config(ModeConfig(), path)
    assert load_mode_config(path) == ModeConfig()


def test_absent_file_is_silent_defaults(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    assert load_mode_config(tmp_path / "missing.json") == ModeConfig()
    assert warnings_from(caplog) == []       # first run is normal, not a fault


def test_corrupt_file_defaults_with_one_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    path = tmp_path / "tracker_mode.json"
    path.write_text("{not json")
    assert load_mode_config(path) == ModeConfig()
    assert len(warnings_from(caplog)) == 1


def test_non_object_json_defaults_with_one_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    path = tmp_path / "tracker_mode.json"
    path.write_text(json.dumps(["n64", "jp"]))
    assert load_mode_config(path) == ModeConfig()
    assert len(warnings_from(caplog)) == 1


def test_unknown_mode_defaults_mode_only(tmp_path, caplog):
    """Per-field, never whole-file: a bad mode string must not eat the version
    stored beside it."""
    caplog.set_level(logging.WARNING)
    path = tmp_path / "tracker_mode.json"
    path.write_text(json.dumps({"mode": "gamecube", "version": "jp"}))
    assert load_mode_config(path) == ModeConfig(TrackerMode.EMU, GameVersion.JP)
    assert len(warnings_from(caplog)) == 1


def test_unknown_version_defaults_version_only(tmp_path):
    path = tmp_path / "tracker_mode.json"
    path.write_text(json.dumps({"mode": "n64", "version": "pal"}))
    assert load_mode_config(path) == ModeConfig(TrackerMode.N64, GameVersion.AUTO)


def test_two_unknown_fields_still_one_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    path = tmp_path / "tracker_mode.json"
    path.write_text(json.dumps({"mode": "gamecube", "version": "pal"}))
    assert load_mode_config(path) == ModeConfig()
    assert len(warnings_from(caplog)) == 1


def test_missing_keys_keep_defaults_silently(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    path = tmp_path / "tracker_mode.json"
    path.write_text(json.dumps({"version": "us"}))
    assert load_mode_config(path) == ModeConfig(TrackerMode.EMU, GameVersion.US)
    assert warnings_from(caplog) == []


def test_stored_strings_survive_case_and_whitespace(tmp_path):
    """A hand-edited file with 'N64' must not silently fall back to EMU."""
    path = tmp_path / "tracker_mode.json"
    path.write_text(json.dumps({"mode": " N64 ", "version": "JP"}))
    assert load_mode_config(path) == ModeConfig(TrackerMode.N64, GameVersion.JP)


def test_default_path_is_the_overlay(monkeypatch, tmp_path):
    """No-arg save/load speak to mode_settings_path() — the one path source."""
    from sm64_events.core import modes
    path = tmp_path / "tracker_mode.json"
    monkeypatch.setattr(modes, "mode_settings_path", lambda: path)
    save_mode_config(ModeConfig(TrackerMode.N64, GameVersion.US))
    assert load_mode_config() == ModeConfig(TrackerMode.N64, GameVersion.US)
    assert path.exists()


def test_mode_settings_path_lives_in_data_dir():
    assert mode_settings_path().name == "tracker_mode.json"
    assert mode_settings_path().parent.name == "data"

def test_effective_version_explicit_wins_over_detected():
    assert effective_version(ModeConfig(version=GameVersion.JP), detected="us") == "jp"
    assert effective_version(ModeConfig(version=GameVersion.US), detected="jp") == "us"


def test_effective_version_auto_takes_detected_else_us():
    assert effective_version(ModeConfig(version=GameVersion.AUTO), detected="jp") == "jp"
    assert effective_version(ModeConfig(version=GameVersion.AUTO)) == "us"


def test_effective_version_rejects_junk_detected():
    assert effective_version(ModeConfig(version=GameVersion.AUTO), detected="pal") == "us"
