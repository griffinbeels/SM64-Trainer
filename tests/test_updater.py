import re

from sm64_events.core.version import __version__


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__


from sm64_events.core.updater import is_newer, parse_version


def test_parse_version_strips_v_and_splits():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.3") == (1, 2, 3)


def test_parse_version_stops_at_non_numeric_suffix():
    assert parse_version("1.2.3-beta") == (1, 2, 3)


def test_is_newer_compares_numerically():
    assert is_newer("1.2.10", "1.2.9") is True   # not lexicographic
    assert is_newer("1.0.0", "0.9.9") is True
    assert is_newer("1.0.0", "1.0.0") is False
    assert is_newer("0.9.9", "1.0.0") is False


import io
import json as _json

from sm64_events.core.updater import UpdateInfo, check_for_update


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, headers: dict | None = None):
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _fake_http(routes: dict):
    """routes: url -> bytes. Raises for an unmapped url."""
    def opener(req):
        url = req.full_url if hasattr(req, "full_url") else req
        if url not in routes:
            raise OSError(f"unmapped url {url}")
        body = routes[url]
        return _Resp(body, {"Content-Length": str(len(body))})
    return opener


def _release_json(tag, assets):
    return _json.dumps({
        "tag_name": tag, "body": "notes here",
        "html_url": f"https://github.com/x/y/releases/tag/{tag}",
        "assets": [{"name": n, "browser_download_url": u}
                   for n, u in assets.items()],
    }).encode()


LATEST = "https://api.github.com/repos/griffinbeels/SM64-Trainer/releases/latest"


def test_check_returns_info_when_newer_with_asset():
    http = _fake_http({LATEST: _release_json("v2.0.0", {
        "sm64_tracker.exe": "https://dl/exe",
        "sm64_tracker.exe.sha256": "https://dl/sha",
    })})
    info = check_for_update("1.0.0", http=http)
    assert isinstance(info, UpdateInfo)
    assert info.version == "2.0.0"
    assert info.asset_url == "https://dl/exe"
    assert info.sha256_url == "https://dl/sha"


def test_check_none_when_not_newer():
    http = _fake_http({LATEST: _release_json("v1.0.0", {
        "sm64_tracker.exe": "https://dl/exe"})})
    assert check_for_update("1.0.0", http=http) is None


def test_check_none_when_no_exe_asset():
    http = _fake_http({LATEST: _release_json("v2.0.0", {"notes.txt": "u"})})
    assert check_for_update("1.0.0", http=http) is None


def test_check_none_on_http_error():
    def boom(req):
        raise OSError("network down")
    assert check_for_update("1.0.0", http=boom) is None


import hashlib as _hashlib

import pytest

from sm64_events.core.updater import (apply_update, cleanup_old,
                                      download_and_stage, exe_dir_writable)


def test_download_stage_verifies_good_hash(tmp_path):
    payload = b"new exe bytes"
    digest = _hashlib.sha256(payload).hexdigest()
    info = UpdateInfo("2.0.0", "n", "h", "https://dl/exe", "https://dl/sha")
    http = _fake_http({"https://dl/exe": payload,
                       "https://dl/sha": (digest + "  sm64_tracker.exe").encode()})
    seen = []
    staged = download_and_stage(info, tmp_path, http=http,
                                progress=seen.append)
    assert staged.read_bytes() == payload
    assert staged.name == "sm64_tracker.exe.new"
    assert seen and seen[-1] == 1.0


def test_download_stage_rejects_bad_hash(tmp_path):
    info = UpdateInfo("2.0.0", "n", "h", "https://dl/exe", "https://dl/sha")
    http = _fake_http({"https://dl/exe": b"corrupt",
                       "https://dl/sha": (("0" * 64) + "  x").encode()})
    with pytest.raises(ValueError):
        download_and_stage(info, tmp_path, http=http)
    assert not (tmp_path / "sm64_tracker.exe.new").exists()


def test_apply_update_swaps_running_exe(tmp_path):
    current = tmp_path / "sm64_tracker.exe"
    current.write_text("OLD")
    staged = tmp_path / "sm64_tracker.exe.new"
    staged.write_text("NEW")
    apply_update(staged, current)
    assert current.read_text() == "NEW"
    assert (tmp_path / "sm64_tracker.exe.old").read_text() == "OLD"


def test_cleanup_old_removes_old_files(tmp_path):
    (tmp_path / "sm64_tracker.exe.old").write_text("x")
    cleanup_old(tmp_path)
    assert not (tmp_path / "sm64_tracker.exe.old").exists()


