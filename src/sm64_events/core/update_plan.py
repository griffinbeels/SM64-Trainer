# src/sm64_events/core/update_plan.py
"""Manifest schema + update planning (pure, stdlib-only).

THE registry for release asset names and the per-file manifest format that
release tooling (tools/make_manifest.py) writes and the updater + bootstrap
consume. A manifest lists every installed file with the SHA-256 of its
UNCOMPRESSED content plus the byte range of its compressed data inside the
release zip — that byte range is what lets core/update_fetch.py download
only changed files via HTTP Range requests.

Manifests arrive over the network, so parse_manifest() is defensive: any
missing/mistyped field or unsafe path (absolute, ``..``, backslash, empty
segment) raises ValueError — a hostile manifest must never be able to write
outside the install root.

Stays stdlib-only (json/hashlib/dataclasses/pathlib): the bootstrap
installer imports this module and must remain a tiny PyInstaller build."""
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = 1
ZIP_ASSET = "SM64Trainer-full.zip"
MANIFEST_ASSET = "manifest.json"
# Old shipped updaters can ONLY install an asset with exactly this name —
# every release publishes the bootstrap installer under it, forever.
BOOTSTRAP_ASSET = "SM64Trainer.exe"
INSTALLED_MANIFEST = "installed_manifest.json"
# Release bodies are composed as: first-time-setup header + THIS MARKER +
# patch notes (tools/release.py compose_release_body). The GitHub release
# page shows everything (new users get setup steps); core/release_feed.py's
# strip_body runs it through the marker for EVERY release in the feed, so
# the in-app popup shows ONLY the patch notes. It's a
# natural heading on purpose: clients too old to strip (≤1.4.0) render it as
# a plausible section title instead of stray markup.
PATCH_NOTES_MARKER = "## What's new"
# The apply layer's on-disk state (core/update_apply.py imports these from
# here — ONE registry). Reserved below: a manifest that named these could
# rename the crash-recovery journal away mid-swap (defeating rollback) or
# nest the backup tree into itself (wave-0 review, 2026-07-23).
BACKUP_DIR = ".update_backup"
STAGING_DIR = ".update_staging"
JOURNAL_NAME = "update_journal.json"

_RESERVED_FIRST_SEGMENTS = {INSTALLED_MANIFEST, JOURNAL_NAME,
                            BACKUP_DIR, STAGING_DIR}
# Windows-invalid filename characters + NUL/control chars. The colon matters
# most: 'C:/x' is DRIVE-RELATIVE (root.joinpath('C:', 'x') escapes the
# install root entirely) and 'file.dll:name' is an NTFS alternate data
# stream (wave-0 review, 2026-07-23).
_BAD_PATH_CHARS = set('<>:"|?*') | {chr(code) for code in range(32)}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ManifestEntry:
    path: str          # forward-slash relative path under the install root
    sha256: str        # lowercase hex of the UNCOMPRESSED file content
    size: int          # uncompressed bytes
    zip_offset: int    # absolute offset of the compressed data in the zip
    zip_csize: int     # compressed size in bytes
    zip_method: int    # 0 = stored, 8 = deflate


@dataclass(frozen=True)
class Manifest:
    version: str
    files: tuple[ManifestEntry, ...]


def _safe_path(path: str) -> bool:
    if not path or "\\" in path or path.startswith("/"):
        return False
    if any(ch in _BAD_PATH_CHARS for ch in path):
        return False
    segments = path.split("/")
    if segments[0] in _RESERVED_FIRST_SEGMENTS:
        return False
    return all(seg not in ("", ".", "..") for seg in segments)


def parse_manifest(text: str) -> Manifest:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError(f"manifest is not valid JSON: {err}") from err
    if not isinstance(doc, dict):
        raise ValueError("manifest is not an object")
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"unsupported manifest schema: {doc.get('schema')!r}")
    entries: list[ManifestEntry] = []
    for raw in doc.get("files", []):
        try:
            entry = ManifestEntry(
                path=str(raw["path"]),
                sha256=str(raw["sha256"]).lower(),
                size=int(raw["size"]),
                zip_offset=int(raw["zip_offset"]),
                zip_csize=int(raw["zip_csize"]),
                zip_method=int(raw["zip_method"]))
        except (KeyError, TypeError, ValueError) as err:
            raise ValueError(f"bad manifest entry: {raw!r}") from err
        if not _safe_path(entry.path):
            raise ValueError(f"unsafe manifest path: {entry.path!r}")
        if not _SHA256_RE.fullmatch(entry.sha256):
            raise ValueError(f"{entry.path}: sha256 is not 64 hex chars")
        if entry.size < 0 or entry.zip_offset < 0 or entry.zip_csize < 0:
            raise ValueError(f"{entry.path}: negative size/offset/csize")
        if entry.zip_method not in (0, 8):
            raise ValueError(f"{entry.path}: unsupported zip method "
                             f"{entry.zip_method}")
        entries.append(entry)
    paths = [entry.path for entry in entries]
    if len(paths) != len(set(paths)):
        raise ValueError("manifest lists a path more than once")
    return Manifest(version=str(doc.get("version", "")), files=tuple(entries))


def manifest_to_json(m: Manifest) -> str:
    return json.dumps({"schema": SCHEMA, "version": m.version,
                       "files": [asdict(entry) for entry in m.files]}, indent=1)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class UpdatePlan:
    fetch: tuple[ManifestEntry, ...]   # download these (new/changed/corrupt)
    delete: tuple[str, ...]            # remove these (no longer shipped)
    download_bytes: int                # sum of zip_csize over fetch


def build_plan(remote: Manifest, installed: "Manifest | None", root: Path, *,
               verify_local: bool = False,
               file_hash=file_sha256) -> UpdatePlan:
    """Diff the remote manifest against the installed record + the disk.

    verify_local=True re-hashes every supposedly-unchanged file (a forced
    'Check for updates' passes it) so silent same-size corruption self-heals;
    the routine hourly check uses the cheap existence+size path."""
    if not remote.files:
        # A self-consistent-but-empty manifest (release-side build bug)
        # would otherwise plan deleting the ENTIRE install.
        raise ValueError("remote manifest lists no files")
    installed_files = {e.path: e for e in (installed.files if installed else ())}
    fetch: list[ManifestEntry] = []
    for entry in remote.files:
        local = root.joinpath(*entry.path.split("/"))
        recorded = installed_files.get(entry.path)
        stale = (recorded is None
                 or recorded.sha256 != entry.sha256
                 or not local.is_file()
                 or local.stat().st_size != entry.size
                 or (verify_local and file_hash(local) != entry.sha256))
        if stale:
            fetch.append(entry)
    remote_paths = {e.path for e in remote.files}
    delete = tuple(sorted(p for p in installed_files if p not in remote_paths))
    return UpdatePlan(fetch=tuple(fetch), delete=delete,
                      download_bytes=sum(e.zip_csize for e in fetch))
