# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""The stage cache's file names, without the simulation (stage_sim.py makes the files; this names them).

The server asks whether a stage is made before it starts a warm child (server/scene_resim.py). It must not import
stage_sim for that: stage_sim imports frame_sim, the simulation, and the server process stays clear of it (the
simulation runs in its own processes, tools/scene_lab/sim_boundary.py). So the names live here, and stage_sim reads
them from here too: one owner, and a cache check outside the simulation names exactly the files a warm writes.

Beside the files, `done-<recipe>.txt` lists the stages a warm tried and could not make (a warm that failed): the
warmer skips them on later runs instead of trying each again every time the Trainer starts (`failed`, `fail`).
A new STAGE_VERSION or another ROM is another recipe, so it tries them again.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

STAGE_VERSION = 2                # the reset's recipe: a change makes every reset (and start-file base) anew
_lock = threading.Lock()


def recipe(rom: bytes) -> str | None:
    """What every cached boot and reset of this cartridge is named by (STAGE_VERSION, its build, its bytes); None for a
    ROM that is neither Usamune build. stage_sim.Stages.key and `made` both read it."""
    from sm64_events.memory.version_probe import version_from_header  # noqa: PLC0415
    version = version_from_header(rom[:0x40])
    return None if version is None else f"v{STAGE_VERSION}-{version}-{hashlib.sha256(rom).hexdigest()[:12]}"


def boot_file(cache: Path, recipe_name: str) -> Path:
    return Path(cache) / f"boot-{recipe_name}.npz"


def reset_file(cache: Path, recipe_name: str, level: int, area: int, act: int) -> Path:
    return Path(cache) / f"reset-{recipe_name}-{level}-{area}-{act}.npz"


def made(cache: Path, recipe_name: str, level: int, area: int, act: int) -> bool:
    """Whether `cache` already holds what a warm of that stage makes first: the cold boot's RAM and that place's
    practice reset. A warm's packer seed for the level comes with the reset (the build or warm that made the reset
    packed that level's frames, sim_scene.Seed.keep)."""
    return boot_file(cache, recipe_name).is_file() and reset_file(cache, recipe_name, level, area, act).is_file()


def _failed_file(cache: Path, recipe_name: str) -> Path:
    return Path(cache) / f"done-{recipe_name}.txt"


def failed(cache: Path, recipe_name: str) -> set[tuple[int, int, int]]:
    """The stages a warm of this recipe tried and could not make, on any earlier run."""
    try:
        lines = _failed_file(cache, recipe_name).read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    stages = set()
    for line in lines:
        try:
            level, area, act = (int(part) for part in line.split())
        except ValueError:
            continue
        stages.add((level, area, act))
    return stages


def fail(cache: Path, recipe_name: str, level: int, area: int, act: int) -> None:
    """Remember that a warm of this stage could not make it, so later runs skip it."""
    with _lock:
        if (level, area, act) in failed(cache, recipe_name):
            return
        Path(cache).mkdir(parents=True, exist_ok=True)
        with _failed_file(cache, recipe_name).open("a", encoding="utf-8") as handle:
            handle.write(f"{level} {area} {act}\n")
            handle.flush()
            os.fsync(handle.fileno())
