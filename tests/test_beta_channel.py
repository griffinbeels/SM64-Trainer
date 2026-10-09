"""Packaged beta authority protects public profile/update side effects."""
import json
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.core import build_channel, paths, updater
from sm64_events.server.update_api import create_update_router


def metadata():
    return {
        "schema": 1, "channel": "beta", "build_id": "friends-20261008-a0e59ab9",
        "community_url": "https://community-beta.example.com",
        "supabase_url": "https://beta-project.supabase.co",
        "supabase_publishable_key": "sb_publishable_testpublickey",
        "oauth_client_id": "11111111-2222-4333-8444-555555555555",
    }


@pytest.fixture
def beta(monkeypatch):
    build = build_channel.parse_metadata(metadata())
    monkeypatch.setattr(build_channel, "current_build", lambda: build)
    return build


def test_source_ignores_nearby_metadata_and_channel_environment(monkeypatch, tmp_path):
    (tmp_path / build_channel.CONFIG_FILENAME).write_text(json.dumps(metadata()))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("SM64_BUILD_CHANNEL", "beta")
    assert build_channel.current_build() == build_channel.BuildChannel()


def test_packaged_metadata_selects_identity_without_environment(monkeypatch, tmp_path):
    (tmp_path / build_channel.CONFIG_FILENAME).write_text(json.dumps(metadata()))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    build = build_channel.current_build()
    assert build.channel == "beta" and build.app_dir_name == "SM64TrainerBeta"
    assert build.display_name == "SM64 Trainer Beta" and build.default_port == 8072
    assert build.updates_enabled is False


def test_absent_metadata_preserves_stable_and_present_invalid_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "absent"), raising=False)
    assert build_channel.current_build() == build_channel.BuildChannel()
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / build_channel.CONFIG_FILENAME).write_text("{broken")
    monkeypatch.setattr(sys, "_MEIPASS", str(invalid))
    with pytest.raises(ValueError, match="packaged build metadata"):
        build_channel.current_build()


def test_beta_executable_cannot_fall_back_to_stable_after_bundle_metadata_loss(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "SM64TrainerBeta.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    with pytest.raises(ValueError, match="beta metadata"):
        build_channel.current_build()


@pytest.mark.parametrize("changes", [
    {"schema": True}, {"schema": 2}, {"channel": "preview"},
    {"unsupported_field": "never-echo-this"}, {"build_id": "../../public"},
    {"community_url": "http://community.example.com"},
    {"community_url": "https://user@example.com"},
    {"supabase_url": "https://beta.supabase.co?invalid=never-echo-this"},
    {"supabase_publishable_key": "invalid-nonpublic-key"},
    {"oauth_client_id": "unregistered-client"}, {"community_url": []},
])
def test_invalid_or_unsupported_metadata_refused_without_echo(changes):
    with pytest.raises(ValueError) as failure:
        build_channel.parse_metadata({**metadata(), **changes})
    assert "never-echo-this" not in str(failure.value)


def test_beta_profile_port_and_population_ignore_inherited_public_overrides(beta, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for name in ("SM64_PRACTICE_ROOT", "SM64_GHOST_POPULATION"):
        monkeypatch.setenv(name, str(tmp_path / "SM64Trainer"))
    monkeypatch.setenv("SM64_PORT", "8064")
    root = tmp_path / "SM64TrainerBeta"
    assert paths.data_root() == root
    assert paths.server_port() == 8072
    assert paths.ghost_population_root() == root / "data" / "ghost_population"
    assert paths.practice_root() is None
    for path in (paths.db_path(), paths.instance_lock_path(), paths.replays_root(),
                 paths.pidfile_path(), paths.window_state_path(), paths.logs_dir(),
                 paths.update_state_path(), paths.replay_settings_path()):
        assert path.is_relative_to(root)
    assert paths.capture_layer_settings_path() == tmp_path / "SM64Trainer" / "data" / "capture_layer.json"


def test_beta_never_migrates_or_changes_public_data(beta, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for name in ("sm64_tracker", "SM64Trainer"):
        target = tmp_path / name / "data" / "tracker.db"
        target.parent.mkdir(parents=True)
        target.write_bytes(name.encode())
    paths.migrate_legacy_data_dir()
    assert (tmp_path / "sm64_tracker" / "data" / "tracker.db").read_bytes() == b"sm64_tracker"
    assert (tmp_path / "SM64Trainer" / "data" / "tracker.db").read_bytes() == b"SM64Trainer"
    assert not (tmp_path / "SM64TrainerBeta").exists()


def test_beta_update_http_entry_points_have_no_network_files_threads_or_restart(beta, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("disabled beta updater performed side effects")
    monkeypatch.setenv("SM64_UPDATE_FAKE", "1")
    monkeypatch.setattr(updater, "exe_dir_writable", forbidden)
    monkeypatch.setattr(updater, "sweep_backup", forbidden)
    with monkeypatch.context() as context:
        context.setattr(updater.threading, "Thread", forbidden)
        service = updater.UpdateService(current_version="1.0.0", frozen=True, http=forbidden,
                                        exe_path=tmp_path / "SM64TrainerBeta.exe",
                                        state_path=tmp_path / "update_state.json")
        service.startup_maintenance(str(tmp_path / "public-bootstrap.exe"))
        assert service.begin_apply(forbidden)["state"] == "error"
    app = FastAPI()
    app.include_router(create_update_router(service, forbidden))
    with TestClient(app) as client:
        for query in ("", "?force=true"):
            body = client.get("/api/update/status" + query).json()
            assert body["state"] == "disabled" and body["disabled_reason"]
            assert not body["update_available"] and not body["writable"]
        assert client.post("/api/update/apply").json()["state"] == "error"
        client.post("/api/update/skip", json={"version": "9.9.9"})
    assert service._check(force=True) is None
    assert list(tmp_path.iterdir()) == []


def test_entry_refuses_bad_packaged_identity_before_dispatch(monkeypatch):
    import gui_entry
    def invalid():
        raise ValueError("invalid packaged build metadata")
    monkeypatch.setattr(build_channel, "current_build", invalid)
    monkeypatch.setattr(sys, "argv", ["SM64TrainerBeta.exe", "--some-helper"])
    with pytest.raises(ValueError, match="packaged build metadata"):
        gui_entry.main()
