"""Where the Ghost Lab's inputs come from: the ROMs, the symbol maps and Project64's Plugin folder.

Two sources, and this module is the one door to both:

- A developer's lab config: a lab.json of absolute paths ({"rom", "packages", "pj64_plugin", "map"}, see
  server/scene_lab_api.py). `SM64_SCENE_LAB` names it for the server (main.py), and a tools/scene_lab command
  run from a source checkout reads the lab root's lab.json, exactly as it always has (`tool_config`).
- The installed app, which has no lab.json (`app_inputs`): each region's ROM from the ROM store
  (sm64_events/scenelab/roms.py, interface I3: a private copy of each Usamune ROM the Trainer found), the maps
  bundled with the app (core/paths.py::bundled_lab_file, interface I2) and the Plugin folder the setup screen
  found (the server passes it; a tool never needs it).

`IN_APP` is the one switch for whether the installed app shows the Ghost Lab. Griffin's decision D2 (pending,
2026-11-09): "Ghost Lab on by default as its own sidebar entry"; on, as recommended. A source checkout without
`SM64_SCENE_LAB` stays without a lab, as before (`app_lab_on` is False there).

The app's lab is US first: the simulation that re-builds ghosts runs on the US cartridge only
(tools/scene_lab/stage_sim.py, JP_NOT_YET), so `app_inputs` picks the US ROM when the store has it and the JP ROM
otherwise (a JP player still opens his own attempt scenes). Every sentence here is shown to a player as it is.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

IN_APP = True                      # D2: the installed app shows the Ghost Lab (one place to turn it off)
REGIONS = ("us", "jp")             # the order the app's lab prefers a ROM in
MAP_NAMES = {"us": "MappingUS.map", "jp": "MappingJP.map"}   # bundled beside the natives (interface I2)
ENV = "SM64_SCENE_LAB"
NO_STORE = ("This copy of the Trainer cannot look for the game's ROM, which the Ghost Lab needs. "
            "Reinstall the Trainer to fix it.")
NO_ROM = ("The Ghost Lab needs the Super Mario 64 Usamune v1.93u ROM. Open it in Project64 once while the "
          "Trainer is running.")
NO_MAP = ("This copy of the Trainer is missing a file the Ghost Lab needs (the game's address map). "
          "Reinstall the Trainer to get it back.")
NO_PLUGIN = ("The Ghost Lab needs Project64 set up with the Trainer. Finish the setup steps, "
             "then come back here.")


def frozen() -> bool:
    """The installed app (PyInstaller). A seam: tests replace this, never sys.frozen (paths.py reads that one
    and would aim the test at his real user data)."""
    from sm64_events.core.paths import is_frozen
    return is_frozen()


def app_lab_on() -> bool:
    """Whether this process shows the Ghost Lab without a lab config: the installed app, with IN_APP on."""
    return IN_APP and frozen()


def _store():
    """The ROM store (interface I3), or None when this build lacks it."""
    try:
        return importlib.import_module("sm64_events.scenelab.roms")
    except ImportError:
        return None


def _lab_file():
    """core/paths.py::bundled_lab_file (interface I2), or None when this build lacks it."""
    from sm64_events.core import paths
    return getattr(paths, "bundled_lab_file", None)


def _existing(path) -> Path | None:
    return Path(path) if path is not None and Path(path).is_file() else None


def app_inputs(*, store=None, lab_file=None, plugin=None, lab_root: Path | None = None) -> dict:
    """The installed app's lab inputs, read now (a ROM the store finds later shows up on the next call):

        {"rom", "map", "region"}     the ROM the lab runs and its map (US first; None when none is there)
        {"roms", "maps"}             each region's ROM and map, None where there is none ({"us": ..., "jp": ...})
        "rom_problems"               each region's sentence when it has no ROM (the store's missing_reason)
        "pj64_plugin"                Project64's Plugin folder, None when setup has not found it
        "problems"                   what stops any scene from opening, in the words the page shows
        "lab_root"                   where the lab keeps its files (None: core/paths.py::scene_lab_root)

    store: a module or object with rom_for(region) and missing_reason(region) (sm64_events/scenelab/roms.py);
    lab_file(name) -> Path | None (core/paths.py::bundled_lab_file); plugin() -> the Plugin folder or None."""
    store = _store() if store is None else store
    lab_file = _lab_file() if lab_file is None else lab_file
    problems = []
    roms, rom_problems = {}, {}
    for region in REGIONS:
        found = None
        if store is not None:
            try:
                found = _existing(store.rom_for(region))
            except OSError:
                found = None
        roms[region] = found
        if found is None:
            reason = store.missing_reason(region) if store is not None else None
            rom_problems[region] = reason or (NO_STORE if store is None else NO_ROM)
    maps = {region: _existing(lab_file(MAP_NAMES[region])) if lab_file is not None else None for region in REGIONS}
    region = next((region for region in REGIONS if roms[region] is not None), None)
    if region is None:
        problems.append(rom_problems["us"])
    elif maps[region] is None:
        problems.append(NO_MAP)
    folder = None
    try:
        folder = plugin() if plugin is not None else None
    except Exception:  # noqa: BLE001 - a setup read that fails reads as "not set up yet"
        folder = None
    folder = Path(folder) if folder and Path(folder).is_dir() else None
    if folder is None:
        problems.append(NO_PLUGIN)
    return {"rom": roms[region] if region else None, "map": maps[region] if region else None, "region": region,
            "roms": roms, "maps": maps, "rom_problems": rom_problems, "pj64_plugin": folder,
            "problems": problems, "lab_root": lab_root}


def tool_config(lab_json: Path | str | None = None) -> dict:
    """What a tools/scene_lab command reads when its caller named no ROM or map. A named lab.json is read; else,
    from a source checkout, the lab root's lab.json, as every command always read it; else (the installed app)
    `SM64_SCENE_LAB`'s lab.json when a developer set it, and otherwise the app's own inputs (`app_inputs`, whose
    "roms" and "maps" give each region's file). The keys a command reads ("rom", "map") are in both shapes."""
    if lab_json is None and frozen():
        named = os.environ.get(ENV)
        if not named:
            return app_inputs()
        lab_json = named
    if lab_json is None:
        from sm64_events.core.paths import scene_lab_root
        lab_json = scene_lab_root() / "lab.json"
    return json.loads(Path(lab_json).read_text(encoding="utf-8"))
