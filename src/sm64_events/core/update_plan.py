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
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = 1
ZIP_ASSET = "SM64Trainer-full.zip"
MANIFEST_ASSET = "manifest.json"
# Old shipped updaters can ONLY install an asset with exactly this name —
# every release publishes the bootstrap installer under it, forever.
BOOTSTRAP_ASSET = "SM64Trainer.exe"
INSTALLED_MANIFEST = "installed_manifest.json"


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
    return all(seg not in ("", ".", "..") for seg in path.split("/"))


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
        entries.append(entry)
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
