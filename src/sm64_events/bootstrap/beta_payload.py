"""A bounded, checksummed app ZIP appended to the private installer executable.

The small PyInstaller stub extracts only its own standard-library runtime. The
large application stays appended until installation, and ordinary app launches
use the installed directory. No network or public release feed participates.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import zipfile

MAGIC = b"SM64-BETA-ZIP-V1"
FOOTER = struct.Struct("<16sQ32s")
MAX_ARCHIVE_BYTES = 1 << 30
MAX_UNPACKED_BYTES = 2 << 30
MAX_FILES = 20000
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                     *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def append_payload(stub: Path, archive: Path, destination: Path) -> str:
    """Build one downloadable EXE from already validated app bytes."""
    length = archive.stat().st_size
    if not 0 < length <= MAX_ARCHIVE_BYTES or stub.resolve() == destination.resolve():
        raise ValueError("invalid private installer payload")
    digest = hashlib.sha256()
    with stub.open("rb") as launcher, archive.open("rb") as payload, destination.open("wb") as out:
        shutil.copyfileobj(launcher, out)
        while chunk := payload.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
        out.write(FOOTER.pack(MAGIC, length, digest.digest()))
    verify_payload(destination)
    with destination.open("rb") as complete:
        return hashlib.file_digest(complete, "sha256").hexdigest()


def verify_payload(executable: Path, progress=None) -> str:
    """Verify appended bytes before any installed program file is changed."""
    size = executable.stat().st_size
    if size <= FOOTER.size:
        raise ValueError("private installer has no complete application payload")
    with executable.open("rb") as stream:
        stream.seek(-FOOTER.size, 2)
        magic, length, expected = FOOTER.unpack(stream.read(FOOTER.size))
        if magic != MAGIC or not 0 < length <= MAX_ARCHIVE_BYTES or length >= size - FOOTER.size:
            raise ValueError("private installer payload footer is invalid")
        stream.seek(size - FOOTER.size - length)
        digest = hashlib.sha256()
        remaining = length
        while remaining:
            chunk = stream.read(min(1 << 20, remaining))
            if not chunk:
                raise ValueError("private installer payload is incomplete")
            digest.update(chunk)
            remaining -= len(chunk)
            if progress:
                progress((length - remaining) / length)
        if digest.digest() != expected:
            raise ValueError("private installer payload failed verification; download it again")
    return digest.hexdigest()


def checked_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Reject traversal, Win32 aliases, links, duplicate files and oversized ZIPs."""
    members = archive.infolist()
    if not 0 < len(members) <= MAX_FILES or sum(m.file_size for m in members) > MAX_UNPACKED_BYTES:
        raise ValueError("private installer archive exceeds its limits")
    seen = set()
    for member in members:
        # ZipInfo normalizes backslashes on Windows and truncates NULs. Read
        # the original central-directory spelling before trusting that alias.
        name = member.orig_filename
        path = PurePosixPath(name)
        mode = member.external_attr >> 16
        if (not name or "\\" in name or path.is_absolute() or member.flag_bits & 1
                or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))
                or any(part in ("", ".", "..") or ":" in part or part[-1:] in (".", " ")
                       or any(ord(c) < 32 for c in part)
                       or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED for part in name.rstrip("/").split("/"))):
            raise ValueError("private installer archive contains an unsafe path")
        folded = name.rstrip("/").casefold()
        if folded in seen:
            raise ValueError("private installer archive contains duplicate paths")
        seen.add(folded)
    files = {m.filename.rstrip("/").casefold() for m in members if not m.is_dir()}
    if any(any(parent.as_posix().casefold() in files for parent in PurePosixPath(m.filename).parents
               if parent.as_posix() != ".") for m in members):
        raise ValueError("private installer archive contains a file-directory collision")
    return members


def extract_payload(executable: Path, directory: Path, progress=None) -> None:
    """Extract only checked members into a newly created private staging tree."""
    with zipfile.ZipFile(executable) as archive:
        members = checked_members(archive)
        total = max(1, sum(m.file_size for m in members))
        done = 0
        for member in members:
            target = directory.joinpath(*PurePosixPath(member.filename).parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, 1 << 20)
            done += member.file_size
            if progress:
                progress(done / total)
