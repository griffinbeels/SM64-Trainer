"""Approved first-party bundle inputs and shared staging/archive path checks.

This is an input/path policy, not a credential detector. Third-party runtime
collection remains PyInstaller's responsibility; secrets also need source scans.
"""
from __future__ import annotations

from collections.abc import Collection
import hashlib
from pathlib import Path, PurePosixPath
import subprocess

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.core.update_plan import BUNDLED_NATIVES, LAB_NATIVES
from sm64_events.scenelab_code import SIM_PROGRAM, child_modules

_REPO = Path(__file__).resolve().parents[1]

#: STROOP's symbol maps, committed under the data folder's maps/ at the commit
#: tools/scene_lab/cold_boot.py pins (STROOP_COMMIT): byte for byte the maps
#: the Ghost Lab has always read. A changed byte refuses the build.
MAP_FOLDER = "maps"
PINNED_MAPS = {
    "MappingUS.map": "d942ba9781aafb500c5bd7a599d15a41a191c6e9fe65b95e6c384429bec2d3b1",
    "MappingJP.map": "5e51852905862c90173e90cd6b58c591a82d735d63575cf5ffab04c148eec9f2",
}
#: The installed app's second program (sm64_events/scenelab_code.py): its exe
#: beside the Trainer's and its own contents folder beside `_internal`.
SIM_EXE = SIM_PROGRAM
SIM_CONTENTS = "_sim"
#: The build identity and version the simulation program carries, the same
#: files as the Trainer's (tools/build_exe.py sim_args).
SIM_ROOT_FILES = frozenset({"build-channel.json", "release-version.json"})
#: The lab modules SIM_EXE runs as children, collected into it by name; it
#: carries whatever they import, which SIM_FORBIDDEN bounds.
SIM_MODULES = child_modules(SIM_PROGRAM)
#: Top-level modules each program must never hold (the license ruling,
#: 2026-11-09, and .tasks/features/ghost-lab-beta/l0-sim-license.md): Unicorn
#: is GPL-2.0-only, so it stays out of the Trainer, which loads PyAV's GPL-3.0
#: FFmpeg and LGPL-3.0 pystray. The simulation program holds nothing that
#: cannot share a process with GPL-2.0-only code: no PyAV, pystray or
#: pyaudiowpatch (Apache-2.0), no OpenSSL (Apache-2.0: `_ssl`, `_hashlib` and
#: their libssl/libcrypto DLLs; hashlib falls back to CPython's built-in hash
#: modules), and none of the Trainer's webview or the scan-only capstone.
#: A name matches itself and its `-version`/`.suffix` forms (av.libs,
#: av-17.1.dist-info, libssl-3.dll).
TRAINER_FORBIDDEN = frozenset({"unicorn"})
SIM_FORBIDDEN = frozenset({"av", "pystray", "pyaudiowpatch", "capstone", "webview",
                           "_ssl", "_hashlib", "libssl", "libcrypto"})
#: The line every module of ours the simulation program carries begins with
#: (l0-sim-license.md, section 1; Griffin's D9, 2026-11-09).
SIM_SPDX = "# SPDX-License-Identifier: GPL-2.0-or-later"

