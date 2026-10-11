"""External repair through the installer entry point; no application is launched."""
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from sm64_events.bootstrap import installer as boot
from sm64_events.bootstrap.beta_payload import append_payload
from sm64_events.core.update_policy import read_policy, write_policy
from test_bootstrap_installer import FULL_ASSETS, _UI, _Resp, _app_files, _release_json, _zip_bytes, make_manifest


def release(version):
    return json.loads(_release_json("v" + version, FULL_ASSETS))


def routes(tmp_path, version, releases):
    blob = _zip_bytes(_app_files(b"REPAIRED"))
    if "-" in version:
        from sm64_events.core.build_channel import BuildChannel
        files = _app_files(b"REPAIRED")
        files["_internal/build-channel.json"] = json.dumps(BuildChannel(
            channel="beta", build_id="repair", installation="normal").metadata()).encode()
        files["_internal/release-version.json"] = json.dumps({"schema": 1, "version": version}).encode()
        blob = _zip_bytes(files)
    path = tmp_path / "app.zip"
    path.write_bytes(blob)
    manifest = make_manifest(path, version)
    values = {"https://dl/full.zip": blob,
              "https://dl/full.sha": (hashlib.sha256(blob).hexdigest() + "  SM64Trainer-full.zip").encode(),
              "https://dl/manifest.json": manifest.encode(),
              "https://dl/manifest.sha": (hashlib.sha256(manifest.encode()).hexdigest() + "  manifest.json").encode()}
    calls = []

    def http(request):
        calls.append(request.full_url)
        if "/releases?per_page=100&page=1" in request.full_url:
            return _Resp(json.dumps(releases).encode())
        return _Resp(values[request.full_url])
    return http, calls


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    monkeypatch.setattr(boot, "create_desktop_shortcut", lambda exe: True)
    monkeypatch.setattr(boot, "launch_app", lambda exe, own: None)
    return tmp_path / "program" / "SM64Trainer", tmp_path / "profile" / "SM64Trainer" / "data" / "update_state.json"


def test_beta_opt_in_selects_newest_semver_and_keeps_preference(tmp_path, context):
    target, policy = context
    write_policy(policy, receive_beta_updates=True, installed_version="1.9.1")
    http, calls = routes(tmp_path, "1.10.0-beta.10", [release("1.10.0-beta.2"), release("1.9.2"), release("1.10.0-beta.10")])
    assert boot.run_install(http=http, ui=_UI(), install_dir=target, policy_path=policy)
    assert read_policy(policy)["installed_version"] == "1.10.0-beta.10"
    assert read_policy(policy)["receive_beta_updates"] is True
    assert any("/releases?" in url for url in calls)


def test_opted_out_ahead_beta_cannot_downgrade_when_app_and_manifest_broken(tmp_path, context):
    target, policy = context
    target.mkdir(parents=True)
    (target / boot.INSTALLED_MANIFEST).write_text("damaged")
    write_policy(policy, installed_version="1.10.0-beta.2", receive_beta_updates=False)
    http, calls = routes(tmp_path, "1.9.2", [release("1.9.2")])
    ui = _UI()
    assert not boot.run_install(http=http, ui=ui, install_dir=target, policy_path=policy)
    assert "older release" in ui.errors[0]
    assert "https://dl/full.zip" not in calls
    assert read_policy(policy)["installed_version"] == "1.10.0-beta.2"


def test_beta_only_forward_repair_requires_explicit_selection_and_preserves_opt_out(tmp_path, context):
    target, policy = context
    write_policy(policy, installed_version="1.10.0-beta.2")
    http, calls = routes(tmp_path, "1.10.0-beta.3", [release("1.9.2"), release("1.10.0-beta.3")])
    assert not boot.run_install(http=http, ui=_UI(), install_dir=target, policy_path=policy)
    ui = _UI()
    picked = []
    ui.confirm_beta_repair = lambda tag: picked.append(tag) or True
    assert boot.run_install(http=http, ui=ui, install_dir=target, policy_path=policy)
    assert picked == ["v1.10.0-beta.3"]
    assert read_policy(policy)["receive_beta_updates"] is False


def test_same_version_repair_reconstructs_corrupt_application_and_preserves_history(tmp_path, context):
    target, policy = context
    target.mkdir(parents=True)
    (target / boot.APP_EXE).write_bytes(b"CORRUPT")
    (target / "obsolete-managed.dll").write_bytes(b"STALE")
    write_policy(policy, installed_version="2.0.0", skipped="2.1.0")
    history = policy.parent / "tracker.db"
    history.write_bytes(b"PRACTICE")
    http, _ = routes(tmp_path, "2.0.0", [release("2.0.0")])
    assert boot.run_install(http=http, ui=_UI(), install_dir=target, policy_path=policy)
    assert (target / boot.APP_EXE).read_bytes() == b"REPAIRED"
    assert not (target / "obsolete-managed.dll").exists()
    assert history.read_bytes() == b"PRACTICE"
    assert read_policy(policy)["skipped"] == "2.1.0"
    record = json.loads((target.parent / ".SM64Trainer-install.json").read_text())
    assert (target.parent / record["previous"] / boot.APP_EXE).read_bytes() == b"CORRUPT"