def test_exe_dir_writable(tmp_path):
    assert exe_dir_writable(tmp_path) is True
    assert exe_dir_writable(tmp_path / "does-not-exist") is False


from sm64_events.core.updater import UpdateService


def _svc(tmp_path, http, *, frozen=True):
    exe = tmp_path / "sm64_tracker.exe"
    exe.write_text("OLD")
    return UpdateService(current_version="1.0.0", http=http, exe_path=exe,
                         state_path=tmp_path / "update_state.json",
                         frozen=frozen)


def test_status_inert_from_source(tmp_path):
    svc = _svc(tmp_path, _fake_http({}), frozen=False)
    st = svc.status()
    assert st["frozen"] is False
    assert st["update_available"] is False


def test_status_reports_available(tmp_path):
    http = _fake_http({LATEST: _release_json("v2.0.0", {
        "sm64_tracker.exe": "https://dl/exe",
        "sm64_tracker.exe.sha256": "https://dl/sha"})})
    svc = _svc(tmp_path, http)
    st = svc.status()
    assert st["update_available"] is True
    assert st["latest"] == "2.0.0"
    assert st["writable"] is True          # tmp dir is writable


def test_skip_persists_and_round_trips(tmp_path):
    http = _fake_http({LATEST: _release_json("v2.0.0", {
        "sm64_tracker.exe": "https://dl/exe"})})
    svc = _svc(tmp_path, http)
    svc.skip("2.0.0")
    assert svc.status()["skipped"] == "2.0.0"


def test_run_apply_swaps_and_calls_on_success(tmp_path):
    payload = b"NEWEXE"
    digest = _hashlib.sha256(payload).hexdigest()
    http = _fake_http({
        LATEST: _release_json("v2.0.0", {
            "sm64_tracker.exe": "https://dl/exe",
            "sm64_tracker.exe.sha256": "https://dl/sha"}),
        "https://dl/exe": payload,
        "https://dl/sha": (digest + "  sm64_tracker.exe").encode()})
    svc = _svc(tmp_path, http)
    info = svc._check(force=True)
    restarted = []
    svc._run_apply(info, lambda: restarted.append(True))
    assert (tmp_path / "sm64_tracker.exe").read_bytes() == payload
    assert restarted == [True]


def test_begin_apply_errors_when_no_update(tmp_path):
    http = _fake_http({LATEST: _release_json("v1.0.0", {
        "sm64_tracker.exe": "https://dl/exe"})})
    svc = _svc(tmp_path, http)
    assert svc.begin_apply(lambda: None)["state"] == "error"


def test_check_none_when_no_sha256_asset():
    http = _fake_http({LATEST: _release_json("v2.0.0", {
        "sm64_tracker.exe": "https://dl/exe"})})   # exe but NO .sha256
    assert check_for_update("1.0.0", http=http) is None


import os as _os

def test_apply_update_restores_backup_on_persistent_failure(tmp_path, monkeypatch):
    current = tmp_path / "sm64_tracker.exe"
    current.write_text("OLD")
    staged = tmp_path / "sm64_tracker.exe.new"
    staged.write_text("NEW")
    real = _os.replace
    def flaky(src, dst):
        if str(src) == str(staged):          # fail only the staged->current move
            raise PermissionError("locked")
        return real(src, dst)
    monkeypatch.setattr("sm64_events.core.updater.os.replace", flaky)
    import pytest
    with pytest.raises(PermissionError):
        apply_update(staged, current, retries=2, sleep=lambda s: None)
    assert current.read_text() == "OLD"      # restored, not bricked


import threading as _threading

def test_begin_apply_happy_path_swaps_and_calls_back(tmp_path):
    payload = b"NEWEXE"
    digest = _hashlib.sha256(payload).hexdigest()
    http = _fake_http({
        LATEST: _release_json("v2.0.0", {
            "sm64_tracker.exe": "https://dl/exe",
            "sm64_tracker.exe.sha256": "https://dl/sha"}),
        "https://dl/exe": payload,
        "https://dl/sha": (digest + "  sm64_tracker.exe").encode()})
    svc = _svc(tmp_path, http)
    done = _threading.Event()
    assert svc.begin_apply(done.set)["state"] == "downloading"
    assert done.wait(timeout=5)
    assert (tmp_path / "sm64_tracker.exe").read_bytes() == payload
