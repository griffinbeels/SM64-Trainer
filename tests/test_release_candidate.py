"""Candidate provenance and reusable phase contracts; no builds or publishing."""
import json
from pathlib import Path
import runpy
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_exe
import release
import release_candidate as candidate
import release_flow
from make_manifest import build_zip, make_manifest
from sm64_events.core.build_channel import BuildChannel, CONFIG_FILENAME
from test_bootstrap_installer import _app_files


def prepared(tmp_path, *, private=True, version="1.10.0-beta.1", manifest_version=None):
    app = tmp_path / "app"
    files = _app_files(b"FROZEN-APP")
    build = BuildChannel(channel="beta", build_id="qa-1", installation="normal")
    files["_internal/" + CONFIG_FILENAME] = json.dumps(build.metadata()).encode()
    files["_internal/release-version.json"] = json.dumps({"schema": 1, "version": version}).encode()
    for name, data in files.items():
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    directory = tmp_path / "candidate"
    directory.mkdir()
    build_zip(app, directory / "SM64Trainer-full.zip")
    (directory / "manifest.json").write_text(make_manifest(directory / "SM64Trainer-full.zip", manifest_version or version))
    (directory / "SM64Trainer.exe").write_bytes(b"BOOTSTRAP")
    for name in ("SM64Trainer-full.zip", "manifest.json", "SM64Trainer.exe"):
        release.write_sha(directory / name)
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"LOCKED DEPENDENCIES")
    hook, _ = build_exe.stage_version(tmp_path / "hooks", version)
    candidate.record_candidate(directory, version=version, build=build, artifacts=release.release_assets(directory),
                               source_commit="a" * 40, lock=lock, app=app, version_hook=hook, private=private,
                               service_provenance={"worker_commit": "b" * 40, "catalog_sha256": "c" * 64})
    return directory, app, build


def test_candidate_binds_all_six_artifacts_runtime_version_native_and_public_config(tmp_path):
    directory, _, build = prepared(tmp_path)
    record = candidate.verify_artifacts(directory, expected_assets=[p.name for p in release.release_assets(directory)])
    assert record["version"] == "1.10.0-beta.1"
    assert len(record["artifacts"]) == 6
    assert len(record["native_sha256"]) == 3
    assert record["public_config_sha256"] == candidate.public_fingerprint(build)
    assert record["service"]["catalog_sha256"] == "c" * 64
    assert "friend_invitation" not in (directory / candidate.RECORD).read_text()


def test_candidate_detects_artifact_changed_after_preparation(tmp_path):
    directory, _, _ = prepared(tmp_path)
    (directory / "SM64Trainer.exe").write_bytes(b"REPLACED BOOTSTRAP")
    with pytest.raises(ValueError, match="changed"):
        candidate.verify_artifacts(directory, expected_assets=[p.name for p in release.release_assets(directory)])


def test_candidate_rejects_manifest_bound_to_another_version(tmp_path):
    directory, _, _ = prepared(tmp_path, manifest_version="1.10.0-beta.2")
    with pytest.raises(ValueError, match="offered release"):
        candidate.verify_artifacts(directory, expected_assets=[p.name for p in release.release_assets(directory)])


def test_existing_immutable_record_can_only_resume_identical_provenance(tmp_path):
    path = tmp_path / "candidate.json"
    candidate.write_once(path, {"source": "one"})
    candidate.write_once(path, {"source": "one"})
    with pytest.raises(ValueError, match="different provenance"):
        candidate.write_once(path, {"source": "two"})
    assert json.loads(path.read_text()) == {"source": "one"}


def test_private_candidate_cannot_reach_github_publication(tmp_path, monkeypatch):
    directory, _, _ = prepared(tmp_path)
    monkeypatch.setattr(release, "_run", lambda *_args, **_kw: pytest.fail("private candidate reached GitHub"))
    with pytest.raises(ValueError, match="never be uploaded"):
        release.publication_preflight(directory)


def test_private_invitation_cannot_be_recorded_as_public(tmp_path):
    directory, app, build = prepared(tmp_path)
    private = BuildChannel(channel="beta", build_id="qa-2", installation="normal", friend_invitation="x" * 43)
    with pytest.raises(ValueError, match="private invitation"):
        candidate.record_candidate(tmp_path, version="1.10.0-beta.1", build=private, artifacts=[],
                                   source_commit="a" * 40, lock=tmp_path / "uv.lock", app=app,
                                   version_hook=tmp_path / "hooks" / "rthook_release_version.py", private=False)


