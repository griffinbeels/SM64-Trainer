"""The setup check: the user's Project64 setup, compared with the tested setup.

The tested setup is Griffin's working machine, recorded with its source and
date in `data/setup_baseline.json`. That file is also the one table of each
item's importance, fix sentence and guide link, so an importance can change
when evidence arrives without touching this code.

  required     known to stop the trainer working (our graphics plugin, frame
               buffer emulation off, 8 MB memory, Counter Factor 1)
  recommended  differs from what we test with; may or may not matter
  noted        recorded only, never a problem (the controller plugin; his
               rule, 2026-10-30: "it's not necessarily a problem to use
               something else. Just is something we should keep track of.")

Where Project64 1.6 keeps each value (read from his machine, 2026-10-30):
plugin choice and global defaults are per Windows user in HKCU
``Software\\N64 Emulation\\Project64 Version 1.6`` (``Default RDRAM Size`` in
bytes, ``CPU Type``) and its ``Dll`` subkey (``Graphics Dll``, ``Audio Dll``,
``Controller Dll``). Per-ROM settings are sections of the emulator folder's
``Project64.rdb`` named by the cartridge header (``[CRC1-CRC2-C:XX]``,
`onboarding.rdb_key`); user-added ROMs are appended after ``//[EOF]``. Under
Program Files the copy Project64 reads may be the VirtualStore one: an
existing copy is read first, none is ever created (`capturelayer` policy).

Read only, never a process launch or a write. Hashes and the rdb parse are
cached by file stat, so a status call costs registry reads and stats.
A value no readable evidence establishes is "unknown", never a guess: the
Counter Factor default and the meaning of the registry's ``CPU Type`` number
are not readable, so a ROM with no rdb entry reports them as unknown.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from pathlib import Path

from sm64_events.core import renderer_settings
from sm64_events.core.capturelayer import (
    REGISTRY_DLL_SUBKEY, REGISTRY_KEY, virtualstore_copy,
)
from sm64_events.core.identity_privacy import contains_identity
from sm64_events.core.plugin_installation import WRAPPER_DLL, WRAPPER_INI
from sm64_events.core.renderer_settings import RENDERER_INI

BASELINE_PATH = Path(__file__).resolve().parents[1] / "data" / "setup_baseline.json"
IMPORTANCE = ("required", "recommended", "noted")
STATUSES = ("differs", "unknown", "match")
#: the importances a difference is worth telling the user about
FLAGGED = frozenset({"required", "recommended"})
RDB = "Project64.rdb"
NO_ROM = "No Usamune ROM is open"
DEFAULT = "Project64's default"
NOT_READABLE = f"{DEFAULT} (we can't read it)"
#: Items whose value is chosen per Windows user (HKCU), so every Project64
#: copy on this account shares it; the rest come from the running folder.
ACCOUNT_CHOICES = frozenset({"graphics_plugin", "audio_plugin", "controller_plugin"})
#: A folder's own name may be shown; anything else about its path never is.
_FOLDER_NAME = re.compile(r"[A-Za-z0-9 ._()+',&#-]{1,48}")


def folder_name(name) -> str:
    """A Project64 folder's own name ("01-stock-vanilla") as reports may show
    it, or "unnamed" for a name that is long, uses other characters, or holds
    this machine's account or computer name: the user chose it."""
    plain = isinstance(name, str) and _FOLDER_NAME.fullmatch(name) and name.strip()
    return name if plain and not contains_identity(name) else "unnamed"


def load_baseline(path: Path = BASELINE_PATH) -> dict:
    baseline = json.loads(path.read_text(encoding="utf-8"))
    for item in baseline["items"]:
        if item["importance"] not in IMPORTANCE:
            raise ValueError(f"unknown importance for {item['id']}: {item['importance']}")
    return baseline


def _program_dirs() -> tuple[str, ...]:
    names = ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
    return tuple(dict.fromkeys(value for name in names if (value := os.environ.get(name))))


def _default_version_reader():
    if sys.platform != "win32":
        return lambda path: None
    from sm64_events.core.capturelayer_win import executable_version
    return executable_version


