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
