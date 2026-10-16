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
import time
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sm64_events.core.update_plan import (INSTALLED_MANIFEST, MANIFEST_ASSET,
                                          ZIP_ASSET, file_sha256, parse_manifest,
                                          validate_release_manifest)
from sm64_events.core.release_contract import release_assets, select_release, version_key
from sm64_events.core.update_policy import (adopt_channel_default, installed_floor,
                                            read_policy, record_installed)
from sm64_events.bootstrap.public_offer import bundled_offer, validate_offer
from sm64_events.core.profile_migration import (beta_profile_migration_available,
                                               migrate_private_beta_profile)
from sm64_events.bootstrap.beta_payload import (FOOTER, MAGIC, MAX_ARCHIVE_BYTES,
                                                checked_members, verify_payload)

DEFAULT_REPO = "griffinbeels/SM64-Trainer"
GITHUB_API = "https://api.github.com"
APP_EXE = "SM64Trainer.exe"
_UA = "SM64Trainer-bootstrap"
OFFLINE_OFFER = "bootstrap-offer.json"
MAX_TEXT_BYTES = 16 << 20


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
        body = r.read(MAX_TEXT_BYTES + 1)
        if len(body) > MAX_TEXT_BYTES:
            raise RuntimeError("release metadata exceeds its size limit")
        return body.decode("utf-8")


def published_releases(http, repo: str = DEFAULT_REPO) -> list[dict]:
    """Use the app's bounded discovery and independent stable fallback."""
    from sm64_events.core.release_feed import ReleaseFeed
    feed = ReleaseFeed(http=http, repo=repo, api_base=GITHUB_API)
    values = feed.releases()
    if not values and feed.last_error:
        raise RuntimeError(feed.last_error)
    return values


def default_policy_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "SM64Trainer" / "data" / "update_state.json"


def repair_floor(target: Path, policy: Path | None) -> str:
    current = None
    manifest = target / INSTALLED_MANIFEST
    try:
        if manifest.is_file() and not manifest.is_symlink() and manifest.stat().st_size <= MAX_TEXT_BYTES:
            value = parse_manifest(manifest.read_text(encoding="utf-8"))
            version_key(value.version)
            current = value.version
    except (OSError, ValueError, UnicodeError):
        pass  # The independently recorded floor still survives damaged program files.
    saved = read_policy(policy).get("installed_version") if policy is not None else None
    if current is None and saved is None and target.exists():
        if not target.is_dir() or any(target.iterdir()):
            raise RuntimeError("The existing application's version could not be established. "
                               "Its program and practice data were preserved. Contact support "
                               "for a verified recovery version before replacing it.")
    current = current or saved or "0.0.0"
    return installed_floor(policy, current) if policy is not None else current


def choose_repair(releases, floor: str, receive_beta: bool, ui) -> dict:
    selected = select_release(releases, floor, include_beta=receive_beta, allow_same=True)
    if selected is not None:
        return selected
    beta = select_release(releases, floor, include_beta=True, allow_same=True)
    if beta is not None and callable(getattr(ui, "confirm_beta_repair", None)):
        if ui.confirm_beta_repair(beta["tag_name"]):
            return beta
    raise RuntimeError("No published repair is available at or above your installed version. "
                       "An older release cannot safely replace it. Contact support for a newer repair.")


def download(http, url: str, dest: Path, progress=None) -> str:
    digest = hashlib.sha256()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with http(_request(url)) as resp:
        total = int((resp.headers or {}).get("Content-Length") or 0)
        if total < 0 or total > MAX_ARCHIVE_BYTES:
            raise RuntimeError("application download exceeds its size limit")
        done = 0
        started = time.monotonic()
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if done > MAX_ARCHIVE_BYTES or time.monotonic() - started > 1800:
                    raise RuntimeError("application download exceeded its size or time limit; retry setup")
                if progress and total:
                    progress(min(1.0, done / total))
    return digest.hexdigest()


