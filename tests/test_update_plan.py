# tests/test_update_plan.py
import json

import pytest

from sm64_events.core.update_plan import (BOOTSTRAP_ASSET, INSTALLED_MANIFEST,
                                          MANIFEST_ASSET, ZIP_ASSET, Manifest,
                                          ManifestEntry, file_sha256,
                                          manifest_to_json, parse_manifest)


def _entry(**kw):
    base = dict(path="a/b.txt", sha256="ab" * 32, size=3,
                zip_offset=100, zip_csize=5, zip_method=8)
    base.update(kw)
    return base


def _doc(*entries):
    return json.dumps({"schema": 1, "version": "1.4.0",
                       "files": [_entry(**e) for e in entries]})


def test_asset_names_are_the_registry():
    assert ZIP_ASSET == "SM64Trainer-full.zip"
    assert MANIFEST_ASSET == "manifest.json"
    assert BOOTSTRAP_ASSET == "SM64Trainer.exe"   # load-bearing: old updaters
    assert INSTALLED_MANIFEST == "installed_manifest.json"


def test_parse_manifest_happy_path():
    m = parse_manifest(_doc({}))
    assert m.version == "1.4.0"
    assert m.files == (ManifestEntry(path="a/b.txt", sha256="ab" * 32, size=3,
                                     zip_offset=100, zip_csize=5, zip_method=8),)


def test_parse_manifest_lowercases_sha():
    m = parse_manifest(_doc({"sha256": "AB" * 32}))
    assert m.files[0].sha256 == "ab" * 32


def test_parse_manifest_rejects_bad_schema():
    with pytest.raises(ValueError):
        parse_manifest(json.dumps({"schema": 99, "version": "1", "files": []}))


def test_parse_manifest_rejects_non_json():
    with pytest.raises(ValueError):
        parse_manifest("not json {")


@pytest.mark.parametrize("bad", ["../x", "/abs", "a\\b", "a//b", "", "a/../b"])
def test_parse_manifest_rejects_unsafe_paths(bad):
    with pytest.raises(ValueError):
        parse_manifest(_doc({"path": bad}))


def test_parse_manifest_rejects_missing_field():
    doc = json.loads(_doc({}))
    del doc["files"][0]["sha256"]
    with pytest.raises(ValueError):
        parse_manifest(json.dumps(doc))


def test_manifest_json_round_trips():
    m = parse_manifest(_doc({}, {"path": "c.dll", "zip_offset": 500}))
    assert parse_manifest(manifest_to_json(m)) == m


def test_file_sha256_streams(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    import hashlib
    assert file_sha256(p) == hashlib.sha256(b"hello").hexdigest()
