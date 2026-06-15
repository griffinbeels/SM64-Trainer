# src/sm64_events/core/paths.py
"""THE single source of truth for where runtime state lives.

From source (dev/tests) every path is `Path(".")`-relative — byte-identical
to the historical layout (the project has always "run from the repo root").
Frozen into a PyInstaller exe (``sys.frozen``) everything moves under
``%LOCALAPPDATA%\\sm64_tracker`` so a double-clicked exe needs no working
directory and a new release can replace the exe while the user keeps their
history / PBs / saved replays.

Every path the server or desktop shell persists to MUST come from here."""
import os
import sys
from pathlib import Path

APP_DIR_NAME = "sm64_tracker"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_root() -> Path:
    """Base directory for all persisted state.

    Source: ``Path(".")`` — joins collapse the leading dot, so
    ``data_root()/"data"/"tracker.db" == Path("data")/"tracker.db"`` exactly
    as before. Frozen: ``%LOCALAPPDATA%\\sm64_tracker``.
    """
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    return Path(".")


def db_path() -> Path:
    return data_root() / "data" / "tracker.db"


def instance_lock_path() -> Path:
    # Matches the historical `DB_PATH.with_suffix(".lock")` -> data/tracker.lock
    return db_path().with_suffix(".lock")


def replay_scratch_dir() -> Path:
    return data_root() / "data" / "replay_buffer"


def replays_root() -> Path:
    return data_root() / "replays"


def replay_settings_path() -> Path:
    return data_root() / "data" / "replay_settings.json"


def pidfile_path() -> Path:
    return data_root() / "server.pid"


def window_state_path() -> Path:
    return data_root() / "window.json"


def logs_dir() -> Path:
    return data_root() / "logs"


def bundled_ffmpeg() -> str | None:
    """Absolute path to the ffmpeg.exe bundled beside a frozen exe, else None.
    PyInstaller unpacks --add-binary files into ``sys._MEIPASS``."""
    if is_frozen():
        cand = Path(getattr(sys, "_MEIPASS", "")) / "ffmpeg.exe"
        if cand.exists():
            return str(cand)
    return None
