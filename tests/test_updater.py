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
