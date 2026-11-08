"""Write the third-party source archives every release attaches beside its binaries.

    uv run python tools/corresponding_source.py --out DIR     # download (cached), verify, write the archives
    uv run python tools/corresponding_source.py --list        # the asset names and pinned sizes, nothing fetched

SPDX-License-Identifier: GPL-3.0-or-later

The GNU GPL asks whoever distributes a program's binary to make its complete
source available from the same place. The trainer's own source, with every
build recipe and patch, is each release's tag, which GitHub offers on the
release page. This writes the rest: every third-party source archive whose
code is in something the trainer ships (the graphics plugin, ffmpeg.exe and
the FFmpeg libraries inside PyAV's wheel), pinned by URL and SHA-256 in
licenses/third-party-sources.json. tools/release.py attaches the archives,
each with a `.sha256` sidecar like the other assets, to every release
(licenses/README.md says why every release and not a separate page).

Each archive is an uncompressed tar of the original files, unchanged, plus a
README.txt and the manifest itself: members sorted, fixed times and owners,
USTAR format, so the same manifest always gives the same bytes. Downloads are
cached in %LOCALAPPDATA%/SM64Trainer/build-cache/corresponding-source and
re-checked against their pins every time.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "licenses" / "third-party-sources.json"
FIXED_MTIME = 1_600_000_000   # every member's time; the archives carry no build date


def load(manifest: Path = MANIFEST) -> dict:
    return json.loads(manifest.read_text(encoding="utf-8"))


def asset_names(manifest: Path = MANIFEST) -> list[str]:
    """Every release asset this writes: each archive and its `.sha256` sidecar."""
    return [name for archive in load(manifest)["archives"] for name in (archive, archive + ".sha256")]


def filename(item: dict) -> str:
    return item.get("filename") or PurePosixPath(urlparse(item["url"]).path).name


def default_cache() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "SM64Trainer" / "build-cache" / "corresponding-source"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(cache: Path, item: dict, user_agent: str) -> Path:
    """One pinned source, downloaded once and checked against its pin every time."""
    target = cache / item["sha256"][:16] / filename(item)
    if target.is_file() and _sha256(target) == item["sha256"]:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    request = urllib.request.Request(item["url"], headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=900) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out)
    if _sha256(partial) != item["sha256"]:
        partial.unlink()
        raise ValueError(f"{item['url']} is not the pinned file ({item['sha256']})")
    partial.replace(target)
    return target


def readme(name: str, archive: dict) -> str:
    lines = [name, "", archive["about"], "",
             "Each file below is the original archive from its URL, unchanged; its SHA-256 is pinned in",
             "third-party-sources.json beside this file and in the SM64 Trainer repository at this release's tag.",
             ""]
    for item in archive["sources"]:
        lines += [f"{filename(item)}", f"  from     {item['url']}", f"  sha256   {item['sha256']}",
                  f"  license  {item['license']}", f"  used in  {', '.join(item['used_in'])}"]
        if item.get("note"):
            lines.append(f"  note     {item['note']}")
        lines.append("")
    return "\n".join(lines)


def _member(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = size, FIXED_MTIME, 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def write_archive(path: Path, stem: str, files: dict[str, bytes | Path]) -> None:
    """A byte-stable tar: sorted members under `stem/`, fixed metadata, USTAR."""
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as tar:
        for name in sorted(files):
            source = files[name]
            if isinstance(source, Path):
                with source.open("rb") as handle:
                    tar.addfile(_member(f"{stem}/{name}", source.stat().st_size), handle)
            else:
                tar.addfile(_member(f"{stem}/{name}", len(source)), io.BytesIO(source))


def write_archives(out_dir: Path, *, cache: Path | None = None, manifest: Path = MANIFEST) -> list[Path]:
    """Download (cached) and verify every pinned source, then write each archive
    and its `.sha256` sidecar into `out_dir`. Returns them in asset order."""
    spec = load(manifest)
    cache = cache or default_cache()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, archive in spec["archives"].items():
        files: dict[str, bytes | Path] = {"README.txt": readme(name, archive).encode("utf-8"),
                                          "third-party-sources.json": manifest.read_bytes()}
        for item in archive["sources"]:
            files[filename(item)] = fetch(cache, item, spec["user_agent"])
        path = out_dir / name
        write_archive(path, PurePosixPath(name).stem, files)
        sidecar = out_dir / (name + ".sha256")
        sidecar.write_text(f"{_sha256(path)}  {name}\n", encoding="ascii", newline="\n")
        written += [path, sidecar]
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, help="write the archives and their .sha256 sidecars here")
    parser.add_argument("--cache", type=Path, default=None, help="download cache folder")
    parser.add_argument("--list", action="store_true", help="print the asset names, fetch nothing")
    args = parser.parse_args()
    if args.list or not args.out:
        for name in asset_names():
            print(name)
        return 0
    try:
        for path in write_archives(args.out, cache=args.cache):
            print(f"{path.stat().st_size / 1e6:9.1f} MB  {path}")
    except (ValueError, OSError) as failure:
        print(failure)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
