"""The PJ64 Fixups: download the tested release and put it in Project64.

PJ64 Fixups is aglab2's RSP plugin (``RSP.dll``), the standard fix set for
Project64 1.6 speedrunning. Its repository states no license, so the trainer
never bundles it: Setup downloads the one release named in
`data/setup_baseline.json` (``fixups``) from the author's GitHub, checks both
the archive's and the DLL's SHA-256 against that file, and copies the DLL to
``Plugin\\RSP.dll`` (his decision, 2026-11-09: "Yes, download and copy into
the folder."). The DLL's hash is the setup check's own ``rsp_plugin`` row, so
"installed" and "matches the tested setup" are one fact.

The file it replaces is kept once beside it as ``RSP.before-fixups`` (not a
.dll, so Project64 never lists it). Nothing is written while Project64 holds
the file.

A protected Plugin folder (Program Files) needs administrator permission
(his decision, 2026-11-09: "we can ask for admin permission when installing").
This module never elevates. The capture layer's one UAC transaction
(`core/plugin_elevation_worker.py`) copies the kept download (`cached`) beside
the graphics plugin, after checking it against the bundled baseline's hash
itself, so one permission prompt covers both.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import urllib.request
import zipfile
from pathlib import Path

from sm64_events.core.plugin_installation import in_use

log = logging.getLogger("sm64.setup")
RSP_DLL = "RSP.dll"
BACKUP = "RSP.before-fixups"
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 30


class FixupError(Exception):
    """A failure, worded for the person looking at the wizard."""

    def __init__(self, message, reason="failed"):
        super().__init__(message)
        self.reason = reason


def tested_sha256(baseline: dict) -> str:
    """The tested RSP.dll's hash: the setup check's `rsp_plugin` row."""
    return next(item["sha256"] for item in baseline["items"] if item["id"] == "rsp_plugin")


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "SM64Trainer"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 - pinned https URL
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise OSError("the download is larger than expected")
    return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fixups:
    """`install(plugin_dir)` is the one door; `status()` reports the last try."""

    def __init__(self, baseline: dict, cache_dir: Path, download=_download):
        self._release = baseline["fixups"]
        self._dll_sha = tested_sha256(baseline)
        self._cache = Path(cache_dir) / f"pj64fixups-{self._release['version']}-{RSP_DLL}"
        self._download = download
        self._error: str | None = None
        self._reason: str | None = None

    def status(self) -> dict:
        return {"version": self._release["version"], "error": self._error, "reason": self._reason}

    def cached(self) -> Path:
        """The tested DLL as a file on disk, downloaded if it is not kept yet."""
        dll = self._dll()
        try:
            if self._cache.read_bytes() == dll:
                return self._cache
        except OSError:
            pass
        raise FixupError("We could not keep the PJ64 Fixups download on this computer.")

    def _dll(self) -> bytes:
        try:
            cached = self._cache.read_bytes()
            if _sha256(cached) == self._dll_sha:
                return cached
        except OSError:
            pass
        try:
            archive = self._download(self._release["url"])
        except OSError as error:
            log.warning("could not download the PJ64 Fixups: %s", error)
            raise FixupError("We could not download the PJ64 Fixups. Check your internet connection, "
                             "then try again.") from error
        if _sha256(archive) != self._release["zip_sha256"]:
            raise FixupError("The PJ64 Fixups download was not the file we tested, so we did not use it.")
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                name = next(entry for entry in bundle.namelist() if Path(entry).name.lower() == RSP_DLL.lower())
                dll = bundle.read(name)
        except (zipfile.BadZipFile, StopIteration, OSError) as error:
            raise FixupError("The PJ64 Fixups download could not be opened.") from error
        if _sha256(dll) != self._dll_sha:
            raise FixupError("The PJ64 Fixups download was not the file we tested, so we did not use it.")
        try:
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            self._cache.write_bytes(dll)
        except OSError:
            log.info("could not keep a copy of the PJ64 Fixups download", exc_info=True)
        return dll

    def install(self, plugin_dir: Path) -> str:
        """"present" when the tested DLL is already there, else "installed".
        Raises FixupError (also kept for `status`) and changes nothing then."""
        try:
            result = self._install(Path(plugin_dir))
        except FixupError as error:
            self._error, self._reason = str(error), error.reason
            raise
        self._error = self._reason = None
        return result

    def _install(self, plugin_dir: Path) -> str:
        target = plugin_dir / RSP_DLL
        try:
            current = target.read_bytes()
        except FileNotFoundError:
            current = None
        except OSError as error:
            raise FixupError("We could not read RSP.dll in your Plugin folder.") from error
        if current is not None and _sha256(current) == self._dll_sha:
            return "present"
        if in_use(target):
            raise FixupError("Close Project64, then install the PJ64 Fixups.")
        dll = self._dll()
        scratch = plugin_dir / (RSP_DLL + ".sm64trainer-new")
        backup = plugin_dir / BACKUP
        try:
            plugin_dir.mkdir(parents=True, exist_ok=True)
            if current is not None and not backup.exists():
                backup.write_bytes(current)
            scratch.write_bytes(dll)
            os.replace(scratch, target)
        except OSError as error:
            try:
                scratch.unlink(missing_ok=True)
            except OSError:
                pass
            log.warning("could not write the PJ64 Fixups to %s: %s", target, error)
            raise FixupError("We could not put the PJ64 Fixups in your Plugin folder. Windows blocked the change.",
                             reason="access_denied" if isinstance(error, PermissionError) else "failed") from error
        log.info("PJ64 Fixups %s installed at %s", self._release["version"], target)
        return "installed"
