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


# --- review findings (2026-07-23 wave-0 review) ---

@pytest.mark.parametrize("bad", ["C:/x", "C:", "C:evil.dll",
                                 "good.dll:hidden.exe", "x\x00y", "a<b",
                                 "a?b", "a*b"])
def test_parse_manifest_rejects_windows_colon_and_control_chars(bad):
    """Colon paths are drive-relative on Windows (root.joinpath('C:', ...)
    ESCAPES the install root) or NTFS alternate data streams; control chars
    are never legitimate. Review finding W0-I-1."""
    with pytest.raises(ValueError):
        parse_manifest(_doc({"path": bad}))


@pytest.mark.parametrize("reserved", [
    "installed_manifest.json", "update_journal.json",
    ".update_backup/x.dll", ".update_staging/y.dll"])
def test_parse_manifest_rejects_updater_control_paths(reserved):
    """A manifest naming the updater's own state files could rename the
    crash-recovery journal away mid-swap or nest the backup tree into
    itself. Review finding W0-I-2."""
    with pytest.raises(ValueError):
        parse_manifest(_doc({"path": reserved}))


def test_parse_manifest_rejects_duplicate_paths():
    with pytest.raises(ValueError):
        parse_manifest(_doc({"path": "dup.dll"}, {"path": "dup.dll"}))


@pytest.mark.parametrize("field,value", [
    ("size", -5), ("zip_offset", -1), ("zip_csize", -3),
    ("zip_method", 12), ("sha256", "xyz"), ("sha256", "AB" * 31)])
def test_parse_manifest_rejects_invalid_field_values(field, value):
    """Negative sizes understate download totals and produce malformed Range
    headers; sha256 must be 64 hex chars. Review finding W0-M-5."""
    with pytest.raises(ValueError):
        parse_manifest(_doc({field: value}))


def test_manifest_json_round_trips():
    m = parse_manifest(_doc({}, {"path": "c.dll", "zip_offset": 500}))
    assert parse_manifest(manifest_to_json(m)) == m


def test_file_sha256_streams(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    import hashlib
    assert file_sha256(p) == hashlib.sha256(b"hello").hexdigest()


from sm64_events.core.update_plan import UpdatePlan, build_plan  # noqa: E402


def _mk(path, sha, size=3, off=0, csize=10):
    return ManifestEntry(path=path, sha256=sha, size=size,
                         zip_offset=off, zip_csize=csize, zip_method=8)


def _tree(tmp_path, files):
    for rel, content in files.items():
        p = tmp_path.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return tmp_path


def test_build_plan_fresh_install_fetches_everything(tmp_path):
    remote = Manifest("2.0.0", (_mk("a.txt", "aa" * 32, csize=7),
                                _mk("b/c.dll", "bb" * 32, csize=9)))
    plan = build_plan(remote, None, tmp_path)
    assert [e.path for e in plan.fetch] == ["a.txt", "b/c.dll"]
    assert plan.delete == ()
    assert plan.download_bytes == 16


def test_build_plan_unchanged_files_skipped(tmp_path):
    import hashlib
    content = b"abc"
    sha = hashlib.sha256(content).hexdigest()
    _tree(tmp_path, {"a.txt": content})
    entry = _mk("a.txt", sha, size=3)
    remote = Manifest("2.0.0", (entry,))
    installed = Manifest("1.0.0", (entry,))
    plan = build_plan(remote, installed, tmp_path)
    assert plan.fetch == () and plan.download_bytes == 0


def test_build_plan_hash_change_fetches(tmp_path):
    _tree(tmp_path, {"a.txt": b"abc"})
    old = _mk("a.txt", "aa" * 32, size=3)
    new = _mk("a.txt", "bb" * 32, size=3)
    plan = build_plan(Manifest("2", (new,)), Manifest("1", (old,)), tmp_path)
    assert [e.path for e in plan.fetch] == ["a.txt"]


def test_build_plan_missing_or_wrong_size_refetches(tmp_path):
    import hashlib
    sha = hashlib.sha256(b"abc").hexdigest()
    entry = _mk("a.txt", sha, size=3)
    installed = Manifest("1", (entry,))
    # missing on disk
    plan = build_plan(Manifest("2", (entry,)), installed, tmp_path)
    assert [e.path for e in plan.fetch] == ["a.txt"]
    # wrong size on disk (truncated)
    _tree(tmp_path, {"a.txt": b"ab"})
    plan = build_plan(Manifest("2", (entry,)), installed, tmp_path)
    assert [e.path for e in plan.fetch] == ["a.txt"]


def test_build_plan_verify_local_catches_silent_corruption(tmp_path):
    import hashlib
    sha = hashlib.sha256(b"abc").hexdigest()
    _tree(tmp_path, {"a.txt": b"abX"})   # same size, different bytes
    entry = _mk("a.txt", sha, size=3)
    installed = Manifest("1", (entry,))
    fast = build_plan(Manifest("2", (entry,)), installed, tmp_path)
    assert fast.fetch == ()              # fast path trusts size+record
    slow = build_plan(Manifest("2", (entry,)), installed, tmp_path,
                      verify_local=True)
    assert [e.path for e in slow.fetch] == ["a.txt"]   # self-heal


def test_build_plan_deletes_removed_files(tmp_path):
    import hashlib
    sha = hashlib.sha256(b"abc").hexdigest()
    _tree(tmp_path, {"gone.dll": b"abc", "kept.txt": b"abc"})
    kept = _mk("kept.txt", sha, size=3)
    gone = _mk("gone.dll", sha, size=3)
    plan = build_plan(Manifest("2", (kept,)), Manifest("1", (kept, gone)),
                      tmp_path)
    assert plan.delete == ("gone.dll",)


def test_build_plan_rejects_empty_remote_manifest(tmp_path):
    """A self-consistent-but-empty manifest (release-side build bug: an
    empty dir got zipped) would otherwise plan deleting the ENTIRE install.
    Review finding W0-M-4."""
    installed = Manifest("1", (_mk("a.txt", "aa" * 32),))
    with pytest.raises(ValueError):
        build_plan(Manifest("2", ()), installed, tmp_path)
