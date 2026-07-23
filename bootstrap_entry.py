# bootstrap_entry.py
"""PyInstaller entry point for the bootstrap installer (built onefile as
SM64TrainerSetup.exe, published as the SM64Trainer.exe release asset)."""
import sys

from sm64_events.bootstrap.installer import main

if __name__ == "__main__":
    sys.exit(main())
