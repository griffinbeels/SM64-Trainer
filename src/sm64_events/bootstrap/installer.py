# src/sm64_events/bootstrap/installer.py
"""Bootstrap installer: the tiny onefile exe published as SM64Trainer.exe.

Two jobs, one code path (idempotent — running it again is a repair):
1. MIGRATION: already-shipped onefile installs can only self-update to an
   asset named SM64Trainer.exe — so that asset IS this bootstrap. The old
   updater swaps it in and relaunches it; it then downloads the full zip
   (the LAST full download), installs to %LOCALAPPDATA%\\Programs\\SM64Trainer,
   creates the Desktop shortcut, launches the real app, and asks the app to
   delete this file (--cleanup-bootstrap; a running exe cannot delete itself).
2. NEW USERS: the GitHub download habit ("grab SM64Trainer.exe, double-click")
   now yields a proper installer.

Stdlib-only + core.update_plan (asset names) so the PyInstaller build stays
~25 MB. User data under %LOCALAPPDATA%\\SM64Trainer is NEVER touched — this
installs only the program directory."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from sm64_events.core.update_plan import (INSTALLED_MANIFEST, MANIFEST_ASSET,
                                          ZIP_ASSET)

DEFAULT_REPO = "griffinbeels/SM64-Trainer"
GITHUB_API = "https://api.github.com"
APP_EXE = "SM64Trainer.exe"
_UA = "SM64Trainer-bootstrap"


def _request(url: str, accept: "str | None" = None) -> urllib.request.Request:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if accept:
        req.add_header("Accept", accept)
    return req


def latest_release(http, repo: str = DEFAULT_REPO) -> tuple[str, dict]:
    url = f"{GITHUB_API}/repos/{repo}/releases/latest"
    with http(_request(url, accept="application/vnd.github+json")) as r:
        rel = json.loads(r.read().decode("utf-8"))
    assets = {a.get("name"): a.get("browser_download_url")
              for a in rel.get("assets", [])}
    for need in (ZIP_ASSET, ZIP_ASSET + ".sha256",
                 MANIFEST_ASSET, MANIFEST_ASSET + ".sha256"):
        if not assets.get(need):
            raise RuntimeError(f"latest release is missing asset {need}")
    return rel.get("tag_name") or "", assets


def fetch_text(http, url: str) -> str:
    with http(_request(url)) as r:
        return r.read().decode("utf-8")


def download(http, url: str, dest: Path, progress=None) -> str:
    digest = hashlib.sha256()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with http(_request(url)) as resp:
        total = int((resp.headers or {}).get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(1.0, done / total))
    return digest.hexdigest()


def default_install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(
        Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / "SM64Trainer"


def install_tree(zip_path: Path, manifest_text: str, install_dir: Path) -> Path:
    """Extract to a temp sibling then atomically swap directories, so a
    failed download/extract can never break an existing install. Raises
    RuntimeError when the current install is locked (app running)."""
    import zipfile
    fresh = install_dir.with_name(install_dir.name + ".new")
    old = install_dir.with_name(install_dir.name + ".old")
    shutil.rmtree(fresh, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(fresh)
    if not (fresh / APP_EXE).is_file():
        # Never replace a working install with a zip that can't launch
        # (sha-valid but malformed release). Wave-1 review M-3.
        shutil.rmtree(fresh, ignore_errors=True)
        raise RuntimeError(f"downloaded release is missing {APP_EXE}")
    (fresh / INSTALLED_MANIFEST).write_text(manifest_text)
    install_dir.parent.mkdir(parents=True, exist_ok=True)
    moved_aside = False
    try:
        if install_dir.exists():
            os.replace(install_dir, old)
            moved_aside = True
        os.replace(fresh, install_dir)
    except OSError as err:
        if moved_aside and not install_dir.exists():
            # The aside-move succeeded but the fresh-move failed: put the
            # last-good install back so a failed run never leaves the user
            # without a program dir. Wave-1 review M-2.
            try:
                os.replace(old, install_dir)
            except OSError:
                pass
        raise RuntimeError(
            "could not replace the existing install — close SM64 Trainer "
            f"and run this installer again ({err})") from err
    shutil.rmtree(old, ignore_errors=True)
    return install_dir / APP_EXE


_SHORTCUT_PS = (
    "$desktop=[Environment]::GetFolderPath('Desktop');"
    "$ws=New-Object -ComObject WScript.Shell;"
    "$s=$ws.CreateShortcut((Join-Path $desktop 'SM64 Trainer.lnk'));"
    "$s.TargetPath='{exe}';"
    "$s.WorkingDirectory='{workdir}';"
    "$s.IconLocation='{exe},0';"
    "$s.Save()")


def _ps_quote(value: str) -> str:
    """Escape for a single-quoted PowerShell literal: only the apostrophe is
    special (doubled). Without this, a username like O'Brien closes the
    string early and the shortcut silently fails. Wave-1 review I-2."""
    return value.replace("'", "''")


def create_desktop_shortcut(exe: Path, run=subprocess.run) -> bool:
    """[Environment]::GetFolderPath('Desktop') (not %USERPROFILE%\\Desktop)
    so OneDrive-redirected Desktops get the shortcut too."""
    script = _SHORTCUT_PS.format(exe=_ps_quote(str(exe)),
                                 workdir=_ps_quote(str(exe.parent)))
    try:
        run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True, capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:
        return False


def launch_app(exe: Path, own_path: "Path | None",
               popen=subprocess.Popen) -> None:
    args = [str(exe)]
    if own_path is not None:
        args += ["--cleanup-bootstrap", str(own_path)]
    # Scrub inherited launcher state: SM64_RESTART (the OLD app's restart
    # handoff — the fresh install must do a NORMAL first boot, not skip the
    # single-instance takeover) and this onefile bootstrap's PyInstaller
    # bootloader vars (_MEIPASS2/_PYI_*), which would confuse the onedir
    # app's bootloader. Mirrors core/relaunch.spawn_replacement's scrub.
    env = {key: value for key, value in os.environ.items()
           if key != "SM64_RESTART" and not key.startswith(("_MEI", "_PYI"))}
    popen(args, cwd=str(exe.parent), close_fds=True, env=env)


def run_install(*, http, ui, repo: str = DEFAULT_REPO,
                install_dir: "Path | None" = None,
                own_path: "Path | None" = None) -> bool:
    """The whole install: release -> download+verify zip -> swap in ->
    shortcut -> launch. Returns False after ui.error() on any failure."""
    target = install_dir or default_install_dir()
    try:
        ui.status("Finding the latest release…")
        tag, assets = latest_release(http, repo)
        with tempfile.TemporaryDirectory(prefix="sm64-bootstrap-") as td:
            zip_path = Path(td) / ZIP_ASSET
            ui.status(f"Downloading SM64 Trainer {tag}…")
            digest = download(http, assets[ZIP_ASSET], zip_path,
                              progress=ui.progress)
            published = fetch_text(http, assets[ZIP_ASSET + ".sha256"]).split()
            if not published or published[0].strip().lower() != digest:
                raise RuntimeError("download failed verification "
                                   "(checksum mismatch)")
            manifest_text = fetch_text(http, assets[MANIFEST_ASSET])
            manifest_sha = fetch_text(http,
                                      assets[MANIFEST_ASSET + ".sha256"]).split()
            manifest_digest = hashlib.sha256(
                manifest_text.encode("utf-8")).hexdigest()
            if not manifest_sha or \
                    manifest_sha[0].strip().lower() != manifest_digest:
                # Same no-unverified-bytes rule as the zip: a bad installed
                # manifest would force a full re-download at the next update.
                raise RuntimeError("manifest failed verification "
                                   "(checksum mismatch)")
            ui.status("Installing…")
            exe = install_tree(zip_path, manifest_text, target)
        if not create_desktop_shortcut(exe):
            ui.status("Note: could not create the Desktop shortcut — "
                      f"the app is installed at {exe}")
        ui.status("Starting SM64 Trainer…")
        launch_app(exe, own_path)
        ui.done(exe)
        return True
    except Exception as err:
        ui.error(str(err))
        return False


class ConsoleUI:
    """--silent / test UI: prints instead of windowing."""

    def status(self, msg: str) -> None:
        print(msg)

    def progress(self, frac: float) -> None:
        print(f"  {frac * 100:5.1f}%")

    def error(self, msg: str) -> None:
        print(f"ERROR: {msg}")

    def done(self, exe: Path) -> None:
        print(f"Installed: {exe}")


class TkUI:
    """Minimal tkinter progress window. All widget mutations are marshalled
    onto the Tk main thread via after(); the worker thread only calls these
    methods."""

    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self._tk = tk
        self.root = tk.Tk()
        self.root.title("SM64 Trainer Setup")
        self.root.geometry("420x120")
        self.root.resizable(False, False)
        self.label = tk.Label(self.root, text="Starting…", anchor="w")
        self.label.pack(fill="x", padx=16, pady=(18, 6))
        self.bar = ttk.Progressbar(self.root, maximum=1000)
        self.bar.pack(fill="x", padx=16)
        self.failed = False

    def status(self, msg: str) -> None:
        self.root.after(0, lambda: self.label.config(text=msg))

    def progress(self, frac: float) -> None:
        self.root.after(0, lambda: self.bar.config(value=int(frac * 1000)))

    def error(self, msg: str) -> None:
        self.failed = True

        def show():
            from tkinter import messagebox
            messagebox.showerror("SM64 Trainer Setup", msg)
            self.root.destroy()
        self.root.after(0, show)

    def done(self, exe: Path) -> None:
        self.root.after(0, self.root.destroy)


def main(argv: "list[str] | None" = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    silent = "--silent" in args
    own_path = Path(sys.executable) if getattr(sys, "frozen", False) else None
    if silent:
        ui = ConsoleUI()
        ok = run_install(http=urllib.request.urlopen, ui=ui,
                         own_path=own_path)
        return 0 if ok else 1
    ui = TkUI()
    result = {"ok": False}

    def work():
        result["ok"] = run_install(http=urllib.request.urlopen, ui=ui,
                                   own_path=own_path)

    import threading
    threading.Thread(target=work, daemon=True).start()
    ui.root.mainloop()
    return 0 if result["ok"] and not ui.failed else 1