def test_staged_hook_sets_actual_runtime_authority_and_exits_before_gui(tmp_path, monkeypatch):
    import sm64_events.core.version as runtime
    import sm64_events.core.build_channel as identity
    original_bytes = Path(runtime.__file__).read_bytes()
    original_version = runtime.__version__
    hook, metadata = build_exe.stage_version(tmp_path, "1.10.0-beta.1")
    proof = tmp_path / "proof.json"
    monkeypatch.setattr(identity, "current_build", lambda: BuildChannel(channel="beta", installation="normal"))
    monkeypatch.setattr(sys, "argv", ["SM64Trainer.exe", "--build-self-check", str(proof)])
    import os
    monkeypatch.setattr(os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    try:
        with pytest.raises(SystemExit) as result:
            runpy.run_path(str(hook))
        assert result.value.code == 0
        assert runtime.__version__ == "1.10.0-beta.1"
        assert json.loads(proof.read_text()) == {"schema": 1, "version": "1.10.0-beta.1", "channel": "beta",
                                               "installation": "normal", "port": 8064, "updates_enabled": True}
        assert json.loads(metadata.read_text())["version"] == "1.10.0-beta.1"
        assert Path(runtime.__file__).read_bytes() == original_bytes
    finally:
        runtime.__version__ = original_version


@pytest.mark.parametrize("value", ["v1.10.0-beta.1", "1.10", "1.10.0-beta.01", "1.10.0\nmalicious"])
def test_version_override_refuses_invalid_version_before_creating_hook(tmp_path, value):
    with pytest.raises(ValueError):
        build_exe.stage_version(tmp_path / "work", value)
    assert not (tmp_path / "work").exists()


def test_verification_refuses_self_check_from_a_different_app(tmp_path, monkeypatch):
    directory, app, _ = prepared(tmp_path, private=False)
    monkeypatch.setattr(candidate, "frozen_self_check", lambda *_: {"app_sha256": "0" * 64, "result": {}})
    with pytest.raises(ValueError, match="different executable"):
        release.verify_candidate(directory, app=app)
    assert not (directory / candidate.VERIFICATION).exists()


def test_verify_phase_never_invokes_build_or_publish(tmp_path, monkeypatch):
    checked = []
    monkeypatch.setattr(release, "verify_candidate", lambda directory: checked.append(directory))
    monkeypatch.setattr(release, "_run", lambda *_args, **_kw: pytest.fail("verify phase invoked a process"))
    monkeypatch.setattr(sys, "argv", ["release.py", "--phase", "verify", "--candidate", str(tmp_path)])
    assert release.main() == 0
    assert checked == [tmp_path]


def test_ship_beta_coordinator_only_delegates_to_shared_preparation(tmp_path, monkeypatch):
    prepared_calls = []
    monkeypatch.setattr(release, "prepare_candidate", lambda version, destination, **kw:
                        prepared_calls.append((version, destination, kw)))
    assert release_flow.main(["ship-beta", "1.10.0-beta.1", str(tmp_path), "--config", str(tmp_path / "config.json")]) == 0
    assert prepared_calls[0][:2] == ("1.10.0-beta.1", tmp_path)
    assert prepared_calls[0][2]["private"] is True


def test_promotion_requires_human_qa_of_exact_candidate(tmp_path):
    directory, _, _ = prepared(tmp_path)
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({"approved": True, "manual_qa": True, "reviewer": "griff",
                                   "candidate_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="exact candidate"):
        candidate.require_approval(directory, approval)
    approval.write_text(json.dumps({"approved": True, "manual_qa": True, "reviewer": "griff",
                                   "candidate_sha256": candidate.digest(directory / candidate.RECORD)}))
    assert candidate.require_approval(directory, approval) == candidate.digest(directory / candidate.RECORD)


def test_private_installer_payload_and_offer_are_bound_to_same_candidate(tmp_path, monkeypatch):
    directory, app, _ = prepared(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    release._package_private_installer(directory, directory / "SM64Trainer-full.zip",
                                      directory / "manifest.json", "1.10.0-beta.1",
                                      directory / candidate.RECORD, work)
    monkeypatch.setattr(candidate, "frozen_self_check", lambda *_: {
        "app_sha256": candidate.digest(app / "SM64Trainer.exe"),
        "result": {"version": "1.10.0-beta.1"}})
    receipt = release.verify_candidate(directory, app=app)
    assert json.loads(receipt.read_text())["draft_ready"] is False
    assert (directory / "SM64Trainer-1.10.0-beta.1-private.exe").is_file()
