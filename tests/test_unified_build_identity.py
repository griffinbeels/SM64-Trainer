"""Release type never changes a normal install's data or OAuth identity."""
from dataclasses import replace
import pytest

from sm64_events.community.config import CommunityConfig, CALLBACK, LEGACY_CALLBACK
from sm64_events.core import build_channel, paths


def metadata(channel="beta"):
    return {"schema": 2, "installation": "normal", "channel": channel, "build_id": "unified-qa-1",
            "community_url": "https://ghosts.example.com", "supabase_url": "https://project.supabase.co",
            "supabase_publishable_key": "sb_publishable_fixture",
            "oauth_client_id": "11111111-2222-4333-8444-555555555555"}


@pytest.mark.parametrize("channel", ["stable", "beta"])
def test_normal_release_has_same_profile_callback_and_account_namespace(channel, monkeypatch, tmp_path):
    build = build_channel.parse_metadata(metadata(channel))
    assert build.app_dir_name == "SM64Trainer" and build.display_name == "SM64 Trainer"
    assert build.default_port == 8064 and build.callback_url == CALLBACK
    assert build.updates_enabled and build.community_enabled and build.account_namespace == "stable"
    assert build.metadata() == metadata(channel)
    monkeypatch.setattr(build_channel, "current_build", lambda: build)
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("SM64_PORT", "8072")
    assert paths.server_port() == 8064
    assert paths.data_root() == tmp_path / "SM64Trainer"
    assert paths.ghost_population_root().is_relative_to(tmp_path / "SM64Trainer")
    assert paths.capture_layer_settings_path().is_relative_to(tmp_path / "SM64Trainer")


def test_legacy_beta_keeps_its_old_configuration_and_data(monkeypatch, tmp_path):
    old = metadata()
    old.pop("installation")
    old["schema"] = 1
    build = build_channel.parse_metadata(old)
    assert build.legacy_beta and build.metadata() == old
    assert build.callback_url == LEGACY_CALLBACK and build.account_namespace == "beta"
    assert not build.updates_enabled
    monkeypatch.setattr(build_channel, "current_build", lambda: build)
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("SM64_PRACTICE_ROOT", str(tmp_path / "other"))
    (tmp_path / "SM64TrainerBeta").mkdir()
    saved = tmp_path / "SM64TrainerBeta" / "kept.dat"
    saved.write_bytes(b"old-beta-data")
    paths.migrate_legacy_data_dir()
    assert saved.read_bytes() == b"old-beta-data"
    assert paths.data_root() == saved.parent
    assert not (tmp_path / "SM64Trainer").exists()


def test_beta_to_stable_preserves_refresh_token_store_identity():
    from sm64_events.community.vault import RefreshTokenStore
    beta = build_channel.parse_metadata(metadata())
    stable = replace(beta, channel="stable")
    def configured(build):
        return CommunityConfig(public_url=build.community_url, supabase_url=build.supabase_url,
            publishable_key=build.supabase_publishable_key, oauth_client_id=build.oauth_client_id,
            callback_url=build.callback_url, channel=build.account_namespace, enabled=True)
    assert RefreshTokenStore(configured(beta)).target == RefreshTokenStore(configured(stable)).target


@pytest.mark.parametrize("changes", [{"installation": "legacy"}, {"installation": "SM64TrainerBeta"},
    {"installation": None}, {"community_url": ""}, {"oauth_client_id": "wrong"},
    {"supabase_url": "https://project.supabase.co?private-value=1"}])
def test_unified_config_fails_closed(changes):
    with pytest.raises(ValueError) as failure:
        build_channel.parse_metadata({**metadata(), **changes})
    assert "private-value" not in str(failure.value)


def test_legacy_schema_cannot_claim_new_identity():
    with pytest.raises(ValueError):
        build_channel.parse_metadata({**metadata(), "schema": 1})


def test_community_allows_only_exact_registered_callback_paths():
    assert CommunityConfig().callback_url == CALLBACK
    assert CommunityConfig(callback_url=LEGACY_CALLBACK).callback_url == LEGACY_CALLBACK
    for callback in (CALLBACK + "?redirect=evil", CALLBACK.replace("127.0.0.1", "localhost"),
                     CALLBACK.replace("8064", "8066"), CALLBACK + "/", "https://evil.example/callback"):
        with pytest.raises(ValueError):
            CommunityConfig(callback_url=callback)
