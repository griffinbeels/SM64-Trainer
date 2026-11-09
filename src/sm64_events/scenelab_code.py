# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""THE door to the Ghost Lab's own code, the modules in tools/scene_lab, and
the one table of which program runs each lab child process.

From a source checkout those modules are plain scripts beside each other and
import each other by bare name (`import package`, `import replay`), so the
folder goes on `sys.path` and a module is imported by that name. The installed
app has no tools folder: tools/build_exe.py collects every module named in
LAB_MODULES (below) into the app under the same bare name, and here they are
imported exactly as they are. LAB_MODULES is the one list of what the installed
app carries (the build reads it through tools/bundle_policy.py, and the frozen
app's own self-check imports each one, desktop/lab_probe.py); tests/test_scenelab_code.py proves it equals the
import closure of what the app reaches, and that no bare name shadows a
standard library, installed or app module.

Two programs. Unicorn, the lab's MIPS emulator, is GPL-2.0-only, so it never
shares a process with PyAV's FFmpeg (a GPL-3.0 build) or pystray (LGPL-3.0)
(the license ruling, 2026-11-09). The installed app therefore holds a second,
small program, SIM_PROGRAM, beside the Trainer: it carries Unicorn and the
modules that use it, and nothing (L)GPL-3.0. LAB_CHILDREN says which program
runs each child process; core/childproc.py starts that program when installed,
and each program's entry (gui_entry.py, sim_entry.py) runs only its own flags.
"""
from __future__ import annotations

import importlib
import runpy
import sys
from pathlib import Path
from types import ModuleType

#: THE list of Ghost Lab modules (tools/scene_lab) the installed app carries,
#: by the bare names they import each other by. It is the import closure of
#: every lab module the app reaches; tests/test_scenelab_code.py recomputes that
#: closure and fails on a module missing here, one listed but unreached, or a
#: name that shadows another module. Left out on purpose: audio_bench,
#: audio_sync_check, e1, e3_measure, fixture, handoff_relocate and slot_library,
#: developer and evidence tools nothing reaches.
LAB_MODULES = (
    "attempt_cutter", "attempt_scenes", "attempt_span", "audio_capture", "audio_clock", "audio_layer",
    "audio_session", "camera_paths", "camera_presets", "camera_rig", "camera_state", "closure",
    "cold_boot", "cpu_hooks", "e2_georender", "evaluator", "evaluator_child", "export_sound", "fake_viewer_host",
    "frame_sim", "ghost_resim", "ghost_sound", "gpu_gate", "handoff_slot", "handoff_try", "hud",
    "live_audio", "live_spool", "moment_slot", "package", "pick", "picture_encoder", "provenance",
    "recamera", "replay", "rig_path", "savestate_frames", "scene_audio", "scene_export",
    "scene_session", "sim_boundary", "sim_scene", "slot_atlas", "stage_cache", "stage_ghosts", "stage_sim",
    "start_check", "state_resolver", "stems_check", "stems_layer", "stems_live", "stems_probe", "tap", "viewer",
    "webview_pictures",
)
#: tools/scene_lab in this checkout; absent from the installed app
LAB_SOURCE = Path(__file__).resolve().parents[2] / "tools" / "scene_lab"
#: the two programs of the installed app, by file name in its folder
TRAINER_PROGRAM = "SM64Trainer.exe"
SIM_PROGRAM = "SM64TrainerSim.exe"
#: THE table of the lab's child processes: the flag a child is started with
#: (core/childproc.py::python_child), the lab module it runs as its main
#: program, and the installed program that runs it. A child that runs Unicorn
#: runs in SIM_PROGRAM; the rest re-enter the Trainer. The SIM_PROGRAM rows are
#: the simulation processes tools/scene_lab/sim_boundary.py guards (its ENTRIES
#: are read from here). The stems child splits music from sound effects on the
#: cxd4 RSP DLL and encodes FLAC with PyAV, and runs no Unicorn: the Trainer's.
LAB_CHILDREN = {
    "--scene-lab-cutter": ("attempt_cutter", TRAINER_PROGRAM),
    "--scene-lab-rig-path": ("rig_path", TRAINER_PROGRAM),
    "--scene-lab-encoder": ("picture_encoder", TRAINER_PROGRAM),
    "--scene-lab-ghost-sound": ("ghost_sound", TRAINER_PROGRAM),
    "--scene-lab-export": ("scene_export", TRAINER_PROGRAM),
    "--scene-lab-stems": ("stems_live", TRAINER_PROGRAM),
    "--scene-lab-evaluator": ("evaluator_child", SIM_PROGRAM),   # the free camera (scene_session's evaluator)
    "--scene-lab-resolver": ("state_resolver", SIM_PROGRAM),      # the free camera's state resolvers
    "--scene-lab-resim": ("ghost_resim", SIM_PROGRAM),            # re-simulated ghosts, stages and their warm-up
    "--scene-lab-moment": ("moment_slot", SIM_PROGRAM),           # a save state built from a scene frame
    "--scene-lab-atlas": ("slot_atlas", SIM_PROGRAM),             # the US/JP slot translation atlas
}


def child_program(flag: str) -> str | None:
    """The installed program that runs the lab child `flag`, or None for a
    flag that is not a lab child's."""
    entry = LAB_CHILDREN.get(flag)
    return entry[1] if entry else None


def child_modules(program: str) -> tuple[str, ...]:
    """The lab modules `program` runs as children, in table order."""
    return tuple(module for module, runs_in in LAB_CHILDREN.values() if runs_in == program)


def run_child(flag: str, args: list[str]) -> int:
    """Run the lab child `flag` in this process, as `python -m <module> <args>`
    would: the module's own main block, with those arguments. Its SystemExit
    ends the process with its code; a module that simply returns exits 0."""
    module = LAB_CHILDREN[flag][0]
    _on_path()
    sys.argv = [module, *args]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return 0


def _on_path() -> None:
    """From a source checkout, put tools/scene_lab first on `sys.path` once."""
    if getattr(sys, "frozen", False):
        return
    folder = str(LAB_SOURCE)
    if folder not in sys.path:
        sys.path.insert(0, folder)


def lab_module(name: str) -> ModuleType:
    """The Ghost Lab module `name` (a bare name such as "scene_session"):
    imported from tools/scene_lab in a source checkout, from the app itself
    when installed."""
    _on_path()
    return importlib.import_module(name)
