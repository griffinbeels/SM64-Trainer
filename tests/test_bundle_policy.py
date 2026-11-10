"""Packaging selects runtime assets and refuses incidental local setup files."""
from pathlib import Path
import os
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bundle_policy as policy


SOURCE_DATA = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "data"


@pytest.fixture
def data_directory(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    for name in policy.DATA_FILES:
        (directory / name).write_bytes(b"runtime fixture")
    (directory / "plugin").mkdir()
    (directory / "maps").mkdir()
    for name in policy.PINNED_MAPS:      # committed, pinned bytes
        (directory / "maps" / name).write_bytes((SOURCE_DATA / "maps" / name).read_bytes())
    return directory


def test_data_inputs_preserve_all_tracked_runtime_data(data_directory):
    # A new source asset requires deliberate review of its packaging contract.
    from sm64_events.core.childproc import quiet_spawn_kwargs
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "ls-files", "-z", "--", "src/sm64_events/data"],
                            cwd=repo, capture_output=True, check=True, **quiet_spawn_kwargs())
    tracked = {Path(name).relative_to("src/sm64_events/data").as_posix()
               for name in result.stdout.decode().split("\0") if name}
    shipped = {path.relative_to(data_directory).as_posix()
               for path in (*policy.data_inputs(data_directory), *policy.map_inputs(data_directory))}
    assert shipped == tracked


def test_the_lab_maps_are_pinned_and_a_changed_byte_refuses_the_build(data_directory):
    """STROOP's maps at the commit tools/scene_lab/cold_boot.py pins: the
    reviewed bytes, and nothing else, reach the installed app."""
    import hashlib
    from sm64_events.core.paths import LAB_MAPS
    assert set(policy.PINNED_MAPS) == set(LAB_MAPS)
    for name, digest in policy.PINNED_MAPS.items():
        assert hashlib.sha256((SOURCE_DATA / "maps" / name).read_bytes()).hexdigest() == digest
    assert [path.name for path in policy.map_inputs(data_directory)] == sorted(LAB_MAPS)
    maps = data_directory / "maps"
    data = bytearray((maps / "MappingJP.map").read_bytes())
    data[100] ^= 1
    (maps / "MappingJP.map").write_bytes(bytes(data))
    with pytest.raises(ValueError, match="maps/MappingJP.map is not the reviewed file"):
        policy.map_inputs(data_directory)
    (maps / "MappingJP.map").unlink()
    with pytest.raises(ValueError, match="maps/"):
        policy.map_inputs(data_directory)
    for name in LAB_MAPS:
        policy.check_bundle_path(f"_internal/sm64_events/data/maps/{name}", ui_paths={"index.html"})
    with pytest.raises(ValueError, match="unexpected first-party data"):
        policy.check_bundle_path("_internal/sm64_events/data/maps/MappingEU.map", ui_paths={"index.html"})


@pytest.mark.parametrize("name", ["local-service.json", ".env", "sdk", "temporary.txt"])
def test_data_inputs_refuse_local_extras_without_echoing_contents(data_directory, name):
    extra = data_directory / name
    extra.write_text("synthetic private build input")
    with pytest.raises(ValueError, match="unexpected first-party data") as failure:
        policy.data_inputs(data_directory)
    assert "synthetic private" not in str(failure.value)


def test_the_lab_natives_have_their_own_folder_and_nothing_else_rides_in_it(data_directory):
    """tools/build_natives.py writes the Ghost Lab's natives into data/lab/
    beside plugin/; the build ships exactly update_plan's LAB_NATIVES from it."""
    from sm64_events.core.update_plan import LAB_NATIVE_FOLDER, LAB_NATIVES
    lab = data_directory / "lab"
    lab.mkdir()
    for name in LAB_NATIVES:
        (lab / name).write_bytes(b"MZ native fixture")
    policy.data_inputs(data_directory)
    for name in LAB_NATIVES:
        policy.check_bundle_path(f"{LAB_NATIVE_FOLDER}/{name}", ui_paths={"index.html"})
    for path in (f"{LAB_NATIVE_FOLDER}/unknown_host.exe", "_internal/sm64_events/data/plugin/viewer_host.exe",
                 f"{LAB_NATIVE_FOLDER}/lab/viewer_host.exe", "_internal/sm64_events/data/viewer_host.exe"):
        with pytest.raises(ValueError, match="unexpected first-party data"):
            policy.check_bundle_path(path, ui_paths={"index.html"})
    (lab / "leftover.pdb").write_bytes(b"debug symbols")
    with pytest.raises(ValueError, match="unexpected first-party data input in lab/"):
        policy.data_inputs(data_directory)


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


def test_the_retired_native_name_holds_only_the_placeholder_text(monkeypatch):
    """Older apps refuse an update without a file at each retired native name
    (core/update_plan.py RETIRED_NATIVES), so a build carries the placeholder
    text there. The policy accepts exactly those bytes, as data; a program or
    any other file under that name is refused once no native ships there."""
    import hashlib
    from sm64_events.core.update_plan import BUNDLED_NATIVES, NATIVE_FOLDER, RETIRED_NATIVE_TEXT, RETIRED_NATIVES
    monkeypatch.setattr(policy, "NATIVE_FILES", BUNDLED_NATIVES)
    for name in RETIRED_NATIVES:
        path = f"{NATIVE_FOLDER}/{name}"
        policy.check_bundle_path(path, ui_paths={"index.html"},
                                 digest=hashlib.sha256(RETIRED_NATIVE_TEXT).hexdigest())
        for other in (None, hashlib.sha256(b"MZ a program").hexdigest()):
            with pytest.raises(ValueError, match="unexpected"):
                policy.check_bundle_path(path, ui_paths={"index.html"}, digest=other)


