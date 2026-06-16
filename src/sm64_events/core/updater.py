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
EXE_NAME = "sm64_tracker.exe"
_UA = "sm64_tracker-updater"
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
        return UpdateInfo(
            version=tag.lstrip("vV"),
            notes=rel.get("body") or "",
            html_url=rel.get("html_url") or "",
            asset_url=asset_url,
            sha256_url=assets.get(EXE_NAME + ".sha256") or "")
    except Exception:
        log.info("update check failed", exc_info=True)
        return None


def download_and_stage(info: "UpdateInfo", exe_dir: Path, *,
                       http=urllib.request.urlopen, progress=None) -> Path:
    """Stream the new exe to <exe_dir>/sm64_tracker.exe.new, verify SHA-256
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
            published = r.read().decode("utf-8").split()[0].strip().lower()
        if published and published != h.hexdigest():
            staged.unlink(missing_ok=True)
            raise ValueError("update checksum mismatch")
    return staged


def apply_update(staged: Path, current_exe: Path, *, retries: int = 5,
                 sleep=time.sleep) -> None:
    """Swap `staged` in for the running exe via two renames (Windows allows
    renaming a running exe). Bounded retry: AV can briefly lock the new file."""
    old = current_exe.parent / (current_exe.name + ".old")
    for attempt in range(retries):
        try:
            old.unlink(missing_ok=True)
            os.replace(current_exe, old)
            os.replace(staged, current_exe)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            sleep(0.5)


def cleanup_old(exe_dir: Path) -> None:
    """Delete a leftover *.old from a prior update (now unlocked)."""
    for p in exe_dir.glob("*.old"):
        try:
            p.unlink()
        except OSError:
            pass  # still locked (rare) -> retry next launch


def exe_dir_writable(exe_dir: Path) -> bool:
    probe = exe_dir / ".sm64_update_probe"
    try:
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False
