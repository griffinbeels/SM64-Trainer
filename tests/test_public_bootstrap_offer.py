"""A release's public installer chooses its beta through the real network path."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_exe
from sm64_events.bootstrap import installer as boot
from sm64_events.bootstrap import public_offer
from sm64_events.core.build_channel import BuildChannel
from sm64_events.core.update_policy import read_policy, write_policy
from test_bootstrap_installer import _app_files, _Resp, _UI, _zip_bytes, make_manifest


def built_offer(tmp_path, monkeypatch, version, *, legacy=False):
    """Run the shipped builder up to its PyInstaller boundary, inspecting data."""
    import PyInstaller.__main__ as pyi
    import build_defaults_seed
    import build_natives
    monkeypatch.setenv("PYTHONHASHSEED", "1")
    monkeypatch.setattr(build_defaults_seed, "main", lambda _: 0)
    monkeypatch.setattr(build_natives, "ensure", lambda **_: None)
    captured = []
    monkeypatch.setattr(pyi, "run", lambda args: captured.append(args))
    work = tmp_path / "build"
    args = ["build_exe.py", "--workpath", str(work), "--mode", "bootstrap" if legacy else "all"]
    if not legacy:
        channel = "beta" if "-" in version else "stable"
        config = tmp_path / "private-app-config.json"
        config.write_text(json.dumps(BuildChannel(channel=channel, installation="normal", build_id="fixture",
            community_url="https://community.example", supabase_url="https://project.supabase.co",
            supabase_publishable_key="sb_publishable_fixture",
            oauth_client_id="11111111-1111-1111-1111-111111111111", friend_invitation="x" * 43).metadata()))
        ffmpeg = tmp_path / "ffmpeg.exe"
        ffmpeg.write_bytes(b"BUILD-ONLY")
        args += ["--version", version, "--config", str(config), "--ffmpeg", str(ffmpeg)]
    monkeypatch.setattr(sys, "argv", args)
    assert build_exe.main() == 0
    installer_args = captured[-1]
    assert "--onefile" in installer_args and "SM64TrainerSetup" in installer_args
    data = [installer_args[n + 1] for n, arg in enumerate(installer_args) if arg == "--add-data"]
    assert len(data) == 1 and data[0].endswith(build_exe.SEP + ".")
    path = Path(data[0].rsplit(build_exe.SEP, 1)[0])
    assert path.name == public_offer.FILENAME
    assert set(json.loads(path.read_text())) == {"schema", "channel", "version"}
    assert "invitation" not in path.read_text() and "x" * 43 not in path.read_text()
    if not legacy:
        app_args = captured[0]
        assert str(work / "release-version.json") + build_exe.SEP + "." in app_args
        assert json.loads((work / "release-version.json").read_text())["version"] == version
    return path


def network(tmp_path, versions):
    values, releases, calls = {}, [], []
    for index, version in enumerate(versions):
        files = _app_files(version.encode())
        channel = "beta" if "-" in version else "stable"
        files["_internal/build-channel.json"] = json.dumps(BuildChannel(
            channel=channel, installation="normal", build_id="fixture").metadata()).encode()
        files["_internal/release-version.json"] = json.dumps({"schema": 1, "version": version}).encode()
        blob = _zip_bytes(files)
        archive = tmp_path / f"app-{index}.zip"
        archive.write_bytes(blob)
        manifest = make_manifest(archive, version).encode()
        assets = {
            boot.ZIP_ASSET: blob,
            boot.ZIP_ASSET + ".sha256": (hashlib.sha256(blob).hexdigest() + "  " + boot.ZIP_ASSET).encode(),
            boot.MANIFEST_ASSET: manifest,
            boot.MANIFEST_ASSET + ".sha256": (hashlib.sha256(manifest).hexdigest() + "  " + boot.MANIFEST_ASSET).encode(),
        }
        prefix = f"https://dl/{version}/"
        values.update({prefix + name: data for name, data in assets.items()})
        releases.append({"tag_name": "v" + version, "draft": False,
            "prerelease": channel == "beta", "assets": [
                {"name": name, "browser_download_url": prefix + name} for name in assets]})

    def http(request, **_kwargs):
        url = request.full_url
        calls.append(url)
        if "/releases?" in url:
            return _Resp(json.dumps(releases).encode())
        if url.endswith("/releases/latest"):
            stable = next((r for r in releases if not r["prerelease"]), {})
            return _Resp(json.dumps(stable).encode())
        return _Resp(values[url])
    return http, calls


def test_public_beta_builder_metadata_reaches_frozen_entry_and_installs_beta(tmp_path, monkeypatch):
    offer = built_offer(tmp_path, monkeypatch, "1.10.0-beta.5")
    # Model the bundle extraction destination in the exact --add-data argument.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(offer.parent), raising=False)
    own = tmp_path / "renamed-by-viewer.exe"
    own.write_bytes(b"MZ-public-bootstrap")
    monkeypatch.setattr(sys, "executable", str(own))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(boot, "create_desktop_shortcut", lambda *_: True)
    monkeypatch.setattr(boot, "launch_app", lambda *_: None)
    monkeypatch.setattr(boot, "beta_profile_migration_available", lambda: False)
    http, calls = network(tmp_path, ["1.9.1", "1.10.0-beta.2", "1.10.0-beta.5"])
    monkeypatch.setattr(boot.urllib.request, "urlopen", http)
    assert boot.main(["--silent"]) == 0
    assert (boot.default_install_dir() / boot.APP_EXE).read_bytes() == b"1.10.0-beta.5"
    assert read_policy(boot.default_policy_path()) == {
        "schema": 1, "installed_version": "1.10.0-beta.5", "receive_beta_updates": True, "beta_choice": "build"}
    assert any("/1.10.0-beta.5/" in url for url in calls)
    assert not any("/1.9.1/" in url or "/1.10.0-beta.2/" in url for url in calls)


@pytest.mark.parametrize("offered,installed,choice,versions,expected,beta", [
    ("1.9.1", None, None, ["1.9.1", "1.10.0-beta.5"], "1.9.1", False),
    ("1.10.0-beta.5", "1.10.0-beta.10", None,
     ["1.9.1", "1.10.0-beta.5", "1.10.0-beta.10"], "1.10.0-beta.10", True),
    ("1.10.0-beta.5", "1.9.1", "player",
     ["1.9.1", "1.10.0-beta.5"], "1.10.0-beta.5", False),
    ("1.10.0-beta.5", None, None, ["1.10.0-beta.5", "1.10.0"], "1.10.0", True),
])
def test_public_offer_preserves_channel_floor_future_choice_and_profile(
        tmp_path, offered, installed, choice, versions, expected, beta):
    path = build_exe.stage_public_offer(tmp_path / "build", offered)
    target, policy = tmp_path / "Programs/SM64Trainer", tmp_path / "profile/data/update_state.json"
    changes = {"skipped": "unrelated"}
    if installed:
        changes["installed_version"] = installed
    if choice:
        changes.update(beta_choice=choice, receive_beta_updates=False)
    write_policy(policy, **changes)
    history = policy.parent / "tracker.db"
    history.write_bytes(b"PRACTICE")
    http, _ = network(tmp_path, versions)
    ui = _UI()
    assert boot.run_install(http=http, ui=ui, install_dir=target, policy_path=policy,
                            activate=False, public_offer=public_offer.read_offer(path)), ui.errors
    assert (target / boot.APP_EXE).read_bytes() == expected.encode()
    state = read_policy(policy)
    assert state["installed_version"] == expected and state["receive_beta_updates"] is beta
    assert state["skipped"] == "unrelated" and history.read_bytes() == b"PRACTICE"
    if choice:
        assert state["beta_choice"] == "player"


def test_public_beta_offer_cannot_fall_back_to_older_beta_or_production(tmp_path):
    offer = public_offer.read_offer(build_exe.stage_public_offer(tmp_path / "build", "1.10.0-beta.5"))
    policy = tmp_path / "profile/update_state.json"
    http, calls = network(tmp_path, ["1.9.1", "1.10.0-beta.4"])
    ui = _UI()
    assert not boot.run_install(http=http, ui=ui, install_dir=tmp_path / "program", policy_path=policy,
                                public_offer=offer, activate=False)
    assert "older release" in ui.errors[0]
    assert not policy.exists() and not any(url.startswith("https://dl/") for url in calls)


def test_installed_floor_can_refuse_an_old_public_beta_offer(tmp_path):
    offer = public_offer.read_offer(build_exe.stage_public_offer(tmp_path / "build", "1.10.0-beta.5"))
    policy = tmp_path / "profile/update_state.json"
    write_policy(policy, installed_version="1.10.0-beta.10")
    http, calls = network(tmp_path, ["1.9.1", "1.10.0-beta.5"])
    assert not boot.run_install(http=http, ui=_UI(), install_dir=tmp_path / "program", policy_path=policy,
                                public_offer=offer, activate=False)
    assert read_policy(policy)["installed_version"] == "1.10.0-beta.10"
    assert not any(url.startswith("https://dl/") for url in calls)


def test_legacy_builder_infers_stable_source_version(tmp_path, monkeypatch):
    from sm64_events.core.version import __version__
    offer = public_offer.read_offer(built_offer(tmp_path, monkeypatch, __version__, legacy=True))
    assert offer == {"schema": 1, "version": __version__, "channel": "stable"}


def test_configured_stable_builder_carries_matching_public_identity(tmp_path, monkeypatch):
    offer = public_offer.read_offer(built_offer(tmp_path, monkeypatch, "1.10.0"))
    assert offer == {"schema": 1, "version": "1.10.0", "channel": "stable"}


@pytest.mark.parametrize("build", [BuildChannel(installation="normal"),
                                  BuildChannel(channel="beta", build_id="private")])
def test_public_offer_staging_rejects_mismatched_or_legacy_identity_before_writing(tmp_path, build):
    config = tmp_path / "config.json"
    config.write_text(json.dumps(build.metadata()))
    work = tmp_path / "build"
    with pytest.raises(ValueError):
        build_exe.stage_public_offer(work, "1.10.0-beta.5", config)
    assert not work.exists()


@pytest.mark.parametrize("value", [
    {}, {"schema": True, "channel": "beta", "version": "1.10.0-beta.5"},
    {"schema": 1, "channel": "beta", "version": "1.10.0"},
    {"schema": 1, "channel": "stable", "version": "1.10.0-beta.5"},
    {"schema": 1, "channel": "beta", "version": "v1.10.0-beta.5"},
    {"schema": 1, "channel": "beta", "version": "1.10.0-beta.05"},
    {"schema": 1, "channel": "beta", "version": "1.10.0-beta.5", "invitation": "private"},
])
def test_public_offer_rejects_invalid_or_private_metadata(value):
    with pytest.raises(ValueError):
        public_offer.validate_offer(value)


def test_source_cannot_read_offer_from_working_directory_or_meipass(tmp_path, monkeypatch):
    build_exe.stage_public_offer(tmp_path, "1.10.0-beta.5")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert public_offer.bundled_offer() is None


def test_frozen_entry_refuses_invalid_metadata_without_network(tmp_path, monkeypatch):
    (tmp_path / public_offer.FILENAME).write_bytes(b"x" * (public_offer.MAX_BYTES + 1))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(boot, "run_install", lambda **_: pytest.fail("invalid bundle reached install"))
    assert boot.main(["--silent"]) == 1
