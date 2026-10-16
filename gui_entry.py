# gui_entry.py
"""PyInstaller entry point for the packaged desktop app.
(Dev runs `python -m sm64_events.desktop`; both call the same main().)"""
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--beta-self-check':
        from sm64_events.desktop.beta_probe import main as beta_self_check
        return beta_self_check(sys.argv[2:])
    # Resolve/reject packaged identity before any worker or desktop imports can
    # resolve paths or ports. No environment variable can select the channel.
    from sm64_events.core.build_channel import current_build
    current_build()
    # Workers must bypass desktop imports, migrations and instance takeover.
    from sm64_events.library.background import WORKER_FLAG
    if len(sys.argv) > 1 and sys.argv[1] == WORKER_FLAG:
        from sm64_events.library.background import worker_main
        return worker_main(sys.argv[2:])
    # The recorder's encoder helper re-enters this exe the same way the
    # ghost sound player below does. Without this dispatch the installed app could not
    # record at all (v1.9.1).
    from sm64_events.replay.gpuprocess.process_worker import GPU_HELPER_FLAG
    if len(sys.argv) > 1 and sys.argv[1] == GPU_HELPER_FLAG:
        from sm64_events.replay.gpuprocess.process_worker import main as gpu_helper_main
        return gpu_helper_main(sys.argv[2:])
    # The ghost sound player re-enters the same way: it plays ghost sound
    # from outside Project64 so the recorder's tap of the game never hears it.
    from sm64_events.ghost.sound_player import SOUND_PLAYER_FLAG
    if len(sys.argv) > 1 and sys.argv[1] == SOUND_PLAYER_FLAG:
        from sm64_events.ghost.sound_player import main as sound_player_main
        return sound_player_main(sys.argv[2:])
    from sm64_events.desktop.app import main as desktop_main
    return desktop_main()

if __name__ == "__main__":
    raise SystemExit(main())
