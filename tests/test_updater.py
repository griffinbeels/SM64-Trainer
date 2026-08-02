import re

from sm64_events.core.version import __version__


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__


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
RELEASES = ("https://api.github.com/repos/griffinbeels/SM64-Trainer"
            "/releases?per_page=100")

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


def test_check_strips_setup_header_from_notes(tmp_path):
    """Release bodies carry a first-time-setup section for the GitHub page;
    the in-app popup must show ONLY what follows the PATCH_NOTES_MARKER."""
    from sm64_events.core.update_plan import PATCH_NOTES_MARKER
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"X"})
    body = ("# First time here?\nDownload the installer, run it, done.\n\n"
            + PATCH_NOTES_MARKER + "\n\n- **New:** a thing\n- **Fix:** a bug")
    rel = _json.loads(routes[LATEST])
    rel["body"] = body
    routes[LATEST] = _json.dumps(rel).encode()
    info = check_for_update("1.0.0", http=_fake_http(routes))
    assert info.notes == "- **New:** a thing\n- **Fix:** a bug"
    assert "First time here" not in info.notes


def test_check_keeps_notes_without_marker(tmp_path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"X"})
    info = check_for_update("1.0.0", http=_fake_http(routes))
    assert info.notes == "notes here"      # marker-less body passes verbatim


def test_check_aggregates_every_missed_version_newest_first(tmp_path):
    """A user several releases behind must see EVERY skipped version's
    notes, not just the newest release's."""
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"X"})
    rel = _json.loads(routes[LATEST])
    rel["body"] = "newest notes"
    rel["published_at"] = "2026-07-23T00:00:00Z"
    routes[LATEST] = _json.dumps(rel).encode()
    routes[RELEASES] = _json.dumps([
        {"tag_name": "v2.0.0", "body": "newest notes",
         "published_at": "2026-07-23T00:00:00Z"},
        {"tag_name": "v1.5.0", "body": "middle notes",
         "published_at": "2026-07-10T00:00:00Z"},
        {"tag_name": "v1.0.0", "body": "already installed",
         "published_at": "2026-06-01T00:00:00Z"},
    ]).encode()
    info = check_for_update("1.0.0", http=_fake_http(routes))
    assert [(row.version, row.notes) for row in info.releases] == [
        ("2.0.0", "newest notes"), ("1.5.0", "middle notes")]
    assert info.releases[0].date == "2026-07-23"
    assert info.notes == "newest notes"     # single-version field unchanged


def test_check_still_offers_when_release_history_is_unavailable(tmp_path):
    """The list endpoint is best-effort — losing it must not lose the OFFER.
    _fake_http raises for any unmapped url, so RELEASES is already dead."""
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"X"})
    info = check_for_update("1.0.0", http=_fake_http(routes))
    assert info is not None
    assert [row.version for row in info.releases] == ["2.0.0"]
    assert info.notes == "notes here"


def test_check_ignores_history_newer_than_the_offered_release(tmp_path):
    """GitHub's 'latest' is the most RECENT publish, not the highest version.
    A backport published last would otherwise stack notes for a version this
    update does not install."""
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"X"})
    routes[RELEASES] = _json.dumps([
        {"tag_name": "v3.0.0", "body": "not installed by this update",
         "published_at": "2026-07-25T00:00:00Z"},
        {"tag_name": "v2.0.0", "body": "newest notes",
         "published_at": "2026-07-23T00:00:00Z"},
    ]).encode()
    info = check_for_update("1.0.0", http=_fake_http(routes))
    assert [row.version for row in info.releases] == ["2.0.0"]


def test_check_offered_release_always_heads_the_stack(tmp_path):
    """/releases/latest and /releases are cached separately by GitHub; right
    after a publish the list page can still lag and omit the tag /latest
    already serves. releases[0] must be the OFFERED release regardless —
    never a stale/missing feed row — so notes and releases[0].notes can
    never disagree."""
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"X"})
    rel = _json.loads(routes[LATEST])
    rel["body"] = "brand new notes"
    routes[LATEST] = _json.dumps(rel).encode()
    routes[RELEASES] = _json.dumps([   # omits v2.0.0 entirely
        {"tag_name": "v1.5.0", "body": "older notes",
         "published_at": "2026-07-10T00:00:00Z"},
    ]).encode()
    info = check_for_update("1.0.0", http=_fake_http(routes))
    assert info.releases[0].version == info.version
    assert info.releases[0].notes == info.notes


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


def test_status_carries_the_release_stack(tmp_path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    # offered (from /releases/latest) always heads the stack (check_for_update),
    # so give it the same body/date a real GitHub response would carry —
    # _fake_release's default LATEST fixture omits published_at entirely.
    rel = _json.loads(routes[LATEST])
    rel["body"] = "newest notes"
    rel["published_at"] = "2026-07-23T00:00:00Z"
    routes[LATEST] = _json.dumps(rel).encode()
    routes[RELEASES] = _json.dumps([
        {"tag_name": "v2.0.0", "body": "newest notes",
         "published_at": "2026-07-23T00:00:00Z"},
        {"tag_name": "v1.5.0", "body": "middle notes",
         "published_at": "2026-07-10T00:00:00Z"},
    ]).encode()
    st = _svc(tmp_path, _fake_http(routes)).status()
    # Pin only the keys this feature owns; the rest of the payload is other
    # features' and must stay unpinned.
    assert [row["version"] for row in st["releases"]] == ["2.0.0", "1.5.0"]
    assert st["releases"][0]["date"] == "2026-07-23"
    assert st["releases"][1]["notes"] == "middle notes"
    assert st["releases"][1]["date"] == "2026-07-10"


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


# --- every shipped request carries a timeout ---
# A socket that connects and then stalls has no deadline of its own, so it
# blocks the update worker forever — and a hang can never be retried, which
# makes this the floor the retry loop stands on. Injected openers (tests, the
# bootstrap) are exempt; what must not regress is the DEFAULT, since that is
# the one main.py gets.

import inspect  # noqa: E402
import urllib.request  # noqa: E402

from sm64_events.core import updater as _updater  # noqa: E402
from sm64_events.core.updater import UpdateService  # noqa: E402


def test_default_opener_passes_a_timeout(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["req"], seen["timeout"] = req, timeout
        return "response"

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert _updater.default_opener("REQ") == "response"
    assert seen["req"] == "REQ"
    assert seen["timeout"] == _updater.NET_TIMEOUT_S
    assert 0 < _updater.NET_TIMEOUT_S <= 120


def test_the_shipped_default_opener_is_the_one_with_the_timeout():
    for func in (_updater.check_for_update, UpdateService.__init__):
        default = inspect.signature(func).parameters["http"].default
        assert default is _updater.default_opener, func.__qualname__
