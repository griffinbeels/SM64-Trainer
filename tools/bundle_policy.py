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
from sm64_events.core.update_plan import BUNDLED_NATIVES

DATA_FILES = frozenset({
    "behaviours_jp.tsv", "behaviours_us.tsv", "defaults.seed.json",
    "error-reporting.json", "library_overrides.json", "oracle_glyphs_us.npz",
    "pad_glyphs_us.npz", "pad_icons_us.npz", "rank_standards.seed.json",
    "ranking_policy.json", "rom_candidates_jp.tsv", "segments.authored.json",
    "setup_baseline.json", "sheet_ladders.seed.json", "sheet_library.seed.json.gz", "symbols_jp.tsv",
    "symbols_us.tsv", "video_checks.seed.json.gz",
})
ROOT_DATA_FILES = frozenset({
    "behaviours_jp.tsv", "behaviours_us.tsv", "defaults.seed.json",
    "library_overrides.json", "rank_standards.seed.json", "sheet_ladders.seed.json",
    "sheet_library.seed.json.gz", "symbols_jp.tsv", "symbols_us.tsv",
    "video_checks.seed.json.gz", "ukiki.ico", "build-channel.json",
    "error-reporting.json", "release-version.json",
})
NATIVE_FILES = BUNDLED_NATIVES  # owned by core/update_plan.py
# Build outputs git ignores in the data folder. Neither is copied as data:
# the natives are bundled by name, ffmpeg.exe by build_exe's --add-binary.
BUILT_FOLDERS = frozenset({"plugin", "ffmpeg"})
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
        if (entry.is_symlink() or entry.is_junction() or entry.name not in DATA_FILES | BUILT_FOLDERS
                or entry.name in DATA_FILES and not entry.is_file()
                or entry.name in BUILT_FOLDERS and not entry.is_dir()):
            raise ValueError("unexpected first-party data input")
    files = tuple(directory / name for name in sorted(DATA_FILES))
    if any(not path.is_file() for path in files):
        raise ValueError("required first-party data input is missing")
    return files


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
        if relative not in {"SM64Trainer.exe", "SM64TrainerBeta.exe", "README-BETA.txt"} and not curated:
            raise ValueError("bundle contains an unexpected top-level file")
    elif parts[0] != "_internal":
        raise ValueError("bundle contains an unexpected runtime directory")
    elif parts[1].lower() == "sm64_events" and parts[1] != "sm64_events":
        raise ValueError("bundle contains an unexpected first-party input")
    elif len(parts) == 2:
        if (path.name not in ROOT_DATA_FILES and not (reviewed_collection and path.name == SEED_DESCRIPTOR)
                and path.suffix.lower() not in {".dll", ".pyd", ".exe", ".zip"}):
            raise ValueError("bundle contains an unexpected runtime input")
    elif _retired_placeholder(relative, digest):
        return
    elif parts[1:3] == ("sm64_events", "data"):
        suffix = path.relative_to("_internal/sm64_events/data").as_posix()
        if suffix not in DATA_FILES and suffix not in {"plugin/" + item for item in NATIVE_FILES}:
            raise ValueError("bundle contains an unexpected first-party data input")
    elif parts[1:3] == ("sm64_events", "ui"):
        if path.relative_to("_internal/sm64_events/ui").as_posix() not in ui_paths:
            raise ValueError("bundle contains an unexpected first-party UI input")
    elif parts[1] == "sm64_events":
        raise ValueError("bundle contains an unexpected first-party input")
