# src/sm64_events/core/paths.py
"""THE single source of truth for where runtime state lives.

From source (dev/tests) every path is `Path(".")`-relative — byte-identical
to the historical layout (the project has always "run from the repo root").
Frozen into a PyInstaller exe (``sys.frozen``) everything moves under
``%LOCALAPPDATA%\\SM64Trainer`` so a double-clicked exe needs no working
directory and a new release can replace the exe while the user keeps their
history / PBs / saved replays. (Pre-1.0.2 the dir was ``sm64_tracker``;
``migrate_legacy_data_dir`` renames it on startup so data carries over.)

Every path the server or desktop shell persists to MUST come from here."""
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("sm64.paths")

# Filesystem identifier (exe name, data dir, locks) — no space, for CLI/path
# friendliness. APP_DISPLAY_NAME is the human-readable name shown in the window
# title, the tray, and dialogs.
APP_DIR_NAME = "SM64Trainer"
LEGACY_APP_DIR_NAME = "sm64_tracker"   # pre-1.0.2 data dir; migrated on startup
APP_DISPLAY_NAME = "SM64 Trainer"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def server_port() -> int:
    """TCP port the API/UI server binds. The packaged exe (frozen) uses the
    canonical 8064 (the port external consumers/overlays expect); running from
    source (dev) uses 8065, so a dev server and a built exe can never collide
    on one port (single-instance takeover, bind conflicts). SM64_PORT
    overrides either."""
    override = os.environ.get("SM64_PORT")
    if override:
        return int(override)
    return 8064 if is_frozen() else 8065


def data_root() -> Path:
    """Base directory for all persisted state.

    Source: ``Path(".")`` — joins collapse the leading dot, so
    ``data_root()/"data"/"tracker.db" == Path("data")/"tracker.db"`` exactly
    as before. Frozen: ``%LOCALAPPDATA%\\SM64Trainer``.
    """
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    return Path(".")


def migrate_legacy_data_dir() -> None:
    """One-time rename of the pre-1.0.2 data dir
    (``%LOCALAPPDATA%\\sm64_tracker``) to APP_DIR_NAME so existing PBs / replays
    / settings carry over after the SM64Trainer rename. Frozen only; idempotent;
    NEVER destroys data — if the new dir already holds data, the legacy dir is
    left untouched. Call before any data path is read/created."""
    if not is_frozen():
        return
    base = Path(os.environ.get("LOCALAPPDATA") or str(
        Path.home() / "AppData" / "Local"))
    new = base / APP_DIR_NAME
    legacy = base / LEGACY_APP_DIR_NAME
    if legacy == new or not legacy.exists():
        return
    try:
        if new.exists():
            if any(new.iterdir()):
                return  # new dir already has data -> already migrated / separate
            new.rmdir()  # empty (e.g. just mkdir'd) -> let the rename proceed
        os.rename(legacy, new)
        log.info("migrated data dir %s -> %s", legacy, new)
    except OSError:
        log.exception("data dir migration failed; using %s as-is", new)


def db_path() -> Path:
    return data_root() / "data" / "tracker.db"


def instance_lock_path() -> Path:
    # Matches the historical `DB_PATH.with_suffix(".lock")` -> data/tracker.lock
    return db_path().with_suffix(".lock")


def replay_scratch_dir() -> Path:
    return data_root() / "data" / "replay_buffer"


def compare_cache_dir() -> Path:
    # Normalized comparison videos (yt-dlp'd / copied + ffmpeg-normalized).
    # Content-addressed by source; survives restarts (unlike the replay ring).
    return data_root() / "data" / "compare_cache"


def replays_root() -> Path:
    return data_root() / "replays"


def user_icons_dir() -> Path:
    # User-uploaded selector icons (server/api.py /api/icons/upload). In the
    # DATA dir — never under the install's ui/assets, which is read-only when
    # frozen and replaced wholesale by updates.
    return data_root() / "data" / "icons"


def compilations_dir() -> Path:
    # Generated failure compilations. Lives UNDER replays_root() (== the
    # ReplayService save_root) on purpose: the existing /api/replay/reveal
    # path-check only opens files inside save_root, so this dir is revealable
    # with no new endpoint. Filenames start with "compilation_" so the saved-
    # clip glob (attempt_*.mp4) never mistakes one for a saved attempt clip.
    return replays_root() / "compilations"


def diagnostics_dir() -> Path:
    # One-button debug reports (task 0092). UNDER replays_root() (== the
    # ReplayService save_root) for the same reason compilations_dir() is:
    # /api/replay/reveal only opens files inside save_root, so a report is
    # revealable with no new endpoint.
    return replays_root() / "diagnostics"


def replay_settings_path() -> Path:
    return data_root() / "data" / "replay_settings.json"


def update_state_path() -> Path:
    # Skipped-update version lives here (a JSON overlay like replay_settings.json,
    # keeps the updater DB-free).
    return data_root() / "data" / "update_state.json"