DATA_FILES = frozenset({
    "behaviours_jp.tsv", "behaviours_us.tsv", "build_shifts.tsv", "defaults.seed.json",
    "error-reporting.json", "library_overrides.json", "oracle_glyphs_us.npz",
    "pad_glyphs_us.npz", "pad_icons_us.npz", "rank_standards.seed.json",
    "ranking_policy.json", "rom_candidates_jp.tsv", "segments.authored.json",
    "setup_baseline.json", "sheet_ladders.seed.json", "sheet_library.seed.json.gz", "symbols_jp.tsv",
    "symbols_us.tsv", "video_checks.seed.json.gz",
})
#: THE list of Ghost Lab modules (tools/scene_lab) the installed app carries,
#: by the bare names they import each other by (sm64_events/scenelab_code.py).
#: It is the import closure of every lab module the app reaches;
#: tests/test_scenelab_code.py recomputes that closure and fails on a module
#: missing here, one listed but unreached, or a name that shadows another module.
#: Left out on purpose: audio_bench, audio_sync_check, e1, e3_measure, fixture,
#: handoff_relocate and slot_library, developer and evidence tools nothing reaches.
LAB_MODULES = (
    "attempt_cutter", "attempt_scenes", "attempt_span", "audio_capture", "audio_clock", "audio_layer",
    "audio_session", "camera_paths", "camera_presets", "camera_rig", "camera_state", "closure",
    "cold_boot", "cpu_hooks", "e2_georender", "evaluator", "evaluator_child", "export_sound", "fake_viewer_host",
    "frame_sim", "ghost_resim", "ghost_sound", "gpu_gate", "handoff_slot", "handoff_try", "hud",
    "live_audio", "live_spool", "moment_slot", "package", "pick", "picture_encoder", "provenance",
    "recamera", "replay", "rig_path", "savestate_frames", "scene_audio", "scene_export",
    "scene_session", "sim_boundary", "sim_scene", "slot_atlas", "stage_ghosts", "stage_sim", "start_check",
    "state_resolver", "stems_check", "stems_layer", "stems_live", "stems_probe", "tap", "viewer",
    "webview_pictures",
)
ROOT_DATA_FILES = frozenset({
    "behaviours_jp.tsv", "behaviours_us.tsv", "defaults.seed.json",
    "library_overrides.json", "rank_standards.seed.json", "sheet_ladders.seed.json",
    "sheet_library.seed.json.gz", "symbols_jp.tsv", "symbols_us.tsv",
    "video_checks.seed.json.gz", "ukiki.ico", "build-channel.json",
    "error-reporting.json", "release-version.json",
})
NATIVE_FILES = BUNDLED_NATIVES  # owned by core/update_plan.py
# Build outputs in the data folder. None is copied as data: the natives and
# the Ghost Lab's natives (lab/, tools/build_natives.py writes them) are
# bundled by name, ffmpeg.exe by build_exe's --add-binary.
BUILT_FOLDERS = frozenset({"plugin", "ffmpeg", "lab"})
#: What the lab/ build folder may hold: update_plan's LAB_NATIVES, nothing else.
LAB_FOLDER = "lab"
COLLECTION_NAME = "griffman1212.sm64ghosts"
SEED_DESCRIPTOR = "beta-ghost-seed.json"
VENDOR_HIDDEN = {
    "_internal/numpy/f2py/tests/src/assumed_shape/.f2py_f2cmap":
        "cdfb8e4a19aea2dcdc2c84039d515a011c2f33aea22eb398ce9aae2060db88a5",
    "_internal/numpy/f2py/tests/src/f2cmap/.f2py_f2cmap":
        "7f0b33ca669685c58edbdeaee538475b9c8c02416107a12d1d6aaaa5cf450152",
}
_PRIVATE_NAMES = frozenset({
    "tracker.db", "tracker.lock", "server.pid", "window.json", "update_state.json",
    "capture_layer.json", "replay_settings.json", "ui_log.jsonl", "library_adoptions.json",
    "tracker_mode.json", "climb-profile.json", "developer-simulation.json",
})
_PRIVATE_DIRS = frozenset({
    "replays", "logs", "replay_buffer", "ghost_population", "community_cache",
    "compare_cache", "eos-sdk", "eos-private",
})
_PRIVATE_SUFFIXES = frozenset({
    ".db", ".sqlite", ".sqlite3", ".gho", ".ghost", ".ghc1", ".sm64ghosts",
    ".mp4", ".mkv", ".p12", ".pfx", ".key",
})
_UI_SUFFIXES = frozenset({".js", ".mjs", ".html", ".css", ".png", ".svg", ".webp", ".ttf", ".md", ".txt"})
_UI_SOURCE = "src/sm64_events/ui/"


