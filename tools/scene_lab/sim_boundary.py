"""The simulation processes: the only processes that load Unicorn, and what they never load beside it.

Unicorn 2.1.4 as shipped is GPL-2.0-only (the Ghost Lab beta's license ruling, 2026-11-09: QEMU's v2-only
files are in every target), and GPL-2.0-only code cannot share a program with GPL-3.0 or LGPL-3.0 code. The
Trainer's own process loads both: PyAV's FFmpeg (a GPL-3.0 build) in the server and pystray (LGPL-3.0) in the
window. So Unicorn runs only in child processes of its own, the simulation processes, and those never load
PyAV, pystray or any other (L)GPL-3.0 library. In the installed app they are the separate program
SM64TrainerSim.exe, which carries Unicorn, numpy and the Trainer modules in MODULES: the code licensed
GPL-2.0-or-later so that it may run beside Unicorn (licenses/README.md says which code that is).

    ENTRIES    each simulation process's child flag and the module it runs
    FORBIDDEN  the libraries a simulation process never imports (the sim program is built without them)
    MODULES    the Trainer modules a simulation process may import (tools/scene_lab by bare name, then the
               package's); anything else is a new module that needs its license looked at first
    enter()    the first call of every simulation entry point (its __main__, and the sim program's dispatch)

Everything else the Trainer starts (the cutter, the stems child, the ghost sound maker, the path reader, the
picture encoder, the export child) loads no Unicorn: a free camera anywhere asks evaluator_child.py's process.
tests/test_sim_boundary.py holds these lists to the processes as they run and to their import graph.
"""
from __future__ import annotations

import importlib.abc
import sys

from sm64_events.scenelab_code import LAB_CHILDREN, SIM_PROGRAM  # noqa: E402

# The one table of which program runs each lab child lives in sm64_events/scenelab_code.py; a simulation process
# is exactly a child it gives to the simulation program.
ENTRIES = {flag: module for flag, (module, program) in LAB_CHILDREN.items() if program == SIM_PROGRAM}

FORBIDDEN = ("av", "pystray")

# Imported only inside a function that a simulation process never calls; the sim program is built without the
# library (each pair: module, library, why it never runs there). tests/test_sim_boundary.py refuses any other
# path from a simulation entry to a FORBIDDEN library.
LAZY_FORBIDDEN = {
    ("audio_layer", "av"): "FLAC encode and decode of a scene's sound: the cutter, the stems child, the export "
                           "and the server's sound blocks, never the simulation (it reads the layer's index only)",
}

MODULES = (
    # tools/scene_lab, by bare name
    "audio_clock", "audio_layer", "audio_session", "camera_state", "cold_boot", "cpu_hooks", "e2_georender",
    "evaluator", "evaluator_child", "frame_sim", "ghost_resim", "handoff_slot", "live_spool", "moment_slot",
    "package", "pick", "provenance", "savestate_frames", "sim_boundary", "sim_scene", "slot_atlas", "stage_cache", "stage_sim",
    "start_check", "state_resolver", "stems_check", "stems_layer", "stems_probe", "tap",
    # src/sm64_events
    "sm64_events", "sm64_events.core", "sm64_events.core.build_channel", "sm64_events.core.childproc",
    "sm64_events.core.events", "sm64_events.core.paths", "sm64_events.core.snapshot", "sm64_events.core.update_plan",
    "sm64_events.detectors", "sm64_events.detectors.anchors", "sm64_events.detectors.counter_epoch",
    "sm64_events.ghost", "sm64_events.ghost.clip", "sm64_events.ghost.native_layout", "sm64_events.ghost.start_frame",
    "sm64_events.inputs", "sm64_events.inputs.frame",
    "sm64_events.memory", "sm64_events.memory.addresses", "sm64_events.memory.base", "sm64_events.memory.behaviours",
    "sm64_events.memory.ghost_layout", "sm64_events.memory.layout", "sm64_events.memory.objects",
    "sm64_events.memory.version_probe", "sm64_events.scenelab_code",
    "sm64_events.slotlibrary", "sm64_events.slotlibrary.atlas", "sm64_events.slotlibrary.relocate",
    "sm64_events.slotlibrary.store", "sm64_events.slotlibrary.usamune",
)


class Refused(ImportError):
    """A FORBIDDEN library asked for inside a simulation process."""


class _Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in FORBIDDEN:
            raise Refused(f"{name} cannot load in a simulation process: Unicorn there is GPL-2.0-only, and "
                          f"{name.partition('.')[0]} is (or carries) GPL-3.0 code (tools/scene_lab/sim_boundary.py)")
        return None


def loaded_forbidden(modules=None) -> list[str]:
    """The FORBIDDEN libraries among `modules` (default: this process's)."""
    names = sys.modules if modules is None else modules
    return sorted({name.partition(".")[0] for name in names} & set(FORBIDDEN))


def enter() -> None:
    """Makes this process a simulation process: SystemExit when a FORBIDDEN library is loaded already, and
    every later import of one raises Refused (the installed sim program does not carry them at all)."""
    found = loaded_forbidden()
    if found:
        raise SystemExit(f"a simulation process started with {', '.join(found)} loaded (tools/scene_lab/sim_boundary.py)")
    if not any(isinstance(finder, _Refuse) for finder in sys.meta_path):
        sys.meta_path.insert(0, _Refuse())
