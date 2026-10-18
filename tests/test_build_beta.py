"""Disposable packaging fixtures; no real PyInstaller, compiler or app launch."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_beta
from sm64_events.core.build_channel import parse_metadata
from test_beta_channel import metadata
from beta_ghosts import COLLECTION_NAME, review_collection


@pytest.fixture
def artifact(tmp_path):
    build = parse_metadata(metadata())
    folder = tmp_path / "SM64TrainerBeta"
    names = ["SM64TrainerBeta.exe", "_internal/ffmpeg.exe", "_internal/sm64_events/ui/index.html",
             *["_internal/sm64_events/data/plugin/" + name for name in
               ("sm64_trainer_gfx.dll", "GLideN64_SM64Trainer.dll", "SM64GpuEncoderV1.dll")],
             *["_internal/" + name for name in
               ("ukiki.ico", "behaviours_us.tsv", "behaviours_jp.tsv", "symbols_us.tsv", "symbols_jp.tsv",
                "rank_standards.seed.json", "defaults.seed.json", "sheet_ladders.seed.json",
                "sheet_library.seed.json.gz", "library_overrides.json", "video_checks.seed.json.gz")]]
    for name in names:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"disposable-artifact-fixture")
    (folder / "_internal" / "build-channel.json").write_text(json.dumps(build.metadata()))
    return folder, build


def test_beta_args_share_app_assets_but_isolate_every_build_output(tmp_path):
    config = tmp_path / "build-channel.json"
    args = build_beta.beta_args("C:/tools/ffmpeg.exe", config, dist=tmp_path / "dist", work=tmp_path / "work")
    assert args[args.index("--name") + 1] == "SM64TrainerBeta"
    assert "--onefile" not in args and "--windowed" in args
    assert not any("bootstrap_entry" in arg or "SM64TrainerSetup" in arg for arg in args)
    for flag, value in (("--distpath", tmp_path / "dist"),
                        ("--workpath", tmp_path / "work" / "pyinstaller"),
                        ("--specpath", tmp_path / "work" / "spec")):
        assert args[args.index(flag) + 1] == str(value)
    assert any(str(config) in arg for arg in args)
    for expected in ("ffmpeg.exe", "sm64_trainer_gfx.dll", "GLideN64_SM64Trainer.dll", "SM64GpuEncoderV1.dll"):
        assert any(expected in arg for arg in args)


def test_friend_zip_contains_complete_folder_and_matches_sha_receipt(artifact, tmp_path):
    folder, build = artifact
    archive = build_beta.package_beta(folder, build, tmp_path / "private-output")
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        assert set(zipped.namelist()) == {
            path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}
        assert json.loads(zipped.read("_internal/build-channel.json")) == build.metadata()
        assert b"public app updates are disabled" in zipped.read("README-BETA.txt").lower()
        assert b"complete ZIP" in zipped.read("README-BETA.txt")
    checksum, filename = archive.with_suffix(".zip.sha256").read_text().split()
    assert checksum == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert filename == archive.name


@pytest.mark.parametrize("path", ["tracker.db", "replays/attempt_1.mp4", "logs/debug.log",
                                 ".private/config", "SM64TrainerSetup.exe", "SM64Trainer.exe",
                                 "recording.ghost", "asset.ghc1", "export.sm64ghosts", "ui_log.jsonl",
                                 "library_adoptions.json", "tracker_mode.json",
                                 "climb-profile.json", "developer-simulation.json",
                                 "ghost_population/manifest.json", "community_cache/index.json",
                                 "compare_cache/private.json"])
def test_artifact_refuses_private_data_hidden_files_and_public_executables(artifact, path):
    folder, build = artifact
    extra = folder / path
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b"fixture")
    with pytest.raises(RuntimeError, match="unexpected"):
        build_beta.validate_artifact(folder, build)


def test_artifact_refuses_missing_asset_and_different_build_configuration(artifact):
    folder, build = artifact
    config = folder / "_internal" / "build-channel.json"
    config.write_text(json.dumps({**build.metadata(), "build_id": "different-build"}))
    with pytest.raises(RuntimeError, match="metadata does not match"):
        build_beta.validate_artifact(folder, build)
    config.write_text(json.dumps(build.metadata()))
    (folder / "_internal" / "ffmpeg.exe").unlink()
    with pytest.raises(RuntimeError, match="ffmpeg"):
        build_beta.validate_artifact(folder, build)


def approved_collection(tmp_path, *, author="griffman1212"):
    from test_ghost_sharing import recording
    from sm64_events.ghost.portable import make_entry, write_package
    source, original = recording(tmp_path)
    entry = make_entry(source, author=author, target=original["target"], frames=original["frames"],
                       clock=original["clock"], recorded_utc=original["recorded_utc"], practice=False,
                       outcome="success", game_version=None)
    collection = tmp_path / "reviewed.sm64ghosts"
    write_package(collection, [(entry, source)])
    return collection


def test_only_explicit_reviewed_ghosts_can_travel_with_beta(artifact, tmp_path):
    from test_ghost_sharing import provider
    folder, build = artifact
    source = approved_collection(tmp_path)
    approved = review_collection(source)
    archive = build_beta.package_beta(folder, build, tmp_path / "output", ghosts=approved)
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.read(COLLECTION_NAME) == source.read_bytes()
        assert b"1 legitimate recordings credited to griffman1212" in zipped.read("README-BETA.txt")
    sharing = provider(tmp_path)
    assert sharing.import_file(folder / COLLECTION_NAME)["imported"] == 1
    assert sharing.imports_for("star", "2-1")[0].author == "griffman1212"
    assert sharing.database.writes == []
    with pytest.raises(RuntimeError, match="unexpected"):
        build_beta.validate_artifact(folder, build)
    (folder / "other.sm64ghosts").write_bytes(source.read_bytes())
    with pytest.raises(RuntimeError, match="unexpected"):
        build_beta.validate_artifact(folder, build, ghosts=approved)


def test_curated_collection_rejects_wrong_author_and_changed_bytes(artifact, tmp_path):
    source = approved_collection(tmp_path, author="gtest1")
    with pytest.raises(ValueError, match="griffman1212"):
        review_collection(source)
    source = approved_collection(tmp_path)
    approved = review_collection(source)
    source.write_bytes(source.read_bytes() + b"changed")
    folder, build = artifact
    with pytest.raises(RuntimeError, match="changed"):
        build_beta.package_beta(folder, build, tmp_path / "output", ghosts=approved)


@pytest.mark.parametrize("relative,content", [
    ("_internal/numpy/f2py/tests/src/assumed_shape/.f2py_f2cmap", b'dict(real=dict(rk="double"))\r\n'),
    ("_internal/numpy/f2py/tests/src/f2cmap/.f2py_f2cmap",
     b"dict(real=dict(real32='float', real64='double'), integer=dict(int64='long_long'))\r\n"),
])
def test_vendor_hidden_exception_requires_exact_path_and_bytes(artifact, relative, content):
    folder, build = artifact
    vendor = folder / relative
    vendor.parent.mkdir(parents=True)
    vendor.write_bytes(content)
    build_beta.validate_artifact(folder, build)
    vendor.write_bytes(content + b"x")
    with pytest.raises(RuntimeError, match="unexpected"):
        build_beta.validate_artifact(folder, build)
    vendor.write_bytes(content)
    neighbour = vendor.with_name(".private-neighbour")
    neighbour.write_bytes(content)
    with pytest.raises(RuntimeError, match="unexpected"):
        build_beta.validate_artifact(folder, build)
    neighbour.unlink()
    misplaced = folder / ".f2py_f2cmap"
    misplaced.write_bytes(content)
    with pytest.raises(RuntimeError, match="unexpected"):
        build_beta.validate_artifact(folder, build)


def test_builder_reexec_relays_failure_diagnostics(monkeypatch, tmp_path, capsys):
    config = tmp_path / "public-config.json"
    config.write_text(json.dumps(metadata()))
    monkeypatch.setattr(build_beta, "needs_reexec", lambda env: True)
    monkeypatch.setattr(build_beta, "_source_date_epoch", lambda: "315532800")
    monkeypatch.setattr(build_beta.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=2, stdout="prerequisites checked\n", stderr="artifact refused\n"))
    assert build_beta.main(["--config", str(config)]) == 2
    output = capsys.readouterr()
    assert "prerequisites checked" in output.out and "artifact refused" in output.err


def test_builder_requires_ffmpeg_instead_of_silent_replay_fallback(monkeypatch):
    monkeypatch.setattr(build_beta.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="FFmpeg is required"):
        build_beta.check_prerequisites(None)


def test_builder_rejects_stale_natives_without_attempting_compilation(monkeypatch, tmp_path):
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"fixture")
    monkeypatch.setattr(build_beta.importlib.util, "find_spec", lambda name: object())
    calls = []
    def ensure(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("native is stale")
    monkeypatch.setitem(sys.modules, "build_natives", SimpleNamespace(ensure=ensure))
    with pytest.raises(RuntimeError, match="stale"):
        build_beta.check_prerequisites(str(ffmpeg))
    assert calls == [{"build": False, "strict": True}]


def test_check_only_never_builds_writes_or_reexecutes(monkeypatch, tmp_path):
    config = tmp_path / "public-config.json"
    config.write_text(json.dumps(metadata()))
    monkeypatch.setattr(build_beta, "check_prerequisites", lambda value: "C:/fixture/ffmpeg.exe")
    monkeypatch.setattr(build_beta.subprocess, "run", lambda *a, **k: pytest.fail("reexecuted check-only mode"))
    monkeypatch.setattr(build_beta, "WORK", tmp_path / "work")
    assert build_beta.main(["--config", str(config), "--check"]) == 0
    assert not (tmp_path / "work").exists()


def test_private_builder_refuses_stable_config_before_prerequisites(monkeypatch, tmp_path):
    config = tmp_path / "stable.json"
    config.write_text('{"schema":1,"channel":"stable"}')
    monkeypatch.setattr(build_beta, "check_prerequisites", lambda value: pytest.fail("accepted stable"))
    assert build_beta.main(["--config", str(config), "--check"]) == 2


def test_collection_review_refuses_any_recording_with_an_unknown_input(tmp_path):
    """His rule (round 40): "If it's not a perfectly recorded ghost, it doesn't belong in the system."

    beta.2 shipped 160 of 180 recordings with no pads; a collection with one
    unknown frame now never becomes a build input."""
    from test_ghost_sharing import recording
    from sm64_events.ghost.portable import make_entry, write_package
    entries = []
    for name, pad in (("known", (12, -34, 0x8000)), ("unknown", None)):
        (tmp_path / name).mkdir()
        source, original = recording(tmp_path / name, pad=pad)
        entries.append((make_entry(source, author="griffman1212", target=original["target"], frames=original["frames"],
                                   clock=original["clock"], recorded_utc=original["recorded_utc"], practice=False,
                                   outcome="success", game_version=None), source))
    collection = tmp_path / "reviewed.sm64ghosts"
    write_package(collection, entries)
    with pytest.raises(ValueError, match=r"complete inputs only \(1 of 2"):
        review_collection(collection)
    approved = review_collection(approved_collection(tmp_path))
    assert (approved.count, approved.with_inputs) == (1, 1)


def author_file(tmp_path, build, **changes):
    path = tmp_path / "reviewed-author.json"
    path.write_text(json.dumps({"schema": 1, "author": "griffman1212",
        "issuer": build.supabase_url + "/auth/v1", "subject": "11111111-1111-4111-8111-111111111111", **changes}))
    return path


def test_reviewed_binding_travels_through_actual_zip_and_frozen_population_entrypoint(artifact, tmp_path, monkeypatch):
    from beta_ghosts import review_author
    from sm64_events.core import build_channel
    from sm64_events.ghost.bundled_beta import with_bundled_beta
    from test_bundled_beta_ghosts import Local
    folder, build = artifact
    source = approved_collection(tmp_path)
    approved = review_collection(source)
    author = review_author(author_file(tmp_path, build), build)
    archive = build_beta.package_beta(folder, build, tmp_path / "output", ghosts=approved, ghost_author=author)
    with zipfile.ZipFile(archive) as zipped:
        descriptor = json.loads(zipped.read("_internal/" + build_beta.SEED_DESCRIPTOR))
    assert descriptor["schema"] == 2 and descriptor["author_identity"] == author
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(folder / "_internal"), raising=False)
    monkeypatch.setattr(sys, "executable", str(folder / "SM64TrainerBeta.exe"))
    monkeypatch.setattr(build_channel, "current_build", lambda: build)
    population = with_bundled_beta(Local(), cache=tmp_path / "runtime-cache", definitions=())
    assert population.metadata_snapshot("star", "2-1", clock="igt", game_version="us")[0]["player_id"] == author["subject"]
    # Artifact verification compares the identity as well as collection bytes.
    (folder / "_internal" / build_beta.SEED_DESCRIPTOR).write_text(json.dumps({**descriptor,
        "author_identity": {**author, "subject": "22222222-2222-4222-8222-222222222222"}}))
    with pytest.raises(RuntimeError, match="reviewed collection"):
        build_beta.validate_artifact(folder, build, ghosts=approved, ghost_author=author)


@pytest.mark.parametrize("changes", [{"author": "someone-else"}, {"schema": 2}, {"subject": "bad-uuid"},
    {"issuer": "https://another.supabase.co/auth/v1"}, {"issuer": "https://fixture/auth/v1?secret=private"}])
def test_author_review_refuses_wrong_author_subject_or_issuer_without_echoing_values(artifact, tmp_path, changes):
    from beta_ghosts import review_author
    _, build = artifact
    with pytest.raises(ValueError) as failure:
        review_author(author_file(tmp_path, build, **changes), build)
    assert not any(str(value) in str(failure.value) for value in changes.values())


def test_legacy_builder_forwards_reviewed_author_to_normal_candidate(monkeypatch, tmp_path):
    import release
    build = parse_metadata({**metadata(), "schema": 2, "installation": "normal"})
    config = tmp_path / "config.json"
    config.write_text(json.dumps(build.metadata()))
    author = author_file(tmp_path, build)
    calls = []
    monkeypatch.setattr(release, "prepare_candidate", lambda *args, **kwargs: calls.append(kwargs))
    assert build_beta.main(["--config", str(config), "--version", "1.10.0-beta.16",
        "--destination", str(tmp_path / "candidate"), "--ghosts", str(tmp_path / "collection.sm64ghosts"),
        "--ghost-author", str(author), "--check"]) == 0
    assert calls[0]["ghost_author"] == author