def data_inputs(directory: Path) -> tuple[Path, ...]:
    """Refuse incidental local files instead of copying the package-data folder."""
    if directory.is_symlink() or directory.is_junction():
        raise ValueError("unexpected first-party data input")
    for entry in directory.iterdir():
        if (entry.is_symlink() or entry.is_junction()
                or entry.name not in DATA_FILES | BUILT_FOLDERS | {MAP_FOLDER}
                or entry.name in DATA_FILES and not entry.is_file()
                or entry.name in BUILT_FOLDERS | {MAP_FOLDER} and not entry.is_dir()):
            raise ValueError("unexpected first-party data input")
    lab = directory / LAB_FOLDER
    if lab.is_dir() and any(entry.is_symlink() or entry.is_junction() or not entry.is_file()
                            or entry.name not in LAB_NATIVES for entry in lab.iterdir()):
        raise ValueError("unexpected first-party data input in lab/")
    files = tuple(directory / name for name in sorted(DATA_FILES))
    if any(not path.is_file() for path in files):
        raise ValueError("required first-party data input is missing")
    return files


def map_inputs(directory: Path) -> tuple[Path, ...]:
    """The data folder's maps/: exactly PINNED_MAPS, each with its reviewed bytes."""
    folder = directory / MAP_FOLDER
    if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
        raise ValueError("required first-party data input is missing: maps/")
    found = {entry.name for entry in folder.iterdir()}
    if found != set(PINNED_MAPS) or any((folder / name).is_symlink() or not (folder / name).is_file()
                                        for name in found):
        raise ValueError("unexpected or missing first-party data input in maps/")
    for name, pinned in PINNED_MAPS.items():
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != pinned:
            raise ValueError(f"first-party data input maps/{name} is not the reviewed file")
    return tuple(folder / name for name in sorted(PINNED_MAPS))


def _held(names: Collection[str], forbidden: Collection[str]) -> set[str]:
    """The forbidden names among `names`, each matching itself and its
    `-version` and `.suffix` forms."""
    return {item for item in forbidden for name in names
            if name == item or name.startswith((item + "-", item + "."))}


def _archive_modules(exe: Path) -> set[str]:
    """Every module name in a built program's embedded archive."""
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(exe))
    return {module for entry in archive.toc if entry.endswith(".pyz")
            for module in archive.open_embedded_archive(entry).toc}


def program_modules(app_dir: Path, exe_name: str, contents: str) -> set[str]:
    """What one built program holds, by lowercased top-level name: the modules
    in its exe's embedded archive and every entry of its contents folder
    (packages, `.libs` and `.dist-info` folders, extensions, DLLs)."""
    names = {module.split(".")[0].lower() for module in _archive_modules(app_dir / exe_name)}
    return names | {child.name.lower() for child in (app_dir / contents).iterdir() if child.name != "licenses"}


def _source_of(module: str) -> Path | None:
    """The repository file a bundled module of ours was built from, or None
    for a module that is not ours."""
    top, *rest = module.split(".")
    if top == "sim_entry" and not rest:
        return _REPO / "sim_entry.py"
    if not rest and (_REPO / "tools" / "scene_lab" / f"{top}.py").is_file():
        return _REPO / "tools" / "scene_lab" / f"{top}.py"
    if top == "sm64_events":
        base = _REPO / "src" / top / Path(*rest) if rest else _REPO / "src" / top
        return base / "__init__.py" if base.is_dir() else base.with_suffix(".py")
    return None


def unlicensed_sim_modules(app_dir: Path) -> list[str]:
    """Our modules in the simulation program whose source does not begin
    with SIM_SPDX (within its first three lines)."""
    missing = []
    for module in sorted(_archive_modules(app_dir / SIM_EXE)):
        source = _source_of(module)
        if source is None:
            continue
        head = source.read_text(encoding="utf-8").splitlines()[:3] if source.is_file() else []
        if SIM_SPDX not in (line.rstrip() for line in head):
            missing.append(module)
    return missing


