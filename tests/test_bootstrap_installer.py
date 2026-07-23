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


def test_launch_app_scrubs_launcher_env(tmp_path, monkeypatch):
    """The migration chain sets SM64_RESTART (old app's restart handoff) and
    PyInstaller bootloader vars in the BOOTSTRAP's env; leaking them makes
    the fresh install skip its single-instance takeover / confuses the
    onedir bootloader (final review, minor 4)."""
    monkeypatch.setenv("SM64_RESTART", "1")
    monkeypatch.setenv("_MEIPASS2", r"C:\temp\_MEI123")
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\temp\_MEI123")
    monkeypatch.setenv("KEEP_ME", "yes")
    kwargs_seen = {}
    exe = tmp_path / "SM64Trainer.exe"
    launch_app(exe, None,
               popen=lambda args, **kw: kwargs_seen.update(kw))
    env = kwargs_seen["env"]
    assert "SM64_RESTART" not in env
    assert "_MEIPASS2" not in env
    assert "_PYI_APPLICATION_HOME_DIR" not in env
    assert env["KEEP_ME"] == "yes"


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
        "https://dl/manifest.sha": (
            hashlib.sha256(manifest.encode()).hexdigest()
            + "  manifest.json").encode(),
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


from sm64_events.bootstrap import installer as boot


def test_console_ui_reports_and_swallows_output(capsys):
    ui = boot.ConsoleUI()
    ui.status("hello")
    ui.progress(0.5)
    ui.error("bad")
    ui.done(Path("x"))
    out = capsys.readouterr().out
    assert "hello" in out and "bad" in out


def test_main_silent_runs_install(monkeypatch, tmp_path):
    called = {}

    def fake_run_install(**kw):
        called.update(kw)
        return True

    monkeypatch.setattr(boot, "run_install", fake_run_install)
    assert boot.main(["--silent"]) == 0
    assert isinstance(called["ui"], boot.ConsoleUI)
    assert called["own_path"] is None      # not frozen under pytest


def test_main_silent_failure_returns_1(monkeypatch):
    monkeypatch.setattr(boot, "run_install", lambda **kw: False)
    assert boot.main(["--silent"]) == 1


# --- review findings (2026-07-23 wave-1 review) ---

def test_shortcut_escapes_apostrophes_in_paths(tmp_path):
    """A username like O'Brien puts an apostrophe in the exe path; inside a
    single-quoted PS literal it must be doubled or the script is a parse
    error and the shortcut silently never appears (I-2)."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 0
        return R()

    exe = tmp_path / "O'Brien" / "SM64Trainer.exe"
    assert create_desktop_shortcut(exe, run=fake_run) is True
    script = calls[0][-1]
    assert "O''Brien" in script
    assert "O'Brien\\SM64Trainer" not in script.replace("''", "\x00")


def test_run_install_bad_manifest_hash_reports_error(tmp_path, monkeypatch):
    """The installed manifest must be verified like the zip (M-1)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    blob = _zip_bytes({APP_EXE: b"EXE"})
    routes = {
        LATEST: _release_json("v2.0.0", FULL_ASSETS),
        "https://dl/full.zip": blob,
        "https://dl/full.sha": (hashlib.sha256(blob).hexdigest()
                                + "  SM64Trainer-full.zip").encode(),
        "https://dl/manifest.json": b'{"schema": 1}',
        "https://dl/manifest.sha": ("0" * 64 + "  manifest.json").encode(),
    }
    ui = _UI()
    assert run_install(http=_http(routes), ui=ui) is False
    assert ui.errors
    assert not (tmp_path / "lad" / "Programs" / "SM64Trainer").exists()


def test_install_tree_rejects_zip_without_exe(tmp_path):
    """A sha-valid but malformed zip (missing the exe) must never replace a
    working install (M-3)."""
    import pytest
    target = tmp_path / "Programs" / "SM64Trainer"
    good = _zip_bytes({APP_EXE: b"GOOD"})
    zp = tmp_path / "full.zip"
    zp.write_bytes(good)
    install_tree(zp, '{"schema": 1}', target)
    zp.write_bytes(_zip_bytes({"_internal/only.dll": b"X"}))   # no exe
    with pytest.raises(RuntimeError):
        install_tree(zp, '{"schema": 1}', target)
    assert (target / APP_EXE).read_bytes() == b"GOOD"          # intact


def test_install_tree_restores_old_when_swap_fails(tmp_path, monkeypatch):
    """If current->old succeeds but new->current fails, the last-good
    install must be moved back, never left stranded as .old (M-2)."""
    import pytest
    target = tmp_path / "Programs" / "SM64Trainer"
    zp = tmp_path / "full.zip"
    zp.write_bytes(_zip_bytes({APP_EXE: b"V1"}))
    install_tree(zp, '{"schema": 1}', target)
    zp.write_bytes(_zip_bytes({APP_EXE: b"V2"}))
    real = os.replace

    def fail_fresh_move(src, dst):
        if str(src).endswith(".new"):
            raise PermissionError("locked")
        return real(src, dst)

    import sm64_events.bootstrap.installer as installer_mod
    monkeypatch.setattr(installer_mod.os, "replace", fail_fresh_move)
    with pytest.raises(RuntimeError):
        install_tree(zp, '{"schema": 1}', target)
    monkeypatch.setattr(installer_mod.os, "replace", real)
    assert (target / APP_EXE).read_bytes() == b"V1"            # restored


import os  # noqa: E402  (used by the monkeypatch tests above)
