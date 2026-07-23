import hashlib
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from make_manifest import build_zip, entry_spans, make_manifest  # noqa: E402

from sm64_events.core.update_plan import parse_manifest  # noqa: E402


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for rel, content in files.items():
        p = root.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return root


FILES = {
    "SM64Trainer.exe": b"exe-bytes" * 1000,
    "_internal/python312.dll": b"dll" * 5000,
    "_internal/sub/data.json": b'{"k": 1}',
}


def test_build_zip_is_deterministic(tmp_path):
    src = _tree(tmp_path / "src", FILES)
    build_zip(src, tmp_path / "a.zip")
    build_zip(src, tmp_path / "b.zip")
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()


def test_manifest_lists_every_file_with_correct_hashes(tmp_path):
    src = _tree(tmp_path / "src", FILES)
    zp = tmp_path / "full.zip"
    build_zip(src, zp)
    m = parse_manifest(make_manifest(zp, "1.4.0"))
    assert m.version == "1.4.0"
    assert {e.path for e in m.files} == set(FILES)
    for entry in m.files:
        assert entry.sha256 == hashlib.sha256(FILES[entry.path]).hexdigest()
        assert entry.size == len(FILES[entry.path])


def test_manifest_offsets_slice_and_inflate_back_to_content(tmp_path):
    """THE load-bearing property: for every entry, blob[offset:offset+csize]
    must decode (raw deflate / stored) to exactly the original file — this is
    the byte range the updater Range-fetches in production."""
    src = _tree(tmp_path / "src", FILES)
    zp = tmp_path / "full.zip"
    build_zip(src, zp)
    blob = zp.read_bytes()
    m = parse_manifest(make_manifest(zp, "1.4.0"))
    for entry in m.files:
        comp = blob[entry.zip_offset:entry.zip_offset + entry.zip_csize]
        if entry.zip_method == 8:
            inflater = zlib.decompressobj(-15)
            data = inflater.decompress(comp) + inflater.flush()
        else:
            assert entry.zip_method == 0
            data = comp
        assert data == FILES[entry.path], entry.path


def test_entry_spans_match_manifest(tmp_path):
    src = _tree(tmp_path / "src", FILES)
    zp = tmp_path / "full.zip"
    build_zip(src, zp)
    spans = entry_spans(zp)
    m = parse_manifest(make_manifest(zp, "1.0.0"))
    for entry in m.files:
        off, csize, method = spans[entry.path]
        assert (off, csize, method) == (entry.zip_offset, entry.zip_csize,
                                        entry.zip_method)