@pytest.mark.parametrize("path", [
    "SM64TrainerSim.exe", "_sim/python314.dll", "_sim/base_library.zip", "_sim/unicorn/lib/unicorn.dll",
    "_sim/unicorn-2.1.4.dist-info/METADATA", "_sim/numpy/_core/_multiarray_umath.cp314-win_amd64.pyd",
    "_sim/_sha256.pyd", "_sim/libffi-8.dll", "_sim/build-channel.json", "_sim/release-version.json",
])
def test_the_simulation_program_keeps_its_runtime_in_its_own_folder(path):
    policy.check_bundle_path(path, ui_paths={"index.html"})


@pytest.mark.parametrize("path", [
    "_sim/av/__init__.pyd", "_sim/av.libs/avcodec-61.dll", "_sim/pystray/_win32.pyc", "_sim/webview/util.pyc",
    "_sim/av-17.1.dist-info/METADATA", "_sim/sm64_events/data/maps/MappingUS.map", "_sim/notes.txt",
    "_internal/unicorn/lib/unicorn.dll", "_internal/unicorn-2.1.4.dist-info/METADATA", "_other/x.dll",
    "_sim/_ssl.pyd", "_sim/_hashlib.pyd", "_sim/libssl-3-x64.dll", "_sim/libcrypto-3-x64.dll",
    "_sim/pyaudiowpatch/__init__.pyc", "_sim/capstone/lib/capstone.dll", "_sim/error-reporting.json",
])
def test_unicorn_and_the_gpl3_packages_never_share_a_program(path):
    """The license ruling (2026-11-09): Unicorn (GPL-2.0-only) stays out of the
    Trainer, and the simulation program holds no PyAV (GPL-3.0 FFmpeg),
    pystray (LGPL-3.0), pyaudiowpatch or OpenSSL (Apache-2.0), capstone or
    webview (l0-sim-license.md)."""
    with pytest.raises(ValueError, match="unexpected|must|Unicorn|first-party"):
        policy.check_bundle_path(path, ui_paths={"index.html"})


def test_a_built_app_whose_programs_mix_is_refused(monkeypatch, tmp_path):
    held = {"SM64Trainer.exe": {"numpy", "av", "pystray", "sm64_events"},
            "SM64TrainerSim.exe": {"numpy", "unicorn", "sm64_events", "_sha256", "libffi-8.dll"}}
    unlicensed: list[str] = []
    monkeypatch.setattr(policy, "program_modules", lambda app, exe, contents: held[exe])
    monkeypatch.setattr(policy, "unlicensed_sim_modules", lambda app: unlicensed)
    with pytest.raises(ValueError, match="lacks SM64TrainerSim.exe"):
        policy.check_programs(tmp_path)
    (tmp_path / "SM64TrainerSim.exe").write_bytes(b"MZ")
    (tmp_path / "_sim").mkdir()
    policy.check_programs(tmp_path)
    held["SM64Trainer.exe"].add("unicorn")
    with pytest.raises(ValueError, match="SM64Trainer.exe holds unicorn"):
        policy.check_programs(tmp_path)
    held["SM64Trainer.exe"].discard("unicorn")
    held["SM64TrainerSim.exe"] |= {"av", "pystray"}
    with pytest.raises(ValueError, match="SM64TrainerSim.exe holds av, pystray"):
        policy.check_programs(tmp_path)
    held["SM64TrainerSim.exe"] -= {"av", "pystray"}
    held["SM64TrainerSim.exe"] |= {"_hashlib.pyd", "libcrypto-3-x64.dll", "av.libs"}
    with pytest.raises(ValueError, match="holds _hashlib, av, libcrypto"):
        policy.check_programs(tmp_path)
    held["SM64TrainerSim.exe"] -= {"_hashlib.pyd", "libcrypto-3-x64.dll", "av.libs"}
    unlicensed.append("state_resolver")
    with pytest.raises(ValueError, match="carries 1 modules of ours without '# SPDX-License-Identifier: "
                                         "GPL-2.0-or-later': state_resolver"):
        policy.check_programs(tmp_path)


def test_every_module_of_ours_in_the_simulation_program_needs_the_gpl2_line(monkeypatch, tmp_path):
    """unlicensed_sim_modules maps each archive module back to the file it was
    built from (sim_entry, a lab module, an sm64_events module or package) and
    ignores third-party modules; the line counts within a file's first three."""
    spdx = policy.SIM_SPDX + "\n"
    files = {
        "sim_entry.py": '"""The entry."""\n' + spdx,
        "tools/scene_lab/state_resolver.py": '"""No license line."""\n',
        "tools/scene_lab/ghost_resim.py": "#!/usr/bin/env python\n# coding: utf-8\n" + spdx,
        "tools/scene_lab/late_line.py": "a = 1\nb = 2\nc = 3\n" + spdx,
        "src/sm64_events/__init__.py": spdx,
        "src/sm64_events/core/__init__.py": "",
        "src/sm64_events/core/paths.py": spdx,
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(policy, "_REPO", tmp_path)
    monkeypatch.setattr(policy, "_archive_modules", lambda exe: {
        "sim_entry", "state_resolver", "ghost_resim", "late_line", "sm64_events", "sm64_events.core",
        "sm64_events.core.paths", "numpy", "numpy._core", "unicorn.unicorn_py3", "json"})
    assert policy.unlicensed_sim_modules(tmp_path) == ["late_line", "sm64_events.core", "state_resolver"]