def _known_builds() -> dict:
    try:
        from sm64_events.core.capturelayer_win import _UNVERSIONED_16_BUILDS
    except (ImportError, OSError, ValueError):
        return {}
    return _UNVERSIONED_16_BUILDS


def _inside(path: Path, folder: str) -> bool:
    try:
        a, b = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
        return os.path.commonpath([a, b]) == b
    except ValueError:  # different drives
        return False


def parse_rdb(text: str) -> dict[str, dict[str, str]]:
    """Every section's settings, first occurrence of a section winning."""
    sections: dict[str, dict[str, str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("["):
            end = line.find("]")
            name = line[1:end] if end > 0 else None
            current = None if name is None or name in sections else sections.setdefault(name, {})
            continue
        if current is not None and "=" in line:
            key, value = line.split("=", 1)
            current.setdefault(key.strip(), value.split("//", 1)[0].strip())
    return sections


class SetupProfile:
    """Collect and compare; `compare` is the one door every reader uses."""

    def __init__(self, registry, *, local_appdata: str | None = None,
                 program_dirs: tuple[str, ...] | None = None, version_reader=None,
                 known_builds: dict | None = None, baseline: dict | None = None):
        self._registry = registry
        self._local_appdata = local_appdata
        self._program_dirs = _program_dirs() if program_dirs is None else program_dirs
        self._version = version_reader or _default_version_reader()
        self._builds = _known_builds() if known_builds is None else known_builds
        self.baseline = baseline or load_baseline()
        self._rows = {item["id"]: item for item in self.baseline["items"]}
        self._lock = threading.Lock()
        self._hashes: dict[str, tuple] = {}
        self._rdb: tuple | None = None
        self._last: dict | None = None
        self._inputs: tuple = ({}, None)
        self._from_registry: set = set()

    # -- cached file reads ---------------------------------------------------

    def _sha256(self, path: Path) -> str | None:
        """None for a missing file; OSError for an unreadable one."""
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        key = (stat.st_size, stat.st_mtime_ns)
        cached = self._hashes.get(str(path))
        if cached and cached[0] == key:
            return cached[1]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self._hashes[str(path)] = (key, digest)
        return digest

    def _rdb_sections(self, path: Path) -> dict:
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if self._rdb is None or self._rdb[0] != key:
            self._rdb = (key, parse_rdb(path.read_text(encoding="latin-1")))
        return self._rdb[1]

    def _registry_get(self, subkey, name):
        try:
            return self._registry.get(subkey, name)
        except OSError:
            return None

    # -- the comparison ------------------------------------------------------

    def compare(self, layer: dict | None, rom: dict | None) -> dict:
        """The setup check for this capture-layer status and loaded ROM."""
        layer, rom = layer or {}, rom or {}
        with self._lock:
            self._inputs = (layer, rom)
            self._from_registry = set()
            pj64 = Path(layer["pj64_dir"]) if layer.get("pj64_dir") else None
            plugin = Path(layer["plugin_dir"]) if layer.get("plugin_dir") else (pj64 / "Plugin" if pj64 else None)
            rdb_path, rdb_source = self._effective_rdb(pj64)
            readings = {
                "graphics_plugin": self._graphics(),
                "frame_buffer_emulation": self._frame_buffer(plugin),
                **self._per_rom(rom, rdb_path),
                "emulator_build": self._emulator(pj64),
                "rsp_plugin": self._plugin_file(plugin, "RSP.dll", "rsp_plugin", "No RSP.dll"),
                "audio_plugin": self._selected(plugin, "Audio Dll", "audio_plugin"),
                "install_location": self._location(pj64, rdb_source),
                "controller_plugin": self._selected(plugin, "Controller Dll", "controller_plugin"),
            }
            items = [self._item(row, *readings[row["id"]]) for row in self.baseline["items"]]
            for item in items:
                # A per-ROM value that fell back to the registry default is
                # shared too: every Project64 on this account reads it.
                item["shared"] = item["id"] in ACCOUNT_CHOICES | self._from_registry
            order = {status: at for at, status in enumerate(STATUSES)}
            weight = {name: at for at, name in enumerate(IMPORTANCE)}
            items.sort(key=lambda item: (item["importance"] == "noted", order[item["status"]],
                                         weight[item["importance"]]))
            flagged = [item["id"] for item in items
                       if item["status"] == "differs" and item["importance"] in FLAGGED]
            result = {"version": self.baseline["version"],
                      "baseline": {"recorded": self.baseline["recorded"], "source": self.baseline["source"]},
                      "rom": rom.get("rdb_key") if rom.get("state") in ("supported", "jp") else None,
                      "rdb": rdb_source,
                      "folder": self._folder(pj64, plugin, layer),
                      "items": items, "flagged": flagged,
                      "required_differs": sum(item["status"] == "differs" and item["importance"] == "required"
                                              for item in items),
                      "unknown": sum(item["status"] == "unknown" for item in items)}
            self._last = result
            return result

    def diagnostic_status(self, layer: dict | None = None, rom: dict | None = None) -> dict | None:
        """For reports: recompute from the given (or last) inputs with cached
        reads, never discovering a process; None before anything was seen."""
        if layer is None and rom is None:
            layer, rom = self._inputs
            if not layer and not rom and self._last is None:
                return None
        try:
            return self.compare(layer, rom)
        except Exception as error:  # noqa: BLE001 - a report must still build
            return {"error_type": type(error).__name__, "items": []}

    def _item(self, row, status, yours, sha=None):
        item = {"id": row["id"], "label": row["label"], "importance": row["importance"],
                "status": status, "yours": yours, "tested": row["tested"], "link": row["link"],
                "fix": row["fix"] if status == "differs" else row["unknown"] if status == "unknown" else None}
        if sha:
            item["yours_sha256"] = sha
        if row.get("download"):
            item["download"] = row["download"]
        return item

    def _hashed(self, row_id, path, name, missing):
        """(status, yours, sha) for a file compared with the tested file's hash."""
        try:
            sha = self._sha256(path)
        except OSError:
            return "unknown", f"{name} (we can't read it)", None
        if sha is None:
            return "differs", missing, None
        if sha == self._rows[row_id].get("sha256"):
            return "match", self._rows[row_id]["tested"], sha
        return "differs", f"{name} (sha256 {sha[:12]})", sha

    def _graphics(self):
        name = self._registry_get(REGISTRY_DLL_SUBKEY, "Graphics Dll")
        if name is None:
            return "differs", "None selected"
        name = str(name)
        return ("match", self._rows["graphics_plugin"]["tested"]) if name.lower() == WRAPPER_DLL.lower() \
            else ("differs", name)

    def _frame_buffer(self, plugin):
        if plugin is None:
            return "unknown", "Project64 folder not found"
        files = [plugin / RENDERER_INI]
        shadow = virtualstore_copy(self._local_appdata, plugin, RENDERER_INI)
        if shadow is not None:
            files.append(shadow)
        try:
            datas = [renderer_settings.read(path) for path in files]
        except OSError:
            return "unknown", "GLideN64.ini can't be read"
        if all(renderer_settings.frame_buffer_emulation_off(data) for data in datas):
            return "match", "Off"
        if datas[0] is None:
            return "differs", "On (there is no GLideN64.ini, so GLideN64 turns it on)"
        return "differs", "On"

    def _effective_rdb(self, pj64):
        if pj64 is None:
            return None, None
        shadow = virtualstore_copy(self._local_appdata, pj64, RDB)
        if shadow is not None:
            return shadow, "VirtualStore"
        return (pj64 / RDB, "Project64 folder") if (pj64 / RDB).is_file() else (None, "missing")

    def _per_rom(self, rom, rdb_path):
        ids = ("memory_size", "counter_factor", "cpu_core")
        if rom.get("state") not in ("supported", "jp") or not rom.get("rdb_key"):
            return {key: ("unknown", NO_ROM) for key in ids}
        if rdb_path is None:
            section = {}
        else:
            try:
                section = self._rdb_sections(rdb_path).get(rom["rdb_key"], {})
            except OSError:
                return {key: ("unknown", "Project64.rdb can't be read") for key in ids}
        return {"memory_size": self._memory(section.get("RDRAM Size")),
                "counter_factor": self._counter(section.get("Counter Factor")),
                "cpu_core": self._cpu(section.get("CPU Type"))}

    @staticmethod
    def _megabytes(value) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number in (4, 8) else number // (1024 * 1024) if number >= 1024 * 1024 else None

    def _memory(self, value):
        size = self._megabytes(value) if value not in (None, "Default") else None
        source = ""
        if size is None and value in (None, "Default"):
            size = self._megabytes(self._registry_get(REGISTRY_KEY, "Default RDRAM Size"))
            source = f" ({DEFAULT})"
            if size is not None:
                self._from_registry.add("memory_size")
        if size is None:
            return "unknown", NOT_READABLE
        return ("match" if size == 8 else "differs"), f"{size} MB{source}"

    @staticmethod
    def _counter(value):
        if value in (None, "Default"):
            return "unknown", NOT_READABLE
        if not str(value).isdigit():
            return "unknown", f"{value} (not a number)"
        return ("match" if int(value) == 1 else "differs"), str(int(value))

    def _cpu(self, value):
        if value in ("Interpreter", "Recompiler", "SyncCores"):
            return ("match" if value == "Interpreter" else "differs"), value
        raw = self._registry_get(REGISTRY_KEY, "CPU Type")
        if raw is not None:
            self._from_registry.add("cpu_core")
        # The registry number's meaning is not established from evidence here.
        return "unknown", f"Project64's default (setting {raw})" if raw is not None else NOT_READABLE

    def _emulator(self, pj64):
        if pj64 is None:
            return "unknown", "Project64 folder not found"
        exe = pj64 / "Project64.exe"
        status, yours, sha = self._hashed("emulator_build", exe, "Project64.exe", "No Project64.exe")
        if status == "differs" and sha:
            try:
                version = self._version(exe)
            except OSError:
                version = None
            name = self._builds.get(sha) or (f"Project64 {version}" if version else "An unrecognized build")
            yours = f"{name} (sha256 {sha[:12]})"
        return status, yours, sha

    def _plugin_file(self, plugin, name, row_id, missing):
        if plugin is None:
            return "unknown", "Project64 folder not found"
        return self._hashed(row_id, plugin / name, name, missing)

    def _selected(self, plugin, value, row_id):
        name = self._registry_get(REGISTRY_DLL_SUBKEY, value)
        if name is None:
            return "unknown", "None recorded"
        name = Path(str(name)).name
        return self._plugin_file(plugin, name, row_id, f"{name} (the file is missing)")

    def _folder(self, pj64, plugin, layer):
        """Which Project64 copy this check describes: its own folder name,
        where it sits, and whether it was running when the setup was read
        (the capture layer follows the running Project64, else the last one)."""
        if pj64 is None:
            return {"name": "unnamed", "location": "unknown", "running": False}
        location = "other"
        if any(_inside(pj64, folder) for folder in self._program_dirs):
            shadows = [virtualstore_copy(self._local_appdata, pj64, RDB)]
            if plugin is not None:
                shadows += [virtualstore_copy(self._local_appdata, plugin, name)
                            for name in (RENDERER_INI, WRAPPER_INI)]
            location = "VirtualStore" if any(shadows) else "Program Files"
        return {"name": folder_name(pj64.name), "location": location, "running": layer.get("pj64_running") is True}

    def _location(self, pj64, rdb_source):
        if pj64 is None:
            return "unknown", "Project64 folder not found"
        if any(_inside(pj64, folder) for folder in self._program_dirs):
            return "differs", "Program Files" + (" (settings kept in VirtualStore)" if rdb_source == "VirtualStore" else "")
        return "match", "Outside Program Files"


def setup_profile_for(capture_layer) -> SetupProfile | None:
    """The setup check over the capture layer's own registry, or None for a
    layer without one (test fakes)."""
    registry = getattr(capture_layer, "registry", None)
    if registry is None:
        return None
    return SetupProfile(registry, local_appdata=getattr(capture_layer, "local_appdata", None))
