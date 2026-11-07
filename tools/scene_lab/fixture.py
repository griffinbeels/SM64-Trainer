"""Swap this checkout's graphics plugin into Project64 for one scene-tap fixture, then back.

    fixture.py install --plugin-dir <Project64\\Plugin>
    fixture.py restore --plugin-dir <Project64\\Plugin>

Only with Griffin's go-ahead, and only while Project64 is closed: install
backs up the installed sm64_trainer_gfx.dll to
%LOCALAPPDATA%\\SM64Trainer\\scene-lab\\plugin-backup and copies this
checkout's built plugin over it (the registry and every other file are
untouched; the plugin is one file since 2026-11-07, and an older install's
ini and renderer file beside it are ignored by it). restore puts the
backed-up file back byte-for-byte and removes the backup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402

PAIR = ("sm64_trainer_gfx.dll",)   # the files swapped: the plugin alone
BUILT = ROOT / "src/sm64_events/data/plugin"


def backup_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "SM64Trainer" / "scene-lab" / "plugin-backup"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pj64_running() -> bool:
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Project64.exe", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, check=False, **quiet_spawn_kwargs())
    return "Project64.exe" in result.stdout


def install(plugin_dir: Path) -> int:
    if pj64_running():
        raise SystemExit("Project64 is running: close it first")
    backup = backup_dir()
    if (backup / "manifest.json").is_file():
        raise SystemExit(f"a backup already exists at {backup}: restore first")
    backup.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name in PAIR:
        installed, built = plugin_dir / name, BUILT / name
        if not installed.is_file() or not built.is_file():
            raise SystemExit(f"missing {installed if not installed.is_file() else built}")
        shutil.copy2(installed, backup / name)
        manifest[name] = {"installed": digest(installed), "fixture": digest(built)}
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for name in PAIR:
        shutil.copy2(BUILT / name, plugin_dir / name)
        if digest(plugin_dir / name) != manifest[name]["fixture"]:
            raise SystemExit(f"copy of {name} did not verify; run restore")
    print("installed the fixture plugin; backup at", backup)
    return 0


def restore(plugin_dir: Path) -> int:
    if pj64_running():
        raise SystemExit("Project64 is running: close it first")
    backup = backup_dir()
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    for name, hashes in manifest.items():
        if digest(backup / name) != hashes["installed"]:
            raise SystemExit(f"backup of {name} does not verify; leaving everything in place")
    for name, hashes in manifest.items():
        shutil.copy2(backup / name, plugin_dir / name)
        if digest(plugin_dir / name) != hashes["installed"]:
            raise SystemExit(f"restore of {name} did not verify")
    shutil.rmtree(backup)
    print("restored the original plugin")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("install", "restore"))
    parser.add_argument("--plugin-dir", type=Path, default=os.environ.get("SM64_PJ64_PLUGIN"))
    args = parser.parse_args()
    if not args.plugin_dir or not (Path(args.plugin_dir) / PAIR[0]).is_file():
        raise SystemExit("--plugin-dir must name the Project64 Plugin folder with the trainer installed")
    return (install if args.action == "install" else restore)(Path(args.plugin_dir))


if __name__ == "__main__":
    raise SystemExit(main())