def default_install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(
        Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / "SM64Trainer"


def _write_journal(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _sibling(target: Path, name: str) -> Path:
    if (not isinstance(name, str) or not name.startswith(f".{target.name}-install-")
            or Path(name).name != name):
        raise RuntimeError("installer recovery record is invalid")
    path = target.parent / name
    if path.is_symlink() or not path.resolve().is_relative_to(target.parent.resolve()):
        raise RuntimeError("installer recovery path is unsafe")
    return path


@contextmanager
def _install_lock(target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    path = target.parent / f".{target.name}-install.lock"
    with path.open("a+b") as stream:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("another SM64 Trainer installer is running") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def recover_install(target: Path) -> list[Path]:
    """Restore a stranded previous tree; never discard the only surviving copy."""
    journal = target.parent / f".{target.name}-install.json"
    if not journal.exists():
        return []
    if journal.is_symlink() or journal.stat().st_size > 4096:
        raise RuntimeError("installer recovery record is invalid")
    value = json.loads(journal.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise RuntimeError("installer recovery record is invalid")
    staging, previous = (_sibling(target, value.get(key)) for key in ("staging", "previous"))
    retained = value.get("retained", [])
    if not isinstance(retained, list) or len(retained) > 4:
        raise RuntimeError("installer recovery record is invalid")
    prior = [_sibling(target, name) for name in retained]
    if not target.exists() and previous.exists():
        os.replace(previous, target)
    return [staging, previous, *prior]


def verify_tree(root: Path, manifest) -> None:
    for entry in manifest.files:
        path = root.joinpath(*entry.path.split("/"))
        if (path.is_symlink() or not path.is_file() or path.stat().st_size != entry.size
                or file_sha256(path) != entry.sha256):
            raise RuntimeError("application payload does not match the offered manifest")


def _extract_verified_tree(zip_path, manifest, fresh, embedded):
    with zipfile.ZipFile(zip_path) as zf:
        members = checked_members(zf)
        actual = {m.filename for m in members if not m.is_dir()}
        expected = {entry.path for entry in manifest.files}
        if actual != expected | ({OFFLINE_OFFER} if embedded else set()):
            raise RuntimeError("application archive does not match the complete offered manifest")
        if shutil.disk_usage(fresh.parent).free < sum(e.size for e in manifest.files) + (32 << 20):
            raise RuntimeError("not enough free space to repair SM64 Trainer")
        for member in members:
            if member.filename == OFFLINE_OFFER:
                continue
            path = fresh.joinpath(*member.filename.split("/"))
            if member.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as source, path.open("xb") as output:
                    shutil.copyfileobj(source, output, 1 << 20)
    verify_tree(fresh, manifest)
    from sm64_events.core.build_channel import CONFIG_FILENAME, read_metadata
    config = fresh / "_internal" / CONFIG_FILENAME
    if config.exists() or version_key(manifest.version).prerelease:
        build = read_metadata(config)
        if (build.installation != "normal"
                or (build.channel == "beta") != bool(version_key(manifest.version).prerelease)):
            raise RuntimeError("application release identity does not match the offered version")
        version_metadata = fresh / "_internal" / "release-version.json"
        if (version_metadata.stat().st_size > 4096 or json.loads(version_metadata.read_text(encoding="utf-8"))
                != {"schema": 1, "version": manifest.version}):
            raise RuntimeError("application runtime version metadata does not match the offer")


def install_tree(zip_path: Path, manifest_text: str, install_dir: Path, *,
                 offered_version: str | None = None, embedded: bool = False,
                 policy_path: Path | None = None) -> Path:
    """Extract to a temp sibling then atomically swap directories, so a
    failed download/extract can never break an existing install. Raises
    RuntimeError when the current install is locked (app running)."""
    manifest = parse_manifest(manifest_text)
    validate_release_manifest(manifest, offered_version or manifest.version)
    if install_dir.is_symlink():
        raise RuntimeError("installation directory is unsafe")
    with _install_lock(install_dir):
        prior_paths = recover_install(install_dir)
        prior_paths = list(dict.fromkeys(path for path in prior_paths if path.exists()))
        if len(prior_paths) > 4:
            raise RuntimeError("too many interrupted installer backups; contact support before another repair")
        token = uuid4().hex
        fresh = _sibling(install_dir, f".{install_dir.name}-install-{token}.new")
        old = _sibling(install_dir, f".{install_dir.name}-install-{token}.old")
        journal = install_dir.parent / f".{install_dir.name}-install.json"
        moved_aside = False
        try:
            _extract_verified_tree(zip_path, manifest, fresh, embedded)
            # Another installer can finish while this one downloads. Recheck
            # the current files and profile floor under this transaction lock.
            if version_key(manifest.version) < version_key(repair_floor(install_dir, policy_path)):
                raise RuntimeError("a newer application was installed while downloading; use its repair version")
            (fresh / INSTALLED_MANIFEST).write_text(manifest_text, encoding="utf-8")
            _write_journal(journal, {"schema": 1, "staging": fresh.name,
                                     "previous": old.name, "version": manifest.version,
                                     "retained": [path.name for path in prior_paths]})
            if install_dir.exists():
                os.replace(install_dir, old)
                moved_aside = True
            os.replace(fresh, install_dir)
        except Exception as error:
            if moved_aside and not install_dir.exists():
                try:
                    os.replace(old, install_dir)
                except OSError:
                    # Journal and old remain, so another run can recover them.
                    raise RuntimeError("repair was interrupted; previous program files remain in "
                                       "the recovery backup. Close the app and rerun this installer") from error
            if fresh.exists():
                shutil.rmtree(fresh)
            raise RuntimeError("could not repair SM64 Trainer; program and practice data were preserved. "
                               f"Close the app and retry ({error})") from error
        # A newly verified repair supersedes prior evidence; retain this repair's
        # backup until a subsequent verified repair, without guessing startup success.
        for prior in prior_paths:
            if prior.exists() and prior not in (install_dir, old):
                shutil.rmtree(prior)
        _write_journal(journal, {"schema": 1, "staging": fresh.name,
                                 "previous": old.name, "version": manifest.version, "retained": []})
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


def _embedded_offer(own_path, ui, floor):
    ui.status("Verifying the included application…")
    verify_payload(own_path, progress=ui.progress)
    with zipfile.ZipFile(own_path) as archive:
        checked_members(archive)
        info = archive.getinfo(OFFLINE_OFFER)
        if info.file_size > MAX_TEXT_BYTES:
            raise RuntimeError("included installation offer exceeds its size limit")
        offer = json.loads(archive.read(info))
    if not isinstance(offer, dict) or set(offer) != {"schema", "version", "manifest"} or offer["schema"] != 1:
        raise RuntimeError("included installation offer is invalid")
    tag, manifest_text, zip_path = offer["version"], offer["manifest"], own_path
    if version_key(tag) < version_key(floor):
        raise RuntimeError("The included application is older than your installed version; use a newer installer.")
    # Choosing this private offline installer explicitly chooses its
    # version, without enabling future beta updates in the profile.
    return tag, manifest_text, zip_path


def _network_offer(http, ui, repo, floor, receive_beta, temporary):
    ui.status("Finding a compatible release…")
    selected = choose_repair(published_releases(http, repo), floor, receive_beta, ui)
    tag, assets = selected["tag_name"], release_assets(selected)
    zip_path = Path(temporary) / ZIP_ASSET
    ui.status(f"Downloading SM64 Trainer {tag}…")
    digest = download(http, assets[ZIP_ASSET], zip_path, progress=ui.progress)
    published = fetch_text(http, assets[ZIP_ASSET + ".sha256"]).split()
    if len(published) != 2 or published[0].lower() != digest or published[1].lstrip("*") != ZIP_ASSET:
        raise RuntimeError("download failed verification (checksum mismatch)")
    manifest_text = fetch_text(http, assets[MANIFEST_ASSET])
    manifest_sha = fetch_text(http, assets[MANIFEST_ASSET + ".sha256"]).split()
    manifest_digest = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    if (len(manifest_sha) != 2 or manifest_sha[0].lower() != manifest_digest
            or manifest_sha[1].lstrip("*") != MANIFEST_ASSET):
        raise RuntimeError("manifest failed verification (checksum mismatch)")
    return tag, manifest_text, zip_path


def run_install(*, http, ui, repo: str = DEFAULT_REPO,
                install_dir: "Path | None" = None,
                own_path: "Path | None" = None,
                policy_path: "Path | None" = None, activate: bool = True,
                public_offer: dict | None = None) -> bool:
    """The whole install: release -> download+verify zip -> swap in ->
    shortcut -> launch. Returns False after ui.error() on any failure."""
    target = install_dir or default_install_dir()
    policy = policy_path or default_policy_path()
    try:
        if (activate and beta_profile_migration_available()
                and callable(getattr(ui, "confirm_profile_migration", None))
                and ui.confirm_profile_migration()):
            try:
                migrate_private_beta_profile(confirmed=True)
            except (OSError, ValueError):
                ui.status("Could not copy the private beta profile. The original was preserved. "
                          "Setup will continue with the normal profile.")
        # Recover before reading the floor: a previous interrupted rename may
        # have left the installed manifest in the backup directory.
        with _install_lock(target):
            recover_install(target)
        floor = repair_floor(target, policy)
        preference = read_policy(policy)
        receive_beta = preference["receive_beta_updates"]
        offer = validate_offer(public_offer) if public_offer is not None else None
        embedded = False
        if own_path is not None and own_path.is_file() and own_path.stat().st_size > FOOTER.size:
            with own_path.open("rb") as source:
                source.seek(-FOOTER.size, 2)
                embedded = FOOTER.unpack(source.read(FOOTER.size))[0] == MAGIC
        with tempfile.TemporaryDirectory(prefix="sm64-bootstrap-") as td:
            if embedded:
                tag, manifest_text, zip_path = _embedded_offer(own_path, ui, floor)
            else:
                # Downloading beta chooses this installation, just like the
                # private offline offer. The player's future update preference
                # remains sovereign; the installed floor prevents downgrades.
                if offer is not None:
                    floor = max((floor, offer["version"]), key=version_key)
                    receive_beta = receive_beta or offer["channel"] == "beta"
                tag, manifest_text, zip_path = _network_offer(http, ui, repo, floor, receive_beta, td)
            ui.status("Installing…")
            exe = install_tree(zip_path, manifest_text, target, offered_version=tag, embedded=embedded,
                               policy_path=policy)
            record_installed(policy, parse_manifest(manifest_text).version)
            if offer is not None and not embedded:
                adopt_channel_default(policy, offer["channel"])
        if activate:
            if not create_desktop_shortcut(exe):
                ui.status("Note: could not create the Desktop shortcut — "
                          f"the app is installed at {exe}")
            ui.status("Starting SM64 Trainer…")
            # A downloaded public bootstrap cleans itself up after the install.
            # The private offline installer is the file he hands to testers and
            # reruns for repair (round 42: "now SM64Trainer-1.10.0-beta.5-
            # private.exe doesn't exist?"), so it stays where it is.
            launch_app(exe, None if embedded else own_path)
        ui.done(exe)
        return True
    except Exception as err:
        ui.error(str(err))
        return False


def install_self_check(own_path: Path, receipt: Path) -> int:
    """Exercise this EXE's included install without activating a user resource."""
    if not receipt.is_absolute() or receipt.suffix.lower() != ".json" or not receipt.parent.is_dir():
        return 2
    receipt = receipt.resolve()
    base = default_policy_path().parents[2]
    if (any(receipt.is_relative_to(base / name) for name in ("SM64Trainer", "SM64TrainerBeta", "sm64_tracker"))
            or receipt.is_relative_to(default_install_dir())):
        return 2
    report = {"schema": 1, "kind": "offline-install-self-check", "ok": False}
    try:
        with tempfile.TemporaryDirectory(prefix="sm64-install-check-", dir=receipt.parent) as temporary:
            root = Path(temporary)
            target, policy = root / "Programs/SM64Trainer", root / "profile/data/update_state.json"
            policy.parent.mkdir(parents=True)
            history = policy.parent / "tracker.db"
            history.write_bytes(b"MAINTAINER-PROBE-PRACTICE")
            def no_http(_request):
                raise RuntimeError("network is forbidden in the offline install self-check")
            okay = run_install(http=no_http, ui=ConsoleUI(), own_path=own_path,
                               install_dir=target, policy_path=policy, activate=False)
            if okay:
                (target / APP_EXE).write_bytes(b"MAINTAINER-PROBE-CORRUPT")
                (target / "obsolete-probe.dll").write_bytes(b"OBSOLETE")
                okay = run_install(http=no_http, ui=ConsoleUI(), own_path=own_path,
                                   install_dir=target, policy_path=policy, activate=False)
            if okay:
                from sm64_events.core.build_channel import CONFIG_FILENAME, read_metadata
                build = read_metadata(target / "_internal" / CONFIG_FILENAME)
                manifest = parse_manifest((target / INSTALLED_MANIFEST).read_text(encoding="utf-8"))
                report.update(version=manifest.version, installation=build.installation,
                              installed_files=len(manifest.files),
                              profile_preserved=history.read_bytes() == b"MAINTAINER-PROBE-PRACTICE",
                              same_version_repair=True, stale_removed=not (target / "obsolete-probe.dll").exists(),
                              updates_enabled=build.updates_enabled, port=build.default_port)
                report["ok"] = build.installation == "normal" and report["profile_preserved"] and report["stale_removed"]
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
        pass  # No submitted metadata or private invitation enters the receipt.
    receipt.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0 if report["ok"] else 2


class ConsoleUI:
    """--silent / test UI: prints instead of windowing."""

    def __init__(self, *, allow_beta_repair: bool = False, migrate_beta_profile: bool = False):
        self.allow_beta_repair = allow_beta_repair
        self.migrate_beta_profile = migrate_beta_profile

    def confirm_beta_repair(self, tag: str) -> bool:
        return self.allow_beta_repair

    def confirm_profile_migration(self) -> bool:
        return self.migrate_beta_profile

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

    def confirm_beta_repair(self, tag: str) -> bool:
        return self._confirm("SM64 Trainer repair", f"The available forward repair is beta {tag}. "
                             "Install this version? Your beta update preference will stay unchanged.")

    def confirm_profile_migration(self) -> bool:
        return self._confirm("Copy private beta practice history?",
                             "Copy your private beta practice history into the normal SM64 Trainer profile? "
                             "The original will be kept. You will sign in once with the normal app.")

    def _confirm(self, title: str, message: str) -> bool:
        import threading
        event = threading.Event()
        answer = {"yes": False}

        def choose():
            from tkinter import messagebox
            answer["yes"] = messagebox.askyesno(title, message, parent=self.root)
            event.set()
        self.root.after(0, choose)
        event.wait()
        return answer["yes"]


def main(argv: "list[str] | None" = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    silent = "--silent" in args
    own_path = Path(sys.executable) if getattr(sys, "frozen", False) else None
    if args and args[0] == "--install-self-check":
        if len(args) != 2 or own_path is None:
            return 2
        return install_self_check(own_path, Path(args[1]))
    # Read before any network request; invalid bundled intent fails closed.
    try:
        offer = bundled_offer()
    except (OSError, ValueError) as error:
        if silent:
            ConsoleUI().error(str(error))
        else:
            ui = TkUI()
            ui.error(str(error))
            ui.root.mainloop()
        return 1
    if silent:
        ui = ConsoleUI(allow_beta_repair="--allow-beta-repair" in args,
                       migrate_beta_profile="--migrate-beta-profile" in args)
        ok = run_install(http=lambda request: urllib.request.urlopen(request, timeout=60), ui=ui,
                         own_path=own_path, public_offer=offer)
        return 0 if ok else 1
    ui = TkUI()
    result = {"ok": False}

    def work():
        result["ok"] = run_install(http=lambda request: urllib.request.urlopen(request, timeout=60), ui=ui,
                                   own_path=own_path, public_offer=offer)

    import threading
    threading.Thread(target=work, daemon=True).start()
    ui.root.mainloop()
    return 0 if result["ok"] and not ui.failed else 1
