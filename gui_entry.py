# gui_entry.py
"""PyInstaller entry point for the packaged desktop app.
(Dev runs `python -m sm64_events.desktop`; both call the same main().)"""
from sm64_events.desktop.app import main

if __name__ == "__main__":
    main()
