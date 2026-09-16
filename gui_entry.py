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
    from sm64_events.desktop.app import main as desktop_main
    return desktop_main()

if __name__ == "__main__":
    raise SystemExit(main())
