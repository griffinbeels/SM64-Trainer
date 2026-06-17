# src/sm64_events/core/updater.py
"""Self-update for the frozen exe: check the GitHub 'latest' release, download
the new exe, verify its SHA-256, and swap it in over the running process using
the Windows rename-a-running-exe trick (the OS forbids DELETING a running exe
but ALLOWS renaming one). The restart rides core/relaunch.spawn_replacement.

Pure helpers (parse_version … exe_dir_writable) take an injected HTTP opener and
operate on explicit paths so tests never touch the network or a real exe. The
stateful UpdateService orchestrates them, caches the check, tracks download
progress, and persists the 'skipped' version. Everything is guarded on
is_frozen(): from source it is inert (update_available is always False) so a dev
tree is never swapped."""
import hashlib
import json
import logging
import os
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sm64_events.core.paths import is_frozen, update_state_path

log = logging.getLogger("sm64.updater")

DEFAULT_REPO = "griffinbeels/SM64-Trainer"
GITHUB_API = "https://api.github.com"
EXE_NAME = "SM64Trainer.exe"
_UA = "SM64Trainer-updater"
_CHECK_TTL_S = 3600.0


def parse_version(tag: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3' -> (1, 2, 3). A non-numeric piece stops the parse, so
    '1.2.3-beta' compares as (1, 2, 3)."""
    out: list[int] = []
    for part in tag.lstrip("vV").split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        out.append(int(num))
    return tuple(out)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


@dataclass
class UpdateInfo:
    version: str
    notes: str
    html_url: str
    asset_url: str
    sha256_url: str


def _get(http, url: str, *, accept: str | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if accept:
        req.add_header("Accept", accept)
    return http(req)


def check_for_update(current: str, *, http=urllib.request.urlopen,
                     repo: str = DEFAULT_REPO,
                     api_base: str = GITHUB_API) -> "UpdateInfo | None":
    """GET the latest release; return UpdateInfo iff it is strictly newer AND
    carries an EXE_NAME asset. Best-effort: any error -> None (no popup)."""
    try:
        url = f"{api_base}/repos/{repo}/releases/latest"
        with _get(http, url, accept="application/vnd.github+json") as r:
            rel = json.loads(r.read().decode("utf-8"))
        tag = rel.get("tag_name") or ""
        if not is_newer(tag, current):
            return None
        assets = {a.get("name"): a.get("browser_download_url")
                  for a in rel.get("assets", [])}
        asset_url = assets.get(EXE_NAME)
        if not asset_url:
            return None
        sha256_url = assets.get(EXE_NAME + ".sha256")
        if not sha256_url:
            log.info("release %s has no .sha256 asset; not offering", tag)
            return None
        return UpdateInfo(
            version=tag.lstrip("vV"),
            notes=rel.get("body") or "",
            html_url=rel.get("html_url") or "",
            asset_url=asset_url,
            sha256_url=sha256_url)
    except Exception:
        log.info("update check failed", exc_info=True)
        return None


def download_and_stage(info: "UpdateInfo", exe_dir: Path, *,
                       http=urllib.request.urlopen, progress=None) -> Path:
    """Stream the new exe to <exe_dir>/SM64Trainer.exe.new, verify SHA-256
    against the published .sha256, return the staged path. Raises ValueError on
    a hash mismatch (caller keeps the current exe)."""
    staged = exe_dir / (EXE_NAME + ".new")
    h = hashlib.sha256()
    with _get(http, info.asset_url) as r:
        total = int((r.headers or {}).get("Content-Length") or 0)
        done = 0
        with open(staged, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(1.0, done / total))
    if info.sha256_url:
        with _get(http, info.sha256_url) as r:
            text = r.read().decode("utf-8")
        parts = text.split()
        if not parts:
            staged.unlink(missing_ok=True)
            raise ValueError("sha256 file is empty or malformed")
        published = parts[0].strip().lower()
        if published != h.hexdigest():
            staged.unlink(missing_ok=True)
            raise ValueError("update checksum mismatch")
    return staged


def apply_update(staged: Path, current_exe: Path, *, retries: int = 5,
                 sleep=time.sleep) -> None:
    """Swap `staged` in for the running exe via two renames (Windows allows
    renaming a running exe). Renaming the running exe aside happens once; only
    the staged->current move is retried (AV can briefly lock the new file). On
    final failure the backup is restored so the install is never left exe-less."""
    old = current_exe.parent / (current_exe.name + ".old")
    old.unlink(missing_ok=True)
    os.replace(current_exe, old)            # fails here -> current_exe untouched
    for attempt in range(retries):
        try:
            os.replace(staged, current_exe)
            return
        except PermissionError:
            if attempt == retries - 1:
                try:
                    os.replace(old, current_exe)   # restore the backup
                except OSError:
                    log.error("update failed AND backup restore failed; "
                              "exe at %s", old)
                raise
            sleep(0.5)


def cleanup_old(exe_path: Path, *, attempts: int = 1, sleep=time.sleep) -> bool:
    """Delete the `.old` backup of `exe_path` left by a prior self-update.
    Derives ``<name>.old`` from the ACTUAL running-exe path (not a glob or a
    constant) so a shared dir's foreign .old files are never touched and any
    future exe rename stays consistent with apply_update. Retries up to
    `attempts` times (1 s apart): right after a restart the OLD process — which
    executes FROM the .old file — is often still alive and locking it. Returns
    True once the backup is gone."""
    old = exe_path.parent / (exe_path.name + ".old")
    for i in range(attempts):
        if not old.exists():
            return True
        try:
            old.unlink()
            return True
        except OSError:
            if i < attempts - 1:
                sleep(1.0)
    return False


def exe_dir_writable(exe_dir: Path) -> bool:
    probe = exe_dir / ".sm64_update_probe"
    try:
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False


class UpdateService:
    """Stateful orchestrator the REST layer talks to: caches the check, tracks
    download progress/state, persists the skipped version. Inert unless frozen.

    SM64_UPDATE_FAKE=1 makes status() report a synthetic update (writable forced
    False, so the only action is the GitHub link) — lets the popup be verified
    in dev WITHOUT cutting a real release."""

    def __init__(self, *, current_version: str, repo: str = DEFAULT_REPO,
                 exe_path: "Path | None" = None,
                 http=urllib.request.urlopen,
                 state_path: "Path | None" = None,
                 frozen: "bool | None" = None):
        self.current = current_version
        self.repo = repo
        self._http = http
        self._frozen = is_frozen() if frozen is None else frozen
        self._exe = Path(exe_path) if exe_path else Path(sys.executable)
        self._state_path = state_path or update_state_path()
        self._lock = threading.Lock()
        self._state = "idle"        # idle | downloading | installing | error
        self._progress = 0.0
        self._cache: "UpdateInfo | None" = None
        self._checked_at = 0.0      # monotonic of last real check; 0 = never
        self._writable: "bool | None" = None   # probed once, then cached

    # --- skipped-version overlay ---
    def _skipped(self) -> "str | None":
        try:
            return json.loads(self._state_path.read_text()).get("skipped")
        except Exception:
            return None

    def skip(self, version: str) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps({"skipped": version}))

    # --- cached check ---
    def _fake(self) -> "UpdateInfo | None":
        if not os.environ.get("SM64_UPDATE_FAKE"):
            return None
        return UpdateInfo(
            version="9.9.9",
            notes="## Demo release\n"
                  "- **New:** a sample bullet whose text is long enough to wrap\n"
                  "  onto a second source line, exercising the soft-wrap join.\n"
                  "- A second bullet that mentions the `.old` backup as code.\n"
                  "\n"
                  "A trailing paragraph after a blank line, also wrapping across\n"
                  "two source lines, to confirm paragraphs join too.",
            html_url=f"https://github.com/{self.repo}/releases",
            asset_url="", sha256_url="")

    def _check(self, force: bool) -> "UpdateInfo | None":
        fake = self._fake()
        if fake is not None:
            return fake
        if not self._frozen:
            return None
        now = time.monotonic()
        if not force and self._checked_at and (now - self._checked_at) < _CHECK_TTL_S:
            return self._cache
        self._cache = check_for_update(self.current, http=self._http,
                                       repo=self.repo)
        self._checked_at = now
        return self._cache

    def status(self, force: bool = False) -> dict:
        info = self._check(force)
        fake = bool(os.environ.get("SM64_UPDATE_FAKE"))
        if self._writable is None and self._frozen and info is not None and not fake:
            self._writable = exe_dir_writable(self._exe.parent)
        writable = bool(self._writable and not fake
                        and self._state not in ("downloading", "installing"))
        return {
            "current": self.current,
            "frozen": self._frozen,
            "update_available": info is not None,
            "latest": info.version if info else None,
            "notes": info.notes if info else "",
            "html_url": info.html_url if info else "",
            "skipped": self._skipped(),
            "writable": writable,
            "state": self._state,
            "progress": self._progress,
        }

    # --- apply (off-thread; UI polls status for progress) ---
    def begin_apply(self, on_success) -> dict:
        info = self._check(force=False)
        if not self._frozen or info is None:
            return {"state": "error", "error": "no update available"}
        if os.environ.get("SM64_UPDATE_FAKE"):
            return {"state": "error", "error": "fake update"}
        with self._lock:
            if self._state in ("downloading", "installing"):
                return {"state": self._state}
            if not exe_dir_writable(self._exe.parent):
                return {"state": "error", "error": "exe folder not writable"}
            self._state = "downloading"
            self._progress = 0.0
        threading.Thread(target=self._run_apply, args=(info, on_success),
                         daemon=True, name="update-apply").start()
        return {"state": "downloading"}

    def _run_apply(self, info: "UpdateInfo", on_success) -> None:
        staged = None
        try:
            staged = download_and_stage(info, self._exe.parent, http=self._http,
                                        progress=self._set_progress)
            self._state = "installing"
            apply_update(staged, self._exe)
            on_success()
        except Exception:
            log.exception("update apply failed")
            if staged is not None:
                staged.unlink(missing_ok=True)
            self._state = "error"

    def _set_progress(self, frac: float) -> None:
        self._progress = max(0.0, min(1.0, frac))

    def cleanup_old_exe(self) -> None:
        """Background-reap the prior update's `.old` backup. Off-thread with
        bounded retries: right after a self-update restart the OLD process
        (running FROM the .old file) is often still tearing down and locking it,
        so one attempt at startup is too early — without retries the .old
        lingered until the NEXT launch, leaving a visible artifact next to the
        exe."""
        if not self._frozen:
            return
        exe = self._exe
        threading.Thread(target=lambda: cleanup_old(exe, attempts=60),
                         name="update-cleanup", daemon=True).start()
