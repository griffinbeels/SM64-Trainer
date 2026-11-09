"""The Ghost Lab's ROM store: each region's Usamune ROM, found once and kept as a private copy.

Interface I3 of the Ghost Lab beta: `rom_for(region) -> Path | None` and `missing_reason(region) -> str | None`,
region "us" or "jp". Other code asks only these two (and `default_store()` for the route and the copy step).

The Ghost Lab runs the game's own code, so it needs the exact cartridge: Super Mario 64 Usamune v1.93u (US) or
v1.93j (JP), 8 MiB each. A ROM is known by its header (the internal name, exactly, and the country letter) and
its size, never by its file name: on his machine a file named `.n64` holds a `.z64` image. Three ways find it:

A. The game running in Project64 (`copy_from_game`). It costs the player nothing and is exact: it is the image
   he plays. A copy from the game replaces a stored copy of the same region whose bytes differ.
B. Project64's ROM folders (`scan_folders`): the files Project64 last opened and its ROM folder, from its own
   registry settings, and `roms` beside Project64.exe, from setup's record. `rom_for` tries this on a miss.
C. A file the player chooses (`adopt`), when neither found it.

The copy lives at `<lab root>/roms/<region>.z64` in the N64's big-endian order, with a record beside it. Nothing
here writes to Project64, its folders or its registry, and nothing here sends a byte off this computer.

Measured 2026-11-09 on his machine, read-only (Project64 1.6, pid 61300): Project64 holds the image word-swapped,
each 32-bit word reversed, the same order as RDRAM (`memory/base.py`). Swapped back, the 8,388,608 bytes are
identical to `roms/Usamune US.z64` (sha256 7be459e5...). The image's region ends at exactly 8 MiB; one read took
2.4 ms. `KNOWN_SHA256` records his two files; a different image with the right header is still accepted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from array import array
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sm64_events.core import paths
from sm64_events.memory.version_probe import HEADER_SIZE, NAME_FIELD, plausible_header

log = logging.getLogger("sm64.scenelab.roms")

ROM_BYTES = 8 << 20
RECORD_VERSION = 1
GAME, FOLDER, PICKED = "game", "folder", "picked"
MAGIC_Z64 = b"\x80\x37\x12\x40"      # the N64's own order (.z64)
MAGIC_N64 = b"\x40\x12\x37\x80"      # each 32-bit word reversed (.n64, and Project64's memory)
MAGIC_V64 = b"\x37\x80\x40\x12"      # each 16-bit half swapped (.v64)
SCAN_RETRY_S = 30.0                  # a folder scan that found nothing waits this long before the next
ATTACH_RETRY_S = 2.0                 # how often a copy from the game may look for Project64
MAX_FILES_PER_FOLDER = 256
RECENT_FILES = 10                    # Project64 1.6 keeps "Roms To Remember" (4 on his machine) as RecentFile1..N


@dataclass(frozen=True)
class Cartridge:
    region: str
    name: bytes        # the header's internal name, exactly
    country: int       # the header's country letter
    label: str         # what the player reads

    @property
    def version(self) -> str:
        return self.label.rsplit(" ", 1)[-1]


CARTRIDGES = {
    "us": Cartridge("us", b"SM64 USAMUNE v1.93u", ord("E"), "Super Mario 64 Usamune v1.93u"),
    "jp": Cartridge("jp", b"SM64 USAMUNE v1.93j", ord("J"), "Super Mario 64 Usamune v1.93j"),
}
#: His files, in z64 order, read 2026-11-09. A record, not a gate: it says whether a stored copy is the build
#: these were measured on.
KNOWN_SHA256 = {
    "us": "7be459e5383746e39e57e7afa48fa325de2bcbaa8d54925927781b65f409f025",
    "jp": "53371669bbdfaa1c95441184044875c3844618c8dd154f68331bd4a2cf46e7e3",
}


class RomRefused(ValueError):
    """A chosen file is not a ROM the Ghost Lab can use. The message is one plain sentence for the player."""


def cartridge(region: str) -> Cartridge:
    try:
        return CARTRIDGES[region]
    except KeyError:
        raise ValueError(f"unknown region {region!r}; expected 'us' or 'jp'") from None


def to_big_endian(data: bytes) -> bytes | None:
    """A cartridge image in the N64's own order, from whichever of the three dump orders it is in; None for
    anything that does not start with a cartridge's first word."""
    if data[:4] == MAGIC_Z64:
        return bytes(data)
    if len(data) % 4 == 0 and data[:4] == MAGIC_N64:
        words = array("I", data)
        words.byteswap()
        return words.tobytes()
    if len(data) % 2 == 0 and data[:4] == MAGIC_V64:
        halves = array("H", data)
        halves.byteswap()
        return halves.tobytes()
    return None