def check_programs(app_dir: Path, trainer: str = "SM64Trainer.exe") -> None:
    """Refuse a built app whose Trainer holds Unicorn; that lacks the
    simulation program; whose simulation program holds anything SIM_FORBIDDEN
    names; or one of whose modules of ours there lacks the GPL-2.0-or-later line."""
    held = _held(program_modules(app_dir, trainer, "_internal"), TRAINER_FORBIDDEN)
    if held:
        raise ValueError(f"{trainer} holds {', '.join(sorted(held))}, which must stay in {SIM_EXE}")
    if not (app_dir / SIM_EXE).is_file() or not (app_dir / SIM_CONTENTS).is_dir():
        raise ValueError(f"the app lacks {SIM_EXE} and its {SIM_CONTENTS} folder")
    held = _held(program_modules(app_dir, SIM_EXE, SIM_CONTENTS), SIM_FORBIDDEN)
    if held:
        raise ValueError(f"{SIM_EXE} holds {', '.join(sorted(held))}, which must never share a process "
                         "with Unicorn")
    missing = unlicensed_sim_modules(app_dir)
    if missing:
        raise ValueError(f"{SIM_EXE} carries {len(missing)} modules of ours without '{SIM_SPDX}': "
                         + ", ".join(missing[:12]) + (" ..." if len(missing) > 12 else ""))


def tracked_ui_paths(repo: Path) -> frozenset[str]:
    """The public source tree explicitly selects UI files; local extras never ship."""
    result = subprocess.run(["git", "ls-files", "-z", "--", _UI_SOURCE], cwd=repo,
                            capture_output=True, check=True, **quiet_spawn_kwargs())
    names = result.stdout.decode("utf-8").rstrip("\0").split("\0")
    paths = frozenset(name.removeprefix(_UI_SOURCE) for name in names if name)
    if ("index.html" not in paths or any(not name.startswith(_UI_SOURCE) for name in names if name)
            or any(PurePosixPath(name).suffix not in _UI_SUFFIXES
                   or any(part.startswith(".") for part in PurePosixPath(name).parts) for name in paths)):
        raise ValueError("unexpected or missing tracked UI input")
    return paths


def ui_inputs(repo: Path) -> tuple[Path, ...]:
    paths = tracked_ui_paths(repo)
    directory = repo / _UI_SOURCE
    if directory.is_symlink() or directory.is_junction():
        raise ValueError("unexpected first-party UI input")
    actual = set()
    for entry in directory.rglob("*"):
        if (entry.is_symlink() or entry.is_junction()
                or not entry.resolve().is_relative_to(directory.resolve())):
            raise ValueError("unexpected first-party UI input")
        if entry.is_file():
            actual.add(entry.relative_to(directory).as_posix())
    if actual != paths:
        raise ValueError("unexpected or missing first-party UI input")
    return tuple(directory / name for name in sorted(paths))


def _check_sim_path(parts: tuple[str, ...]) -> None:
    """A file in the simulation program's contents folder: its runtime and
    third-party packages, never a package SIM_FORBIDDEN names and never the
    Trainer's own first-party files, which ship in `_internal`."""
    if len(parts) < 2:
        raise ValueError("bundle contains an unexpected runtime directory")
    if _held({parts[1].lower()}, SIM_FORBIDDEN):
        raise ValueError(f"bundle contains {parts[1]} in {SIM_CONTENTS}, which must never share a process with Unicorn")
    if parts[1].lower() == "sm64_events":
        raise ValueError(f"bundle contains a first-party input in {SIM_CONTENTS}")
    if (len(parts) == 2 and parts[1] not in SIM_ROOT_FILES
            and PurePosixPath(parts[1]).suffix.lower() not in {".dll", ".pyd", ".exe", ".zip"}):
        raise ValueError(f"bundle contains an unexpected runtime input in {SIM_CONTENTS}")


