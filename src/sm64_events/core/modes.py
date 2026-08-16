"""EMU/N64 tracker mode + game version — the persisted mode overlay.

The mode config decides which front-end main.py builds: EMU polls Project64
memory (today's wiring), N64 reads the OBS Virtual Camera feed through the
vision front-end. The version picks which glyph/region sets the vision
detectors read; AUTO means the select screen names it.

Persisted as a tiny JSON overlay at mode_settings_path(), with the same
resilience contract as replay/config.py::apply_settings_file: an absent,
corrupt, or unknown-valued file loses to per-field defaults with one warning
line — the server must always start, so load never raises. Unknown values
default PER FIELD: a bad mode string must not eat the version stored beside
it.

The FULL (mode x version) matrix stores and loads — JP+EMU included. Whether
a combination is SUPPORTED is the wiring's concern, never this module's:
refusing to represent a stored value would make the setting unreadable the
moment support changes."""
import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from sm64_events.core.paths import mode_settings_path

log = logging.getLogger("sm64.modes")


class TrackerMode(Enum):
    EMU = "emu"
    N64 = "n64"


class GameVersion(Enum):
    AUTO = "auto"
    JP = "jp"
    US = "us"


@dataclass(frozen=True)
class ModeConfig:
    mode: TrackerMode = TrackerMode.EMU
    version: GameVersion = GameVersion.AUTO


def _pick(enum_cls, raw, default, field_name: str, problems: list[str]):
    """Enum member for `raw`, else `default`. An absent key (None) keeps the
    default silently — that is the normal partial-file case; a present-but-
    unknown value records a problem for the caller's single warning line.
    Case/whitespace-tolerant so a hand-edited "N64" does not silently fall
    back to EMU."""
    if raw is None:
        return default
    try:
        return enum_cls(str(raw).strip().lower())
    except ValueError:
        problems.append(f"unknown {field_name} {raw!r}")
        return default


def load_mode_config(path: Path | None = None) -> ModeConfig:
    """The persisted ModeConfig, or defaults wherever the file falls short.
    Never raises (resilience contract in the module docstring)."""
    if path is None:
        path = mode_settings_path()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return ModeConfig()               # first run — nothing to warn about
    except Exception:
        log.warning("ignoring unreadable %s; mode defaults win", path)
        return ModeConfig()
    if not isinstance(raw, dict):
        log.warning("ignoring non-object %s; mode defaults win", path)
        return ModeConfig()
    problems: list[str] = []
    mode = _pick(TrackerMode, raw.get("mode"), ModeConfig.mode, "mode", problems)
    version = _pick(GameVersion, raw.get("version"), ModeConfig.version,
                    "version", problems)
    if problems:
        log.warning("%s: %s; defaults win for those fields",
                    path, "; ".join(problems))
    return ModeConfig(mode=mode, version=version)


def save_mode_config(cfg: ModeConfig, path: Path | None = None) -> None:
    if path is None:
        path = mode_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"mode": cfg.mode.value, "version": cfg.version.value}, indent=2))


def effective_version(cfg: ModeConfig, detected: str | None = None) -> str:
    """The version grading and the visual switches DEFAULT to: an explicit
    JP/US setting wins outright; AUTO takes `detected` (what a live front-end
    read off the screen -- console-support's N64 mode; nothing on the emulator
    path detects yet) when it is a known version, else US, the only ROM the
    emulator path supports. Always "jp" or "us", never "auto"."""
    if cfg.version is not GameVersion.AUTO:
        return cfg.version.value
    if detected in ("jp", "us"):
        return detected
    return "us"