def pidfile_path() -> Path:
    return data_root() / "server.pid"


def window_state_path() -> Path:
    return data_root() / "window.json"


def logs_dir() -> Path:
    return data_root() / "logs"


def rank_standards_path() -> Path:
    return data_root() / "data" / "rank_standards.json"


def bundled_rank_standards() -> Path | None:
    """The seed shipped beside a frozen exe (PyInstaller _MEIPASS), else the
    in-repo seed when running from source."""
    if is_frozen():
        cand = Path(getattr(sys, "_MEIPASS", "")) / "rank_standards.seed.json"
        return cand if cand.exists() else None
    cand = Path(__file__).resolve().parent.parent / "data" / "rank_standards.seed.json"
    return cand if cand.exists() else None


def bundled_defaults_seed() -> Path | None:
    """The editable-defaults seed (routes/segments corpus) shipped beside a
    frozen exe (PyInstaller _MEIPASS), else the in-repo seed when running
    from source. Mirrors bundled_rank_standards() exactly."""
    if is_frozen():
        cand = Path(getattr(sys, "_MEIPASS", "")) / "defaults.seed.json"
        return cand if cand.exists() else None
    cand = Path(__file__).resolve().parent.parent / "data" / "defaults.seed.json"
    return cand if cand.exists() else None


def sheet_library_path() -> Path:
    """The user's own refreshed library, beside their other data."""
    return data_root() / "data" / "sheet_library.json.gz"


def library_adoptions_path() -> Path:
    """Which library rows the USER assigned to segments they built. Theirs, not
    the community's -- library_overrides.json is the other thing."""
    return data_root() / "data" / "library_adoptions.json"


def bundled_sheet_library() -> Path | None:
    """The library snapshot shipped with the release. Mirrors
    bundled_rank_standards() exactly."""
    if is_frozen():
        cand = Path(getattr(sys, "_MEIPASS", "")) / "sheet_library.seed.json.gz"
        return cand if cand.exists() else None
    cand = (Path(__file__).resolve().parent.parent / "data"
            / "sheet_library.seed.json.gz")
    return cand if cand.exists() else None


def bundled_video_checks() -> Path | None:
    """The video-liveness verdicts (library/videocheck.py) shipped with the
    release — written by tools/check_videos.py, read by the standards API so
    a dead example URL never becomes THE example for a cutoff (task 0098
    round 2). Mirrors bundled_sheet_library() exactly."""
    if is_frozen():
        cand = Path(getattr(sys, "_MEIPASS", "")) / "video_checks.seed.json.gz"
        return cand if cand.exists() else None
    cand = (Path(__file__).resolve().parent.parent / "data"
            / "video_checks.seed.json.gz")
    return cand if cand.exists() else None


def bundled_library_overrides() -> Path | None:
    """The human's audit corrections to our READING of the sheet
    (tools/audit_library.py), shipped beside a frozen exe (PyInstaller
    _MEIPASS), else the in-repo file when running from source. Mirrors
    bundled_rank_standards() exactly. Without this, a server-side refresh
    (library/store.py::LibraryStore.refresh) rebuilds the library with no
    overrides at all -- and since a refreshed copy carries a newer
    sheet_revision, the un-corrected copy then wins over the bundled,
    corrected one until the next release."""
    if is_frozen():
        cand = Path(getattr(sys, "_MEIPASS", "")) / "library_overrides.json"
        return cand if cand.exists() else None
    cand = Path(__file__).resolve().parent.parent / "data" / "library_overrides.json"
    return cand if cand.exists() else None


def bundled_sheet_ladders() -> Path | None:
    """Ladders derived from the Ultimate Sheet. Its own file, never merged into
    rank_standards.seed.json, so a fitted ladder structurally cannot overwrite
    a vetted one. Mirrors bundled_rank_standards() exactly."""
    if is_frozen():
        cand = Path(getattr(sys, "_MEIPASS", "")) / "sheet_ladders.seed.json"
        return cand if cand.exists() else None
    cand = Path(__file__).resolve().parent.parent / "data" / "sheet_ladders.seed.json"
    return cand if cand.exists() else None


def install_root() -> Path:
    """Directory containing the running executable — the onedir install root
    when frozen (SM64Trainer.exe + _internal\\ live here). The updater and the
    startup update-repair operate relative to THIS, never a hardcoded install
    location, so a user who moves the folder keeps working updates."""
    return Path(sys.executable).resolve().parent


def bundled_ffmpeg() -> str | None:
    """Absolute path to the ffmpeg.exe bundled beside a frozen exe, else None.
    PyInstaller unpacks --add-binary files into ``sys._MEIPASS``."""
    if is_frozen():
        cand = Path(getattr(sys, "_MEIPASS", "")) / "ffmpeg.exe"
        if cand.exists():
            return str(cand)
    return None
