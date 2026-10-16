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


def test_public_beta_coordinator_forwards_reviewed_author_binding(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(release, "prepare_candidate", lambda *args, **kw: calls.append(kw))
    binding = tmp_path / "reviewed-author.json"
    assert release_flow.main(["ship-beta", "1.10.0-beta.16", str(tmp_path / "candidate"),
        "--config", str(tmp_path / "config.json"), "--ghosts", str(tmp_path / "collection.sm64ghosts"),
        "--ghost-author", str(binding), "--public", "--check"]) == 0
    assert calls[0]["ghost_author"] == binding and calls[0]["private"] is False


def test_release_prepare_cli_forwards_author_binding(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(release, "prepare_candidate", lambda *args, **kw: calls.append(kw))
    binding = tmp_path / "reviewed-author.json"
    monkeypatch.setattr(sys, "argv", ["release.py", "1.10.0-beta.16", "--phase", "prepare",
        "--candidate", str(tmp_path), "--config", str(tmp_path / "config.json"), "--ghost-author", str(binding)])
    assert release.main() == 0 and calls[0]["ghost_author"] == binding


def test_public_curated_candidate_requires_binding_before_build_prerequisites(tmp_path, monkeypatch):
    import build_beta
    from sm64_events.core.build_channel import parse_metadata
    from test_beta_channel import metadata
    build = parse_metadata({**metadata(), "schema": 2, "installation": "normal"})
    config = tmp_path / "config.json"
    config.write_text(json.dumps(build.metadata()))
    monkeypatch.setattr(build_beta, "check_prerequisites", lambda _: pytest.fail("missing binding reached build"))
    with pytest.raises(ValueError, match="requires --ghost-author"):
        release.prepare_candidate("1.10.0-beta.16", tmp_path / "candidate", config=config,
                                  ghosts=tmp_path / "collection.sm64ghosts", check_only=True)
    assert not (tmp_path / "candidate").exists()


def test_public_candidate_packages_the_reviewed_author_in_the_actual_descriptor(tmp_path, monkeypatch):
    import build_beta
    import shutil
    import zipfile
    from sm64_events.core.build_channel import parse_metadata
    from test_beta_channel import metadata
    from test_build_beta import approved_collection, author_file
    build = parse_metadata({**metadata(), "schema": 2, "installation": "normal"})
    config = tmp_path / "config.json"
    config.write_text(json.dumps(build.metadata()))
    source = approved_collection(tmp_path)
    author = author_file(tmp_path, build)
    prepared_app = tmp_path / "fixture-app"
    # Reuse the real artifact guard's required assets with disposable bytes.
    from test_build_beta import artifact
    folder, _ = artifact.__wrapped__(tmp_path / "private-fixture")
    shutil.copytree(folder, prepared_app)
    (prepared_app / "SM64TrainerBeta.exe").rename(prepared_app / "SM64Trainer.exe")
    (prepared_app / "_internal" / "python313.dll").write_bytes(b"fixture-runtime")
    (prepared_app / "_internal" / CONFIG_FILENAME).write_text(json.dumps(build.metadata()))
    destination = tmp_path / "candidate"
    def fake_build(command, **_kwargs):
        assert "build_exe.py" in command[1]
        dist = destination / ".work" / "dist"
        shutil.copytree(prepared_app, dist / "SM64Trainer")
        (dist / "SM64TrainerSetup.exe").write_bytes(b"fixture-bootstrap")
    monkeypatch.setattr(build_beta, "check_prerequisites", lambda _: "fixture-ffmpeg.exe")
    monkeypatch.setattr(release, "_run", fake_build)
    monkeypatch.setattr(release, "_capture", lambda _: "")
    monkeypatch.setattr(candidate, "source_revision", lambda _: "a" * 40)
    monkeypatch.setattr(release, "_assert_build_source", lambda *_: None)
    monkeypatch.setattr(candidate, "record_candidate", lambda *a, **kw: destination / "candidate.json")
    monkeypatch.setattr(release, "verify_candidate", lambda *_args, **_kwargs: None)
    release.prepare_candidate("1.10.0-beta.16", destination, config=config, ghosts=source, ghost_author=author)
    with zipfile.ZipFile(destination / "SM64Trainer-full.zip") as archive:
        descriptor = json.loads(archive.read("_internal/" + build_beta.SEED_DESCRIPTOR))
        assert archive.read("griffman1212.sm64ghosts") == source.read_bytes()
    binding = json.loads(author.read_text())
    assert descriptor["schema"] == 2
    assert descriptor["author_identity"] == {key: binding[key] for key in ("issuer", "subject")}


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


@pytest.mark.parametrize("change", ["version_only", "project_dependency", "lock_dependency", "runtime_code"])
def test_promotion_accepts_only_own_version_fields_in_actual_committed_source(tmp_path, monkeypatch, change):
    repo = tmp_path / "source"
    repo.mkdir()
    monkeypatch.setattr(release, "REPO", repo)
    version = repo / "src/sm64_events/core/version.py"
    version.parent.mkdir(parents=True)
    version.write_text('__version__ = "1.9.1"\n')
    project = repo / "pyproject.toml"
    project.write_text('[project]\nname = "sm64-events"\nversion = "1.9.1"\ndependencies = ["fastapi>=0.115"]\n')
    lock = repo / "uv.lock"
    lock.write_text('[[package]]\nname = "sm64-events"\nversion = "1.9.1"\n'
                    '[[package]]\nname = "fastapi"\nversion = "0.115.0"\n')
    for args in (["init", "-q"], ["config", "core.longpaths", "true"], ["config", "user.name", "Disposable QA"],
                 ["config", "user.email", "qa@example.invalid"], ["add", "src", "pyproject.toml", "uv.lock"],
                 ["commit", "-qm", "approved beta source"]):
        release._run(["git", *args], capture_output=True)
    source = release._capture(["git", "rev-parse", "HEAD"])
    build = BuildChannel(channel="stable", build_id="stable", installation="normal")
    directory = tmp_path / "candidate"
    directory.mkdir()
    candidate.write_once(directory / candidate.RECORD, {
        "schema": 1, "version": "1.10.0-beta.1", "installation": "normal", "distribution": "public",
        "channel": "beta", "source_commit": source, "public_config_sha256": candidate.public_fingerprint(build)})
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({"approved": True, "manual_qa": True, "reviewer": "griff",
                                   "candidate_sha256": candidate.digest(directory / candidate.RECORD)}))
    config = tmp_path / "config.json"
    config.write_text(json.dumps(build.metadata()))
    for path in (version, project, lock):
        path.write_text(path.read_text().replace("1.9.1", "1.10.0"))
    if change == "project_dependency":
        project.write_text(project.read_text().replace("fastapi>=0.115", "fastapi>=0.120"))
    elif change == "lock_dependency":
        lock.write_text(lock.read_text().replace("0.115.0", "0.120.0"))
    elif change == "runtime_code":
        version.write_text(version.read_text() + 'print("changed runtime")\n')
    release._run(["git", "add", "src", "pyproject.toml", "uv.lock"], capture_output=True)
    release._run(["git", "commit", "-qm", "stable preparation"], capture_output=True)
    if change == "version_only":
        assert release_flow.promotion_source(directory, approval, "1.10.0", config) == candidate.digest(directory / candidate.RECORD)
    else:
        with pytest.raises(ValueError, match="dependencies or configuration changed"):
            release_flow.promotion_source(directory, approval, "1.10.0", config)


@pytest.mark.parametrize("changed", ["head", "working_tree", "lock", None])
def test_candidate_provenance_requires_source_still_unchanged_after_long_build(tmp_path, monkeypatch, changed):
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"ORIGINAL")
    lock_digest = candidate.digest(lock)
    monkeypatch.setattr(release, "UV_LOCK", lock)
    monkeypatch.setattr(release, "_capture", lambda command:
                        ("b" * 40 if changed == "head" else "a" * 40) if command[1] == "rev-parse"
                        else " M src/product.py" if changed == "working_tree" else "")
    if changed == "lock":
        lock.write_bytes(b"CHANGED")
    if changed is None:
        release._assert_build_source("a" * 40, lock_digest)
    else:
        with pytest.raises(ValueError, match="changed during preparation"):
            release._assert_build_source("a" * 40, lock_digest)