def _retired_placeholder(relative: str, digest: str | None) -> bool:
    """The placeholder at a retired native name (core/update_plan.py
    RETIRED_NATIVES): plain-text data with exactly RETIRED_NATIVE_TEXT's
    bytes, never a native. Any other file under that name is refused."""
    from sm64_events.core.update_plan import NATIVE_FOLDER, RETIRED_NATIVE_TEXT, RETIRED_NATIVES
    return (relative in {f"{NATIVE_FOLDER}/{name}" for name in RETIRED_NATIVES}
            and digest == hashlib.sha256(RETIRED_NATIVE_TEXT).hexdigest())


def check_bundle_path(relative: str, *, ui_paths: Collection[str], is_dir: bool = False,
                      digest: str | None = None, reviewed_collection: bool = False) -> None:
    """Apply the same private-file and first-party allowlists to folders and ZIPs."""
    path = PurePosixPath(relative)
    parts = path.parts
    lowered = {part.lower() for part in parts}
    name = path.name.lower()
    curated = reviewed_collection and relative == COLLECTION_NAME
    vendor = not is_dir and VENDOR_HIDDEN.get(relative) is not None and VENDOR_HIDDEN[relative] == digest
    if (not parts or path.is_absolute() or path.as_posix() != relative or "\\" in relative or ".." in parts
            or any(":" in part or part.endswith((".", " ")) or any(ord(c) < 32 for c in part) for part in parts)
            or lowered & _PRIVATE_DIRS or name in _PRIVATE_NAMES
            or any(part.startswith(".") for part in parts) and not vendor
            or path.suffix.lower() in _PRIVATE_SUFFIXES and not curated
            or name.startswith("sm64trainersetup")):
        raise ValueError("bundle contains an unexpected private file")
    if is_dir:
        return
    if len(parts) == 1:
        if (relative not in {"SM64Trainer.exe", "SM64TrainerBeta.exe", "README-BETA.txt", SIM_EXE}
                and not curated):
            raise ValueError("bundle contains an unexpected top-level file")
    elif parts[0] == SIM_CONTENTS:
        _check_sim_path(parts)
    elif parts[0] != "_internal":
        raise ValueError("bundle contains an unexpected runtime directory")
    else:
        _check_internal_path(relative, ui_paths=ui_paths, digest=digest, reviewed_collection=reviewed_collection)


def _check_internal_path(relative: str, *, ui_paths: Collection[str], digest: str | None,
                         reviewed_collection: bool) -> None:
    """A file in the Trainer's contents folder `_internal`: its runtime and
    third-party packages (never Unicorn), and only the reviewed first-party
    data, natives and UI files."""
    path = PurePosixPath(relative)
    parts = path.parts
    if _held({parts[1].lower()}, TRAINER_FORBIDDEN):
        raise ValueError(f"bundle contains {parts[1]}, which must stay out of SM64Trainer.exe")
    elif parts[1].lower() == "sm64_events" and parts[1] != "sm64_events":
        raise ValueError("bundle contains an unexpected first-party input")
    elif len(parts) == 2:
        if (path.name not in ROOT_DATA_FILES and not (reviewed_collection and path.name == SEED_DESCRIPTOR)
                and path.suffix.lower() not in {".dll", ".pyd", ".exe", ".zip"}):
            raise ValueError("bundle contains an unexpected runtime input")
    elif _retired_placeholder(relative, digest):
        pass
    elif parts[1:3] == ("sm64_events", "data"):
        suffix = path.relative_to("_internal/sm64_events/data").as_posix()
        if (suffix not in DATA_FILES and suffix not in {"plugin/" + item for item in NATIVE_FILES}
                and suffix not in {f"{LAB_FOLDER}/{item}" for item in LAB_NATIVES}
                and suffix not in {f"{MAP_FOLDER}/{item}" for item in PINNED_MAPS}):
            raise ValueError("bundle contains an unexpected first-party data input")
    elif parts[1:3] == ("sm64_events", "ui"):
        if path.relative_to("_internal/sm64_events/ui").as_posix() not in ui_paths:
            raise ValueError("bundle contains an unexpected first-party UI input")
    elif parts[1] == "sm64_events":
        raise ValueError("bundle contains an unexpected first-party input")
