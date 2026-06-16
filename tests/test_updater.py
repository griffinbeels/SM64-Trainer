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
