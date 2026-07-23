"""Zip the onedir build deterministically + emit the update manifest.

The manifest records, per file, the SHA-256 of its content AND the byte
range of its compressed data inside the zip. Offsets are read from each
entry's LOCAL file header (header_offset + 30 + name-length + extra-length)
— NOT the central directory: the local extra field can differ in length
from the central one, and a wrong offset would make the updater
Range-fetch garbage. tests/test_make_manifest.py proves every recorded
range slices+inflates back to the exact file content.

Deterministic zip (sorted paths, fixed 1980 timestamps): two builds of
identical trees produce identical zips, so unchanged files keep identical
(offset, csize) across releases only when content before them is unchanged
— offsets are per-release anyway (the updater always reads the CURRENT
manifest), determinism just keeps diffs/debugging sane."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sm64_events.core.update_plan import SCHEMA  # noqa: E402


def build_zip(src_dir: Path, zip_path: Path) -> None:
    paths = sorted(p for p in src_dir.rglob("*") if p.is_file())
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            arc = p.relative_to(src_dir).as_posix()
            info = zipfile.ZipInfo(arc, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, p.read_bytes())


def entry_spans(zip_path: Path) -> dict[str, tuple[int, int, int]]:
    """arcname -> (absolute data offset, compressed size, method), from the
    LOCAL headers."""
    spans: dict[str, tuple[int, int, int]] = {}
    with zipfile.ZipFile(zip_path) as zf, open(zip_path, "rb") as raw:
        for info in zf.infolist():
            raw.seek(info.header_offset)
            header = raw.read(30)
            if header[:4] != b"PK\x03\x04":
                raise ValueError(f"{info.filename}: bad local header magic")
            name_len = int.from_bytes(header[26:28], "little")
            extra_len = int.from_bytes(header[28:30], "little")
            data_offset = info.header_offset + 30 + name_len + extra_len
            spans[info.filename] = (data_offset, info.compress_size,
                                    info.compress_type)
    return spans


def make_manifest(zip_path: Path, version: str) -> str:
    spans = entry_spans(zip_path)
    files = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            data = zf.read(info.filename)
            offset, csize, method = spans[info.filename]
            files.append({"path": info.filename,
                          "sha256": hashlib.sha256(data).hexdigest(),
                          "size": len(data),
                          "zip_offset": offset,
                          "zip_csize": csize,
                          "zip_method": method})
    return json.dumps({"schema": SCHEMA, "version": version, "files": files},
                      indent=1)
