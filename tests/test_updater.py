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


# --- new-format release fixtures (real zip + manifest, network-free) ---

import hashlib as _hashlib
import io
import json as _json
import sys as _sys
from pathlib import Path

_tools = str(Path(__file__).resolve().parents[1] / "tools")
if _tools not in _sys.path:
    _sys.path.insert(0, _tools)
from make_manifest import build_zip, make_manifest  # noqa: E402

from sm64_events.core.update_plan import (INSTALLED_MANIFEST,  # noqa: E402
                                          MANIFEST_ASSET, ZIP_ASSET)
from sm64_events.core.updater import UpdateInfo, check_for_update  # noqa: E402


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, headers: dict | None = None):
        super().__init__(data)
        self.status = 200
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


LATEST = "https://api.github.com/repos/griffinbeels/SM64-Trainer/releases/latest"

FULL_ASSETS = {
    ZIP_ASSET: "https://dl/full.zip",
    ZIP_ASSET + ".sha256": "https://dl/full.sha",
    MANIFEST_ASSET: "https://dl/manifest.json",
    MANIFEST_ASSET + ".sha256": "https://dl/manifest.sha",
}


def _release_json(tag, assets):
    return _json.dumps({
        "tag_name": tag, "body": "notes here",
        "html_url": f"https://github.com/x/y/releases/tag/{tag}",
        "assets": [{"name": n, "browser_download_url": u}
                   for n, u in assets.items()]}).encode()


def _sha_line(data: bytes, name: str) -> bytes:
    return (_hashlib.sha256(data).hexdigest() + "  " + name).encode()


def _fake_release(tmp_path, tag: str, files: dict[str, bytes]) -> dict:
    """Build a real zip+manifest for `files` and return an http routes dict."""
    src = tmp_path / f"src-{tag}"
    for rel, content in files.items():
        p = src.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    zp = tmp_path / f"{tag}.zip"
    build_zip(src, zp)
    manifest = make_manifest(zp, tag.lstrip("v")).encode()
    blob = zp.read_bytes()
    return {
        LATEST: _release_json(tag, FULL_ASSETS),
        "https://dl/full.zip": blob,
        "https://dl/full.sha": _sha_line(blob, ZIP_ASSET),
        "https://dl/manifest.json": manifest,
        "https://dl/manifest.sha": _sha_line(manifest, MANIFEST_ASSET),
    }


# --- check_for_update ---

def test_check_returns_info_when_all_assets_present(tmp_path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"X"})
    info = check_for_update("1.0.0", http=_fake_http(routes))
    assert isinstance(info, UpdateInfo)
    assert info.version == "2.0.0"
    assert info.zip_url == "https://dl/full.zip"
    assert info.zip_sha_url == "https://dl/full.sha"
    assert info.manifest_url == "https://dl/manifest.json"
    assert info.manifest_sha_url == "https://dl/manifest.sha"


def test_check_none_when_missing_manifest_assets():
    partial = {k: v for k, v in FULL_ASSETS.items() if k != MANIFEST_ASSET}
    http = _fake_http({LATEST: _release_json("v2.0.0", partial)})
    assert check_for_update("1.0.0", http=http) is None


def test_check_none_when_missing_sha_assets():
    partial = {k: v for k, v in FULL_ASSETS.items()
               if k != ZIP_ASSET + ".sha256"}
    http = _fake_http({LATEST: _release_json("v2.0.0", partial)})
    assert check_for_update("1.0.0", http=http) is None


def test_check_none_when_not_newer():
    http = _fake_http({LATEST: _release_json("v1.0.0", FULL_ASSETS)})
    assert check_for_update("1.0.0", http=http) is None


def test_check_none_on_http_error():
    def boom(req):
        raise OSError("network down")
    assert check_for_update("1.0.0", http=boom) is None


# --- exe_dir_writable ---

from sm64_events.core.updater import exe_dir_writable  # noqa: E402


def test_exe_dir_writable(tmp_path):
    assert exe_dir_writable(tmp_path) is True
    assert exe_dir_writable(tmp_path / "does-not-exist") is False


# --- UpdateService ---

from sm64_events.core.updater import UpdateService  # noqa: E402


def _svc(tmp_path, http, *, frozen=True):
    root = tmp_path / "app"
    root.mkdir(parents=True, exist_ok=True)
    exe = root / "SM64Trainer.exe"
    if not exe.exists():
        exe.write_bytes(b"OLD")
    return UpdateService(current_version="1.0.0", http=http, exe_path=exe,
                         state_path=tmp_path / "update_state.json",
                         frozen=frozen)


def test_status_inert_from_source(tmp_path):
    svc = _svc(tmp_path, _fake_http({}), frozen=False)
    st = svc.status()
    assert st["frozen"] is False
    assert st["update_available"] is False


def test_status_reports_available_with_download_bytes(tmp_path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    svc = _svc(tmp_path, _fake_http(routes))
    st = svc.status()
    assert st["update_available"] is True
    assert st["latest"] == "2.0.0"
    assert st["download_bytes"] > 0
    assert st["writable"] is True          # tmp dir is writable


def test_status_manifest_tamper_means_no_update(tmp_path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    routes["https://dl/manifest.sha"] = ("0" * 64 + "  x").encode()
    svc = _svc(tmp_path, _fake_http(routes))
    assert svc.status()["update_available"] is False


def test_skip_persists_and_round_trips(tmp_path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    svc = _svc(tmp_path, _fake_http(routes))
    svc.skip("2.0.0")
    assert svc.status()["skipped"] == "2.0.0"


def test_begin_apply_errors_when_no_update(tmp_path):
    http = _fake_http({LATEST: _release_json("v1.0.0", FULL_ASSETS)})
    svc = _svc(tmp_path, http)
    assert svc.begin_apply(lambda: None)["state"] == "error"
