import hashlib
import io
import zlib
from pathlib import Path

import pytest

from sm64_events.core.update_plan import Manifest, ManifestEntry, UpdatePlan
from sm64_events.core.update_fetch import (RangeUnsupported, Span, coalesce,
                                           decode_entry, fetch_full_zip,
                                           fetch_plan, free_disk_ok)


def _deflate(data: bytes) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def _entry(path, data, offset, method=8):
    comp = _deflate(data) if method == 8 else data
    return ManifestEntry(path=path, sha256=hashlib.sha256(data).hexdigest(),
                         size=len(data), zip_offset=offset,
                         zip_csize=len(comp), zip_method=method), comp


class _Resp(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _range_http(blob: bytes, *, honor_range=True, log=None):
    def opener(req):
        rng = req.headers.get("Range")
        if log is not None:
            log.append(rng)
        if rng and honor_range:
            lo, hi = rng.removeprefix("bytes=").split("-")
            return _Resp(blob[int(lo):int(hi) + 1], status=206)
        return _Resp(blob, status=200)
    return opener


# --- coalesce ---

def _span_entry(offset, csize):
    return ManifestEntry(path=f"f{offset}", sha256="00" * 32, size=csize,
                         zip_offset=offset, zip_csize=csize, zip_method=0)


def test_coalesce_merges_adjacent_and_near_entries():
    a, b, c = _span_entry(0, 100), _span_entry(100, 50), _span_entry(200, 10)
    spans = coalesce([a, b, c], gap=64)
    assert len(spans) == 1          # 150->200 gap of 50 <= 64: all merge
    assert spans[0].offset == 0 and spans[0].length == 210
    assert spans[0].entries == (a, b, c)


def test_coalesce_splits_on_large_gap():
    a, b = _span_entry(0, 10), _span_entry(1_000_000, 10)
    spans = coalesce([a, b], gap=64)
    assert len(spans) == 2


def test_coalesce_respects_max_span():
    a, b = _span_entry(0, 100), _span_entry(100, 100)
    spans = coalesce([a, b], gap=64, max_span=150)
    assert len(spans) == 2


def test_coalesce_sorts_by_offset():
    a, b = _span_entry(500, 10), _span_entry(0, 10)
    spans = coalesce([a, b], gap=1000)
    assert spans[0].entries[0].zip_offset == 0


# --- decode_entry ---

def test_decode_entry_inflates_and_verifies():
    entry, comp = _entry("x", b"hello world", 0)
    assert decode_entry(entry, comp) == b"hello world"


def test_decode_entry_stored_passthrough():
    entry, comp = _entry("x", b"hello", 0, method=0)
    assert decode_entry(entry, comp) == b"hello"


def test_decode_entry_rejects_bad_hash():
    entry, _ = _entry("x", b"hello", 0)
    other = _deflate(b"HELLO")
    good = ManifestEntry(path=entry.path, sha256=entry.sha256, size=5,
                         zip_offset=0, zip_csize=len(other), zip_method=8)
    with pytest.raises(ValueError):
        decode_entry(good, other)


def test_decode_entry_rejects_unknown_method():
    entry = ManifestEntry(path="x", sha256="00" * 32, size=1,
                          zip_offset=0, zip_csize=1, zip_method=12)
    with pytest.raises(ValueError):
        decode_entry(entry, b"x")


# --- fetch_plan ---

def _fake_zip(entries_data):
    """entries_data: [(path, bytes)] -> (blob, [ManifestEntry]) with real offsets."""
    blob = b""
    entries = []
    for path, data in entries_data:
        entry, comp = _entry(path, data, offset=len(blob))
        blob += comp
        entries.append(entry)
    return blob, entries


def test_fetch_plan_stages_verified_files(tmp_path):
    blob, entries = _fake_zip([("a.txt", b"AAA"), ("sub/b.bin", b"B" * 100)])
    plan = UpdatePlan(fetch=tuple(entries), delete=(),
                      download_bytes=sum(e.zip_csize for e in entries))
    seen = []
    fetch_plan(plan, "https://dl/z.zip", tmp_path / "st",
               http=_range_http(blob), progress=seen.append)
    assert (tmp_path / "st" / "a.txt").read_bytes() == b"AAA"
    assert (tmp_path / "st" / "sub" / "b.bin").read_bytes() == b"B" * 100
    assert seen and seen[-1] == 1.0


def test_fetch_plan_uses_coalesced_ranges(tmp_path):
    blob, entries = _fake_zip([("a", b"A" * 10), ("b", b"B" * 10)])
    log = []
    plan = UpdatePlan(fetch=tuple(entries), delete=(),
                      download_bytes=sum(e.zip_csize for e in entries))
    fetch_plan(plan, "https://dl/z.zip", tmp_path / "st",
               http=_range_http(blob, log=log))
    assert len(log) == 1 and log[0].startswith("bytes=")


def test_fetch_plan_raises_when_range_ignored(tmp_path):
    blob, entries = _fake_zip([("a", b"AAA")])
    plan = UpdatePlan(fetch=tuple(entries), delete=(), download_bytes=1)
    with pytest.raises(RangeUnsupported):
        fetch_plan(plan, "https://dl/z.zip", tmp_path / "st",
                   http=_range_http(blob, honor_range=False))


# --- fetch_full_zip ---

def _real_zip(tmp_path, files):
    import zipfile
    zp = tmp_path / "full.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
    return zp.read_bytes()


def test_fetch_full_zip_extracts_planned_files(tmp_path):
    blob = _real_zip(tmp_path, {"a.txt": b"AAA", "b.txt": b"BBB"})
    sha = hashlib.sha256(blob).hexdigest()
    entry = ManifestEntry(path="a.txt",
                          sha256=hashlib.sha256(b"AAA").hexdigest(), size=3,
                          zip_offset=0, zip_csize=0, zip_method=8)
    plan = UpdatePlan(fetch=(entry,), delete=(), download_bytes=0)
    fetch_full_zip(plan, "https://dl/z.zip", sha, tmp_path / "st",
                   http=_range_http(blob, honor_range=False))
    assert (tmp_path / "st" / "a.txt").read_bytes() == b"AAA"
    assert not (tmp_path / "st" / "b.txt").exists()   # only planned files
    assert not (tmp_path / "st" / "_full.zip").exists()


def test_fetch_full_zip_rejects_bad_zip_hash(tmp_path):
    blob = _real_zip(tmp_path, {"a.txt": b"AAA"})
    entry = ManifestEntry(path="a.txt",
                          sha256=hashlib.sha256(b"AAA").hexdigest(), size=3,
                          zip_offset=0, zip_csize=0, zip_method=8)
    plan = UpdatePlan(fetch=(entry,), delete=(), download_bytes=0)
    with pytest.raises(ValueError):
        fetch_full_zip(plan, "https://dl/z.zip", "0" * 64, tmp_path / "st",
                       http=_range_http(blob, honor_range=False))


def test_free_disk_ok(tmp_path):
    assert free_disk_ok(tmp_path, 0) is True
    assert free_disk_ok(tmp_path, 1 << 62) is False
