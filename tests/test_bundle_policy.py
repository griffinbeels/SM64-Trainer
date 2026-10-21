"""Packaging selects runtime assets and refuses incidental local setup files."""
from pathlib import Path
import os
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bundle_policy as policy


@pytest.fixture
def data_directory(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    for name in policy.DATA_FILES:
        (directory / name).write_bytes(b"runtime fixture")
    (directory / "plugin").mkdir()
    return directory


def test_data_inputs_preserve_all_tracked_runtime_data(data_directory):
    # A new source asset requires deliberate review of its packaging contract.
    from sm64_events.core.childproc import quiet_spawn_kwargs
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "ls-files", "-z", "--", "src/sm64_events/data"],
                            cwd=repo, capture_output=True, check=True, **quiet_spawn_kwargs())
    tracked = {Path(name).name for name in result.stdout.decode().split("\0") if name}
    assert {path.name for path in policy.data_inputs(data_directory)} == tracked


@pytest.mark.parametrize("name", ["local-service.json", ".env", "sdk", "temporary.txt"])
def test_data_inputs_refuse_local_extras_without_echoing_contents(data_directory, name):
    extra = data_directory / name
    extra.write_text("synthetic private build input")
    with pytest.raises(ValueError, match="unexpected first-party data") as failure:
        policy.data_inputs(data_directory)
    assert "synthetic private" not in str(failure.value)


def test_data_inputs_require_the_runtime_files(data_directory):
    (data_directory / "ranking_policy.json").unlink()
    with pytest.raises(ValueError, match="missing"):
        policy.data_inputs(data_directory)


def test_ui_inputs_keep_nested_tracked_assets_but_refuse_local_script(tmp_path, monkeypatch):
    paths = frozenset({"index.html", "assets/logo.png", "components/example.js"})
    monkeypatch.setattr(policy, "tracked_ui_paths", lambda _repo: paths)
    directory = tmp_path / "src/sm64_events/ui"
    for name in paths:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"runtime fixture")
    assert {path.relative_to(directory).as_posix() for path in policy.ui_inputs(tmp_path)} == paths
    (directory / "local-settings.js").write_text("synthetic local settings")
    with pytest.raises(ValueError, match="unexpected or missing"):
        policy.ui_inputs(tmp_path)


def test_ui_inputs_reject_links_to_other_inputs(tmp_path, monkeypatch):
    directory = tmp_path / "src/sm64_events/ui"
    directory.mkdir(parents=True)
    (directory / "index.html").write_bytes(b"runtime fixture")
    outside = tmp_path / "outside.txt"
    outside.write_text("synthetic local settings")
    link = directory / "linked.js"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("this test account cannot create symbolic links")
    monkeypatch.setattr(policy, "tracked_ui_paths", lambda _repo: {"index.html", "linked.js"})
    with pytest.raises(ValueError, match="unexpected first-party UI"):
        policy.ui_inputs(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary")
@pytest.mark.parametrize("kind", ["data", "ui"])
def test_source_inputs_refuse_real_directory_junctions(tmp_path, monkeypatch, kind):
    from sm64_events.core.childproc import quiet_spawn_kwargs
    directory = tmp_path / ("data" if kind == "data" else "src/sm64_events/ui")
    directory.mkdir(parents=True)
    for name in policy.DATA_FILES if kind == "data" else ("index.html",):
        (directory / name).write_text("runtime fixture")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "local-settings.js").write_text("synthetic local settings")
    link = directory / ("plugin" if kind == "data" else "assets")
    result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
                            capture_output=True, check=False, **quiet_spawn_kwargs())
    if result.returncode:
        pytest.skip("this test account cannot create directory junctions")
    assert link.is_junction()
    monkeypatch.setattr(policy, "tracked_ui_paths", lambda _repo: {"index.html", "assets/local-settings.js"})
    with pytest.raises(ValueError, match="unexpected first-party"):
        policy.data_inputs(directory) if kind == "data" else policy.ui_inputs(tmp_path)


@pytest.mark.parametrize("path", [
    "_internal/sm64_events/data/local-service.json",
    "_internal/sm64_events/ui/local-settings.js",
    "_internal/SM64_EVENTS/data/local-service.json",
    "_internal/Sm64_Events/ui/local-settings.js",
    "_internal/local-service.json", "_internal/eos-private/client.json",
    "_internal/eos-sdk/SDK/Include/header.h", ".env", "local-service.json",
    "_internal//python313.dll", "_internal/./python313.dll",
    "_internal/python313.dll:private", "_internal/python313.dll.",
])
def test_bundle_paths_refuse_unknown_first_party_and_private_inputs(path):
    with pytest.raises(ValueError, match="unexpected"):
        policy.check_bundle_path(path, ui_paths={"index.html"})


@pytest.mark.parametrize("path", [
    "SM64Trainer.exe", "SM64TrainerBeta.exe", "README-BETA.txt",
    "_internal/python313.dll", "_internal/ffmpeg.exe", "_internal/base_library.zip",
    "_internal/certifi/cacert.pem", "_internal/sm64_events/data/ranking_policy.json",
    "_internal/sm64_events/data/plugin/SM64GpuEncoderV1.dll",
    "_internal/sm64_events/ui/index.html",
])
def test_bundle_paths_keep_runtime_dependencies_and_explicit_assets(path):
    policy.check_bundle_path(path, ui_paths={"index.html"})
