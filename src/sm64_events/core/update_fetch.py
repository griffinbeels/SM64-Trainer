"""Download ONLY the planned files from the release zip and stage them.

GitHub release assets redirect to a CDN that honors HTTP Range requests
(the same mechanism electron-updater's differential downloads rely on), so
each changed file's compressed bytes are fetched straight out of
SM64Trainer-full.zip without downloading the rest. Adjacent entries are
coalesced into one request (many tiny ranged GETs are slower than one big
one — a known differential-download gotcha); anything that breaks Range
(proxy, redirect weirdness) raises RangeUnsupported and the caller falls
back to fetch_full_zip, which streams the whole asset and extracts the
planned files. BOTH paths verify every staged file's SHA-256 against the
manifest before it is ever eligible to be applied."""
import hashlib
import shutil
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from sm64_events.core.update_plan import ManifestEntry, UpdatePlan

_UA = "SM64Trainer-updater"


class RangeUnsupported(RuntimeError):
    """The server ignored/mangled a Range request — use the full-zip fallback."""


@dataclass(frozen=True)
class Span:
    offset: int
    length: int
    entries: tuple[ManifestEntry, ...]


def _span(group: list[ManifestEntry]) -> Span:
    start = group[0].zip_offset
    end = group[-1].zip_offset + group[-1].zip_csize
    return Span(offset=start, length=end - start, entries=tuple(group))


def coalesce(entries, *, gap: int = 64 * 1024,
             max_span: int = 32 * 1024 * 1024) -> tuple[Span, ...]:
    """Merge byte ranges that are within `gap` of each other (paying the gap
    bytes beats another round-trip) without letting one request exceed
    `max_span`."""
    ordered = sorted(entries, key=lambda e: e.zip_offset)
    spans: list[Span] = []
    group: list[ManifestEntry] = []
    for entry in ordered:
        if group:
            end = group[-1].zip_offset + group[-1].zip_csize
            joined = entry.zip_offset + entry.zip_csize - group[0].zip_offset
            if entry.zip_offset - end <= gap and joined <= max_span:
                group.append(entry)
                continue
            spans.append(_span(group))
        group = [entry]
    if group:
        spans.append(_span(group))
    return tuple(spans)


def _ranged_get(http, url: str, offset: int, length: int) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Range": f"bytes={offset}-{offset + length - 1}"})
    with http(req) as resp:
        status = getattr(resp, "status", 206)
        if status != 206:
            raise RangeUnsupported(f"expected 206 partial content, got {status}")
        data = resp.read()
    if len(data) != length:
        raise RangeUnsupported(f"range returned {len(data)} bytes, "
                               f"wanted {length}")
    return data


def decode_entry(entry: ManifestEntry, comp: bytes) -> bytes:
    """Raw zip-entry bytes -> verified file content (ValueError on mismatch)."""
    if entry.zip_method == 0:
        data = comp
    elif entry.zip_method == 8:
        inflater = zlib.decompressobj(-15)     # raw deflate, no zlib header
        data = inflater.decompress(comp) + inflater.flush()
    else:
        raise ValueError(f"{entry.path}: unsupported zip method "
                         f"{entry.zip_method}")
    if len(data) != entry.size:
        raise ValueError(f"{entry.path}: inflated to {len(data)} bytes, "
                         f"manifest says {entry.size}")
    if hashlib.sha256(data).hexdigest() != entry.sha256:
        raise ValueError(f"{entry.path}: checksum mismatch after download")
    return data


def _stage(staging: Path, entry: ManifestEntry, data: bytes) -> None:
    dest = staging.joinpath(*entry.path.split("/"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def fetch_plan(plan: UpdatePlan, zip_url: str, staging: Path, *, http,
               progress=None) -> None:
    """Range-fetch every planned file into `staging` (tree mirrors install).
    Raises RangeUnsupported (fall back to fetch_full_zip) or ValueError
    (integrity failure — abort)."""
    total = max(1, plan.download_bytes)
    done = 0
    for span in coalesce(plan.fetch):
        body = _ranged_get(http, zip_url, span.offset, span.length)
        for entry in span.entries:
            start = entry.zip_offset - span.offset
            comp = body[start:start + entry.zip_csize]
            _stage(staging, entry, decode_entry(entry, comp))
            done += entry.zip_csize
            if progress:
                progress(min(1.0, done / total))


def fetch_full_zip(plan: UpdatePlan, zip_url: str, zip_sha256: str,
                   staging: Path, *, http, progress=None) -> None:
    """Fallback: stream the whole zip, verify its digest, extract the planned
    files (each still verified against its manifest hash)."""
    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / "_full.zip"
    digest = hashlib.sha256()
    req = urllib.request.Request(zip_url, headers={"User-Agent": _UA})
    try:
        with http(req) as resp:
            total = int((resp.headers or {}).get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(min(1.0, done / total))
        if digest.hexdigest() != zip_sha256.lower():
            raise ValueError("full zip checksum mismatch")
        with zipfile.ZipFile(tmp) as zf:
            for entry in plan.fetch:
                data = zf.read(entry.path)
                if (len(data) != entry.size
                        or hashlib.sha256(data).hexdigest() != entry.sha256):
                    raise ValueError(f"{entry.path}: zip content does not "
                                     "match manifest")
                _stage(staging, entry, data)
    finally:
        tmp.unlink(missing_ok=True)


def free_disk_ok(path: Path, needed_bytes: int, *,
                 margin: int = 50 * 1024 * 1024) -> bool:
    return shutil.disk_usage(path).free >= needed_bytes + margin
