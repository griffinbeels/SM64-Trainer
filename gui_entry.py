# gui_entry.py
"""PyInstaller entry point for the packaged desktop app.
(Dev runs `python -m sm64_events.desktop`; both call the same main().)"""
import sys


def main():
    # Workers must bypass desktop imports, migrations and instance takeover.
    from sm64_events.library.background import WORKER_FLAG
    if len(sys.argv) > 1 and sys.argv[1] == WORKER_FLAG:
        from sm64_events.library.background import worker_main
        return worker_main(sys.argv[2:])
    # The recorder's encoder helper re-enters this exe the same way the
    # overlay below does. Without this dispatch the installed app could not
    # record at all (v1.9.1).
    from sm64_events.replay.gpuprocess.process_worker import GPU_HELPER_FLAG
    if len(sys.argv) > 1 and sys.argv[1] == GPU_HELPER_FLAG:
        from sm64_events.replay.gpuprocess.process_worker import main as gpu_helper_main
        return gpu_helper_main(sys.argv[2:])
    # The ghost overlay's window is a child of THIS exe: a PyInstaller build
    # has no `-m`, so sys.executable is the trainer and the flag is how it
    # re-enters as the overlay instead of as a second copy of the app. The
    # flag is imported rather than spelled out so the two cannot drift apart.
    from sm64_events.ghost.window import GHOST_WINDOW_FLAG
    if len(sys.argv) > 1 and sys.argv[1] == GHOST_WINDOW_FLAG:
        from sm64_events.ghost.window.child import main as ghost_window_main
        return ghost_window_main(sys.argv[2:])
    from sm64_events.desktop.app import main as desktop_main
    return desktop_main()

if __name__ == "__main__":
    raise SystemExit(main())