def region_of_header(header: bytes) -> str | None:
    """The region a big-endian header names, if it is exactly one of the two Usamune builds."""
    if plausible_header(header) is None:
        return None
    name = header[NAME_FIELD].rstrip(b" \0")
    for kind in CARTRIDGES.values():
        if name == kind.name and header[0x3E] == kind.country:
            return kind.region
    return None


def identify(image: bytes) -> str | None:
    """The region of a whole big-endian image, or None unless it is one of the two Usamune ROMs."""
    if len(image) != ROM_BYTES:
        return None
    return region_of_header(image[:HEADER_SIZE])


def missing_sentence(region: str) -> str:
    kind = cartridge(region)
    return (f"The Ghost Lab needs {kind.label}: start that game once in Project64 while the Trainer is open, "
            "or choose its ROM file.")


def refused_sentence(wanted: str | None) -> str:
    named = cartridge(wanted).label if wanted else "Super Mario 64 Usamune v1.93u or v1.93j"
    return f"This file is not {named}, so choose the ROM file you play in Project64."


def remembered_pj64_dir() -> Path | None:
    """Project64's folder as setup last saw it (`capture_layer.json`'s `pj64_dir`), read only."""
    try:
        record = json.loads(paths.capture_layer_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    folder = record.get("pj64_dir") if isinstance(record, dict) else None
    return Path(folder) if isinstance(folder, str) and folder else None


def windows_registry():
    """Read-only use of setup's registry adapter: this module only ever calls `get`."""
    from sm64_events.core.capturelayer_win import WinRegistry
    return WinRegistry()


def project64_reader():
    from sm64_events.memory.pj64 import Pj64Memory
    return Pj64Memory()


def _write_atomic(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".tmp-{os.getpid()}-{threading.get_ident()}-{path.name}")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class RomStore:
    """One folder of private copies. Every method is safe to call from any thread."""

    def __init__(self, root: Path, *, pj64_dir: Callable[[], Path | None] = remembered_pj64_dir,
                 registry: Callable[[], object] | None = windows_registry,
                 reader: Callable[[], object] = project64_reader, clock: Callable[[], float] = time.monotonic):
        self.root = Path(root)
        self._pj64_dir, self._registry, self._new_reader, self._clock = pj64_dir, registry, reader, clock
        self._lock = threading.RLock()
        self._verified: dict[str, tuple[int, int]] = {}       # region -> the (size, mtime) last proven intact
        self._scanned_at: float | None = None
        self._reader = None
        self._looked_at: float | None = None
        self._games_seen: set[tuple[int, bytes]] = set()      # (pid, header) already copied or compared

    # -- what other code asks --------------------------------------------------------------------------------

    def rom_for(self, region: str) -> Path | None:
        """The private copy for `region`, or None. A miss scans Project64's folders (at most every 30 s)."""
        cartridge(region)
        with self._lock:
            found = self._intact(region)
            if found is None:
                self.scan_folders()
                found = self._intact(region)
            return found

    def missing_reason(self, region: str) -> str | None:
        return None if self.rom_for(region) is not None else missing_sentence(region)

    def status(self) -> dict:
        """What the pick shows: per region, found or not, where the copy came from, and the sentence."""
        regions = {}
        for region, kind in CARTRIDGES.items():
            found = self.rom_for(region)
            record = self._record(region) if found is not None else None
            regions[region] = {"found": found is not None, "label": kind.label, "version": kind.version,
                               "source": record.get("source") if record else None,
                               "known_build": bool(record and record.get("sha256") == KNOWN_SHA256[region]),
                               "reason": None if found is not None else missing_sentence(region)}
        return {"regions": regions}

    # -- A: the running game -----------------------------------------------------------------------------------

    def copy_from_game(self) -> str | None:
        """Copy the cartridge Project64 is running, if it is one of the two Usamune builds. Returns its region.
        Finding Project64 with the store's own reader is tried at most every 2 s."""
        with self._lock:
            reader = self._game_reader()
            return self.copy_from(reader) if reader is not None else None

    def copy_from(self, reader) -> str | None:
        """`copy_from_game` through a reader someone else attached (the setup observer keeps one per Project64
        process). Each (process, cartridge) pair is read once; the reader is never detached here."""
        with self._lock:
            head = reader.rom_header()
            if head is None:
                return None
            key = (reader.pid, bytes(head))
            if key in self._games_seen:
                return None
            raw = reader.cartridge_image(ROM_BYTES)
            image = to_big_endian(raw) if raw is not None else None
            region = identify(image) if image is not None else None
            if raw is None:
                return None                     # the image went away mid-read; the next ask tries again
            self._games_seen.add(key)
            if region is None:
                return None
            self._keep(region, image, GAME, None)
            return region

    def _game_reader(self):
        if self._reader is not None and self._reader.alive():
            return self._reader
        now = self._clock()
        if self._looked_at is not None and now - self._looked_at < ATTACH_RETRY_S:
            return None
        self._looked_at = now
        reader = self._new_reader()
        if not reader.attach():
            return None
        if self._reader is not None:
            self._reader.detach()
        self._reader = reader
        return reader

    # -- B: Project64's folders ------------------------------------------------------------------------------

    def scan_folders(self, pj64_dir: Path | None = None) -> list[str]:
        """Look for each missing region in the files Project64 opened and its ROM folders. Returns the regions
        found. `pj64_dir` is Project64's folder when the caller knows it (setup's own reading); otherwise setup's
        record names it. Files are opened for reading only."""
        with self._lock:
            missing = [region for region in CARTRIDGES if self._intact(region) is None]
            now = self._clock()
            if not missing or (self._scanned_at is not None and now - self._scanned_at < SCAN_RETRY_S):
                return []
            self._scanned_at = now
            found = []
            for path in self._candidate_files(Path(pj64_dir) if pj64_dir else self._pj64_dir()):
                image = self._read_candidate(path)
                region = identify(image) if image is not None else None
                if region in missing and region not in found:
                    self._keep(region, image, FOLDER, path)
                    found.append(region)
                if len(found) == len(missing):
                    break
            return found

    def _candidate_files(self, pj64: Path | None) -> Iterable[Path]:
        """Files Project64 last opened first, then each folder's files of exactly a ROM's size, each once."""
        files, folders = self._project64_settings()
        if pj64 is not None:
            folders += [pj64 / "roms", pj64 / "Roms", pj64]
        seen: set[str] = set()
        for path in files + [child for folder in folders for child in self._listing(folder)]:
            key = os.path.normcase(str(path))
            if key not in seen:
                seen.add(key)
                yield path

    def _project64_settings(self) -> tuple[list[Path], list[Path]]:
        if self._registry is None:
            return [], []
        from sm64_events.core.capturelayer import REGISTRY_KEY
        registry = self._registry()
        def value(name):
            text = registry.get(REGISTRY_KEY, name)
            return Path(text) if isinstance(text, str) and text.strip() else None
        files = [value(f"RecentFile{n}") for n in range(1, RECENT_FILES + 1)]
        folders = [value("Rom Directory")] + [value(f"RecentDir{n}") for n in range(1, RECENT_FILES + 1)]
        return [p for p in files if p is not None], [p for p in folders if p is not None]

    @staticmethod
    def _listing(folder: Path) -> list[Path]:
        try:
            entries = sorted(folder.iterdir())[:MAX_FILES_PER_FOLDER]
        except OSError:
            return []
        return [entry for entry in entries if entry.is_file()]

    @staticmethod
    def _read_candidate(path: Path) -> bytes | None:
        """The file as a big-endian image, if it is a ROM's size and its header names a Usamune build."""
        try:
            if path.stat().st_size != ROM_BYTES:
                return None
            with path.open("rb") as file:
                head = to_big_endian(file.read(HEADER_SIZE))
                if head is None or region_of_header(head) is None:
                    return None
                file.seek(0)
                data = file.read(ROM_BYTES + 1)
        except OSError:
            return None
        return to_big_endian(data) if len(data) == ROM_BYTES else None

    # -- C: a file the player chooses ------------------------------------------------------------------------

    def adopt(self, data: bytes, *, region: str | None = None) -> str:
        """Keep a chosen file. Returns its region, which may be the other one than asked for: that copy is
        kept too. Raises RomRefused with one sentence when the file is neither Usamune ROM."""
        if region is not None:
            cartridge(region)
        image = to_big_endian(data) if len(data) == ROM_BYTES else None
        found = identify(image) if image is not None else None
        if found is None:
            raise RomRefused(refused_sentence(region))
        with self._lock:
            self._keep(found, image, PICKED, None)
        return found

    # -- the copies themselves -------------------------------------------------------------------------------

    def _copy(self, region: str) -> Path:
        return self.root / f"{region}.z64"

    def _record_path(self, region: str) -> Path:
        return self.root / f"{region}.json"

    def _record(self, region: str) -> dict | None:
        try:
            record = json.loads(self._record_path(region).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or record.get("version") != RECORD_VERSION:
            return None
        return record

    def _intact(self, region: str) -> Path | None:
        """The copy, if it is there and still the bytes its record names. Proven once per size and mtime."""
        copy = self._copy(region)
        try:
            stat = copy.stat()
        except OSError:
            return None
        stamp = (stat.st_size, stat.st_mtime_ns)
        if self._verified.get(region) == stamp:
            return copy
        record = self._record(region)
        try:
            data = copy.read_bytes() if record else b""
        except OSError:
            return None
        if not record or identify(data) != region or hashlib.sha256(data).hexdigest() != record.get("sha256"):
            log.warning("the stored %s ROM copy does not match its record; it will be found again", region)
            return None
        self._verified[region] = stamp
        return copy

    def _keep(self, region: str, image: bytes, source: str, origin: Path | None) -> None:
        """Store `image` unless the same bytes are already kept. A game copy replaces different bytes; a folder
        or picked copy fills a missing region and replaces a copy only when it came from the same kind of step."""
        digest = hashlib.sha256(image).hexdigest()
        current = self._record(region) if self._intact(region) is not None else None
        if current is not None and current.get("sha256") == digest:
            return
        if current is not None and source != GAME and current.get("source") == GAME:
            log.info("kept the %s ROM copied from the game over a %s file with other bytes", region, source)
            return
        self.root.mkdir(parents=True, exist_ok=True)
        _write_atomic(self._copy(region), image)
        record = {"version": RECORD_VERSION, "region": region, "sha256": digest, "source": source,
                  "from": str(origin) if origin is not None else None,
                  "stored_utc": datetime.now(UTC).isoformat(timespec="seconds")}
        _write_atomic(self._record_path(region), json.dumps(record, indent=1).encode("utf-8"))
        self._verified.pop(region, None)
        log.info("kept the %s ROM (%s, sha256 %s)", region, source, digest[:16])


_default: RomStore | None = None
_default_lock = threading.Lock()


def default_store() -> RomStore:
    """The process's store, under the lab root (`paths.scene_lab_root()`)."""
    global _default
    with _default_lock:
        if _default is None:
            _default = RomStore(paths.scene_lab_root() / "roms")
        return _default


def rom_for(region: str) -> Path | None:
    """I3: the private copy of `region`'s Usamune ROM, or None."""
    return default_store().rom_for(region)


def missing_reason(region: str) -> str | None:
    """I3: None when `rom_for(region)` has a ROM, else one plain sentence naming the exact ROM and the fix."""
    return default_store().missing_reason(region)
