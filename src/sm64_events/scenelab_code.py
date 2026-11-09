# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the Ghost Lab's simulation program beside Unicorn (GPL v2 only): licenses/README.md.
"""THE door to the Ghost Lab's own code, the modules in tools/scene_lab, and
the one table of which program runs each lab child process.

From a source checkout those modules are plain scripts beside each other and
import each other by bare name (`import package`, `import replay`), so the
folder goes on `sys.path` and a module is imported by that name. The installed
app has no tools folder: tools/build_exe.py collects every module named in
tools/bundle_policy.py's LAB_MODULES into the app under the same bare name, and
here they are imported exactly as they are. LAB_MODULES is the one list of what
the installed app carries; tests/test_scenelab_code.py proves it equals the
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

#: tools/scene_lab in this checkout; absent from the installed app
LAB_SOURCE = Path(__file__).resolve().parents[2] / "tools" / "scene_lab"
#: the two programs of the installed app, by file name in its folder
TRAINER_PROGRAM = "SM64Trainer.exe"
SIM_PROGRAM = "SM64TrainerSim.exe"
#: THE table of the lab's child processes: the flag a child is started with
#: (core/childproc.py::python_child), the lab module it runs as its main
#: program, and the installed program that runs it. A child that runs Unicorn
#: runs in SIM_PROGRAM; the rest re-enter the Trainer.
LAB_CHILDREN = {
    "--scene-lab-cutter": ("attempt_cutter", TRAINER_PROGRAM),
    "--scene-lab-rig-path": ("rig_path", TRAINER_PROGRAM),
    "--scene-lab-encoder": ("picture_encoder", TRAINER_PROGRAM),
    "--scene-lab-ghost-sound": ("ghost_sound", TRAINER_PROGRAM),
    "--scene-lab-export": ("scene_export", TRAINER_PROGRAM),
    "--scene-lab-resolver": ("state_resolver", SIM_PROGRAM),
    "--scene-lab-resim": ("ghost_resim", SIM_PROGRAM),
    "--scene-lab-moment": ("moment_slot", SIM_PROGRAM),
    "--scene-lab-atlas": ("slot_atlas", SIM_PROGRAM),
    "--scene-lab-stems": ("stems_live", SIM_PROGRAM),
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
