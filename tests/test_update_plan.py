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


# -- the move from three natives to one graphics plugin (2026-11-07) ----------

#: The rule every app from 1.10.0-beta.22 to the one-plugin release runs on an
#: offered release before it plans anything (update_plan.validate_release_manifest
#: at commit d014991a): those apps cannot change, so this copy is the contract.
SHIPPED_APPS_REQUIRE = {"SM64Trainer.exe", "_internal/ffmpeg.exe", "_internal/sm64_events/ui/index.html",
                        *(f"_internal/sm64_events/data/plugin/{name}" for name in
                          ("sm64_trainer_gfx.dll", "GLideN64_SM64Trainer.dll", "SM64GpuEncoderV1.dll"))}


def _app(tmp_path, files, version="x"):
    """An app folder and its manifest, every entry hashed from its bytes."""
    import hashlib
    _tree(tmp_path, files)
    return Manifest(version, tuple(_mk(path, hashlib.sha256(data).hexdigest(), size=len(data))
                                   for path, data in files.items()))


def _one_plugin_release():
    from sm64_events.core.update_plan import (BUNDLED_NATIVES, NATIVE_FOLDER, RETIRED_NATIVE_TEXT,
                                              RETIRED_NATIVES)
    return {"SM64Trainer.exe": b"EXE-NEW", "_internal/python314.dll": b"PY", "_internal/ffmpeg.exe": b"FF",
            "_internal/sm64_events/ui/index.html": b"UI",
            **{f"{NATIVE_FOLDER}/{name}": b"MZ new " + name.encode() for name in BUNDLED_NATIVES},
            **{f"{NATIVE_FOLDER}/{name}": RETIRED_NATIVE_TEXT for name in RETIRED_NATIVES}}


def test_an_app_with_three_natives_accepts_and_plans_the_one_plugin_release(tmp_path):
    import re
    from sm64_events.core.update_plan import NATIVE_FOLDER, RETIRED_NATIVE_TEXT, validate_release_manifest
    old = {"SM64Trainer.exe": b"EXE-OLD", "_internal/python314.dll": b"PY", "_internal/ffmpeg.exe": b"FF",
           "_internal/sm64_events/ui/index.html": b"UI",
           **{f"{NATIVE_FOLDER}/{name}": b"MZ old " + name.encode()
              for name in ("sm64_trainer_gfx.dll", "GLideN64_SM64Trainer.dll", "SM64GpuEncoderV1.dll")}}
    installed = _app(tmp_path / "installed", old)
    remote = _app(tmp_path / "release", _one_plugin_release(), version="2.0.0")
    shipped = {entry.path for entry in remote.files if entry.size > 0}
    # What an installed older app checks, and what this build's own tooling checks.
    assert SHIPPED_APPS_REQUIRE <= shipped
    assert any(re.fullmatch(r"_internal/python[0-9]+[.]dll", path) for path in shipped)
    validate_release_manifest(remote, "2.0.0")
    plan = build_plan(remote, installed, tmp_path / "installed")
    fetched = {entry.path for entry in plan.fetch}
    # The real renderer DLL is replaced by the short placeholder: no program
    # stays under that name in the app folder, and nothing else is deleted.
    renderer = f"{NATIVE_FOLDER}/GLideN64_SM64Trainer.dll"
    assert {renderer, f"{NATIVE_FOLDER}/sm64_trainer_gfx.dll", "SM64Trainer.exe"} <= fetched
    assert next(entry for entry in remote.files if entry.path == renderer).size == len(RETIRED_NATIVE_TEXT)
    assert not RETIRED_NATIVE_TEXT.startswith(b"MZ")
    assert plan.delete == ()


def test_a_release_without_the_retired_placeholder_is_refused(tmp_path):
    from sm64_events.core.update_plan import RETIRED_NATIVES, validate_release_manifest
    files = {path: data for path, data in _one_plugin_release().items()
             if not path.endswith(RETIRED_NATIVES)}
    remote = _app(tmp_path, files, version="2.0.0")
    with pytest.raises(ValueError, match="essential"):
        validate_release_manifest(remote, "2.0.0")
