# tests/test_bootstrap_installer.py
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from sm64_events.bootstrap.installer import (APP_EXE, create_desktop_shortcut,
                                             default_install_dir, download,
                                             install_tree, latest_release,
                                             launch_app, run_install)
from sm64_events.core.update_plan import INSTALLED_MANIFEST, ZIP_ASSET


class _Resp(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.status = 200
        self.headers = headers or {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _http(routes):
    def opener(req):
        url = req.full_url if hasattr(req, "full_url") else req
        if url not in routes:
            raise OSError(f"unmapped url {url}")
        return _Resp(routes[url])
    return opener


def _release_json(tag, assets):
    return json.dumps({
        "tag_name": tag,
        "assets": [{"name": n, "browser_download_url": u}
                   for n, u in assets.items()]}).encode()


LATEST = "https://api.github.com/repos/griffinbeels/SM64-Trainer/releases/latest"

FULL_ASSETS = {
    "SM64Trainer-full.zip": "https://dl/full.zip",
    "SM64Trainer-full.zip.sha256": "https://dl/full.sha",
    "manifest.json": "https://dl/manifest.json",
    "manifest.json.sha256": "https://dl/manifest.sha",
}


def test_latest_release_returns_assets():
    http = _http({LATEST: _release_json("v2.0.0", FULL_ASSETS)})
    tag, assets = latest_release(http)
    assert tag == "v2.0.0"
    assert assets[ZIP_ASSET] == "https://dl/full.zip"


def test_latest_release_requires_zip_and_manifest():
    http = _http({LATEST: _release_json("v2.0.0", {"other.txt": "u"})})
    with pytest.raises(RuntimeError):
        latest_release(http)


def test_download_streams_and_hashes(tmp_path):
    payload = b"Z" * 100_000
    http = _http({"https://dl/full.zip": payload})
    seen = []
    digest = download(http, "https://dl/full.zip", tmp_path / "z.zip",
                      progress=seen.append)
    assert (tmp_path / "z.zip").read_bytes() == payload
    assert digest == hashlib.sha256(payload).hexdigest()
    assert seen and seen[-1] == 1.0


def test_default_install_dir_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_install_dir() == tmp_path / "Programs" / "SM64Trainer"


def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
    return buf.getvalue()


def test_install_tree_fresh_and_overwrite(tmp_path):
    zp = tmp_path / "full.zip"
    zp.write_bytes(_zip_bytes({APP_EXE: b"EXE-V1", "_internal/a.dll": b"A"}))
    target = tmp_path / "Programs" / "SM64Trainer"
    exe = install_tree(zp, '{"schema": 1}', target)
    assert exe == target / APP_EXE
    assert exe.read_bytes() == b"EXE-V1"
    assert (target / INSTALLED_MANIFEST).read_text() == '{"schema": 1}'
    # reinstall over an existing dir (repair path)
    zp.write_bytes(_zip_bytes({APP_EXE: b"EXE-V2"}))
    exe = install_tree(zp, '{"schema": 1}', target)
    assert exe.read_bytes() == b"EXE-V2"
    assert not target.with_name(target.name + ".old").exists()
    assert not target.with_name(target.name + ".new").exists()


def test_create_desktop_shortcut_invokes_powershell(tmp_path):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 0
        return R()

    exe = tmp_path / "SM64Trainer.exe"
    assert create_desktop_shortcut(exe, run=fake_run) is True
    joined = " ".join(calls[0])
    assert "powershell" in calls[0][0]
    assert "SM64 Trainer.lnk" in joined
    assert str(exe) in joined


def test_launch_app_passes_cleanup_arg(tmp_path):
    calls = []
    exe = tmp_path / "app" / "SM64Trainer.exe"
    exe.parent.mkdir()
    launch_app(exe, tmp_path / "bootstrap.exe",
               popen=lambda args, **kw: calls.append(args))
    assert calls[0] == [str(exe), "--cleanup-bootstrap",
                       str(tmp_path / "bootstrap.exe")]


class _UI:
    def __init__(self):
        self.errors, self.done_exe = [], None

    def status(self, msg):
        pass

    def progress(self, frac):
        pass

    def error(self, msg):
        self.errors.append(msg)

    def done(self, exe):
        self.done_exe = exe


def test_run_install_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    blob = _zip_bytes({APP_EXE: b"EXE", "_internal/a.dll": b"A"})
    manifest = '{"schema": 1, "version": "2.0.0", "files": []}'
    routes = {
        LATEST: _release_json("v2.0.0", FULL_ASSETS),
        "https://dl/full.zip": blob,
        "https://dl/full.sha": (hashlib.sha256(blob).hexdigest()
                                + "  SM64Trainer-full.zip").encode(),
        "https://dl/manifest.json": manifest.encode(),
    }
    launched = []
    monkeypatch.setattr("sm64_events.bootstrap.installer.create_desktop_shortcut",
                        lambda exe, run=None: True)
    monkeypatch.setattr("sm64_events.bootstrap.installer.launch_app",
                        lambda exe, own, popen=None: launched.append((exe, own)))
    ui = _UI()
    ok = run_install(http=_http(routes), ui=ui,
                     own_path=tmp_path / "boot.exe")
    assert ok is True and not ui.errors
    exe = tmp_path / "lad" / "Programs" / "SM64Trainer" / APP_EXE
    assert exe.read_bytes() == b"EXE"
    assert launched == [(exe, tmp_path / "boot.exe")]
    assert ui.done_exe == exe


def test_run_install_bad_zip_hash_reports_error(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    blob = _zip_bytes({APP_EXE: b"EXE"})
    routes = {
        LATEST: _release_json("v2.0.0", FULL_ASSETS),
        "https://dl/full.zip": blob,
        "https://dl/full.sha": ("0" * 64 + "  x").encode(),
        "https://dl/manifest.json": b"{}",
    }
    ui = _UI()
    assert run_install(http=_http(routes), ui=ui) is False
    assert ui.errors
    assert not (tmp_path / "lad" / "Programs" / "SM64Trainer").exists()