@pytest.mark.parametrize("fault", ["missing_python", "wrong_version", "content_mismatch", "unlisted_file", "unsafe_path"])
def test_repair_rejects_incomplete_or_unbound_payload_before_touching_install(tmp_path, context, fault):
    target, _ = context
    target.mkdir(parents=True)
    (target / boot.APP_EXE).write_bytes(b"ORIGINAL")
    files = _app_files(b"NEW")
    if fault == "missing_python":
        files.pop("_internal/python313.dll")
    archive = tmp_path / "payload.zip"
    archive.write_bytes(_zip_bytes(files))
    manifest = make_manifest(archive, "2.0.0")
    if fault == "content_mismatch":
        files[boot.APP_EXE] = b"EVIL"
    elif fault == "unlisted_file":
        files["tracker.db"] = b"PRIVATE"
    elif fault == "unsafe_path":
        files["../escaped"] = b"PRIVATE"
    archive.write_bytes(_zip_bytes(files))
    with pytest.raises((ValueError, RuntimeError)):
        boot.install_tree(archive, manifest, target, offered_version="3.0.0" if fault == "wrong_version" else "2.0.0")
    assert (target / boot.APP_EXE).read_bytes() == b"ORIGINAL"
    assert not (target.parent / "escaped").exists()


def test_failed_rollback_keeps_backup_and_next_installer_recovers_it(tmp_path, context, monkeypatch):
    target, _ = context
    target.mkdir(parents=True)
    (target / boot.APP_EXE).write_bytes(b"ORIGINAL")
    archive = tmp_path / "payload.zip"
    archive.write_bytes(_zip_bytes(_app_files(b"NEW")))
    manifest = make_manifest(archive, "2.0.0")
    real = boot.os.replace

    def fail(source, destination):
        if Path(destination) == target and str(source).endswith((".new", ".old")):
            raise PermissionError("simulated locked path")
        return real(source, destination)
    monkeypatch.setattr(boot.os, "replace", fail)
    with pytest.raises(RuntimeError, match="recovery backup"):
        boot.install_tree(archive, manifest, target)
    journal = json.loads((target.parent / ".SM64Trainer-install.json").read_text())
    backup = target.parent / journal["previous"]
    assert (backup / boot.APP_EXE).read_bytes() == b"ORIGINAL"
    monkeypatch.setattr(boot.os, "replace", real)
    boot.recover_install(target)
    assert (target / boot.APP_EXE).read_bytes() == b"ORIGINAL"
    boot.install_tree(archive, manifest, target)
    assert (target / boot.APP_EXE).read_bytes() == b"NEW"


def test_offline_normal_installer_contains_everything_and_never_uses_network(tmp_path, context):
    target, policy = context
    archive = tmp_path / "payload.zip"
    from sm64_events.core.build_channel import BuildChannel
    files = _app_files(b"OFFLINE")
    files["_internal/build-channel.json"] = json.dumps(BuildChannel(
        channel="beta", build_id="offline", installation="normal").metadata()).encode()
    files["_internal/release-version.json"] = json.dumps({"schema": 1, "version": "1.10.0-beta.1"}).encode()
    archive.write_bytes(_zip_bytes(files))
    manifest = make_manifest(archive, "1.10.0-beta.1")
    with zipfile.ZipFile(archive, "a") as zipped:
        zipped.writestr(boot.OFFLINE_OFFER, json.dumps({"schema": 1, "version": "1.10.0-beta.1", "manifest": manifest}))
    stub = tmp_path / "stub.exe"
    stub.write_bytes(b"MZ-stub")
    installer = tmp_path / "private.exe"
    append_payload(stub, archive, installer)
    assert boot.run_install(http=lambda _: pytest.fail("offline installer used network"), ui=_UI(),
                            own_path=installer, install_dir=target, policy_path=policy)
    assert (target / boot.APP_EXE).read_bytes() == b"OFFLINE"
    assert not (target / boot.OFFLINE_OFFER).exists()
    assert read_policy(policy)["installed_version"] == "1.10.0-beta.1"
    assert read_policy(policy)["receive_beta_updates"] is False


def test_profile_copy_is_only_called_after_explicit_opt_in(tmp_path, context, monkeypatch):
    target, policy = context
    monkeypatch.setattr(boot, "beta_profile_migration_available", lambda: True)
    copied = []
    monkeypatch.setattr(boot, "migrate_private_beta_profile", lambda **kw: copied.append(kw))
    http, _ = routes(tmp_path, "2.0.0", [release("2.0.0")])
    ui = _UI()
    ui.confirm_profile_migration = lambda: False
    assert boot.run_install(http=http, ui=ui, install_dir=target, policy_path=policy)
    assert copied == []
    ui.confirm_profile_migration = lambda: True
    assert boot.run_install(http=http, ui=ui, install_dir=target, policy_path=policy)
    assert copied == [{"confirmed": True}]
