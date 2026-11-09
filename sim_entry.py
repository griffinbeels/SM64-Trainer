# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""PyInstaller entry point for SM64TrainerSim.exe, the installed app's second
program: the Ghost Lab's child processes that run Unicorn.

Unicorn is GPL-2.0-only, so it never shares a process with PyAV's FFmpeg
(GPL-3.0) or pystray (LGPL-3.0); this program carries Unicorn, numpy and the
lab modules that use them, and nothing (L)GPL-3.0 (tools/build_exe.py builds
it, tools/bundle_policy.py checks what it holds). It runs only the children
sm64_events/scenelab_code.py assigns to it, and nothing else: no window, no
server, no user data of its own."""
import sys


def main():
    from sm64_events.scenelab_code import SIM_PROGRAM, child_program, run_child
    if len(sys.argv) > 1 and sys.argv[1] == "--sim-self-check":    # sm64_events/scenelab/probe.py SIM_FLAG
        from sm64_events.scenelab.probe import sim_self_check
        return sim_self_check(sys.argv[2:])
    if len(sys.argv) > 1 and child_program(sys.argv[1]) == SIM_PROGRAM:
        return run_child(sys.argv[1], sys.argv[2:])
    print("SM64TrainerSim.exe runs the Ghost Lab's simulation for SM64 Trainer; "
          "SM64 Trainer starts it by itself.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
