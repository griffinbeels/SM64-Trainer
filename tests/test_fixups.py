"""Setup's PJ64 Fixups install (core/fixups.py): the tested DLL or nothing."""
import hashlib
import io
import zipfile

import pytest

from sm64_events.core.fixups import BACKUP, RSP_DLL, FixupError, Fixups
from sm64_events.core.setup_profile import load_baseline

DLL = b"the tested fixups dll"


def _zip(dll=DLL):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        bundle.writestr("RSP.dll", dll)
    return data.getvalue()


def _baseline(archive, dll=DLL):
    return {"fixups": {"version": "9.9", "url": "https://example.invalid/RSP.zip",
                       "zip_sha256": hashlib.sha256(archive).hexdigest()},
            "items": [{"id": "rsp_plugin", "sha256": hashlib.sha256(dll).hexdigest()}]}


def _fixups(tmp_path, archive=None, served=None, calls=None):
    archive = _zip() if archive is None else archive

    def download(url):
        if calls is not None:
            calls.append(url)
        if isinstance(served, Exception):
            raise served
        return archive if served is None else served
    return Fixups(_baseline(archive), tmp_path / "cache", download=download)


def test_install_replaces_the_old_plugin_keeps_it_once_and_downloads_once(tmp_path):
    plugin = tmp_path / "Plugin"
    plugin.mkdir()
    (plugin / RSP_DLL).write_bytes(b"stock rsp")
    calls = []
    fixups = _fixups(tmp_path, calls=calls)
    assert fixups.install(plugin) == "installed"
    assert (plugin / RSP_DLL).read_bytes() == DLL
    assert (plugin / BACKUP).read_bytes() == b"stock rsp"
    assert fixups.install(plugin) == "present" and len(calls) == 1
    # A second Project64 folder uses the kept copy; its own old file is kept too.
    other = tmp_path / "Other" / "Plugin"
    assert fixups.install(other) == "installed" and len(calls) == 1
    assert not (other / BACKUP).exists()
    assert fixups.status() == {"version": "9.9", "error": None, "reason": None}
    # The kept download is a real file: the administrator helper copies from it.
    assert fixups.cached().read_bytes() == DLL and len(calls) == 1
    # Reinstalling over a changed file never overwrites the first kept copy.
    (plugin / RSP_DLL).write_bytes(b"something else")
    fixups.install(plugin)
    assert (plugin / BACKUP).read_bytes() == b"stock rsp"


@pytest.mark.parametrize("served, words", [
    (OSError("offline"), "could not download"),
    (_zip(b"another dll"), "not the file we tested"),
    (b"not a zip", "not the file we tested"),
])
def test_a_failed_or_different_download_changes_nothing_and_says_why(tmp_path, served, words):
    plugin = tmp_path / "Plugin"
    plugin.mkdir()
    (plugin / RSP_DLL).write_bytes(b"stock rsp")
    fixups = _fixups(tmp_path, served=served)
    with pytest.raises(FixupError, match=words):
        fixups.install(plugin)
    assert (plugin / RSP_DLL).read_bytes() == b"stock rsp"
    assert sorted(path.name for path in plugin.iterdir()) == [RSP_DLL]
    assert words in fixups.status()["error"] and fixups.status()["reason"] == "failed"


def test_a_refused_write_is_named_so_setup_can_ask_for_permission(tmp_path, monkeypatch):
    from sm64_events.core import fixups as module
    plugin = tmp_path / "Plugin"
    plugin.mkdir()
    fixups = _fixups(tmp_path)

    def denied(source, target):
        raise PermissionError(13, "Access is denied", str(target))
    monkeypatch.setattr(module.os, "replace", denied)
    with pytest.raises(FixupError, match="Windows blocked"):
        fixups.install(plugin)
    assert fixups.status()["reason"] == "access_denied"
    assert list(plugin.iterdir()) == []       # the scratch copy is gone too


def test_an_archive_whose_dll_is_not_the_tested_one_is_refused(tmp_path):
    archive = _zip(b"another dll")
    fixups = Fixups(_baseline(archive), tmp_path / "cache", download=lambda url: archive)
    with pytest.raises(FixupError, match="not the file we tested"):
        fixups.install(tmp_path / "Plugin")
    assert not (tmp_path / "Plugin" / RSP_DLL).exists()


def test_the_shipped_baseline_names_one_https_github_release():
    baseline = load_baseline()
    release = baseline["fixups"]
    assert release["url"].startswith("https://github.com/aglab2/PJ64Fixups/releases/download/")
    assert len(release["zip_sha256"]) == 64
    Fixups(baseline, cache_dir=".")   # the rsp_plugin row carries the DLL's hash
