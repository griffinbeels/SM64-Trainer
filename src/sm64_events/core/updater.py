# src/sm64_events/core/updater.py
"""Self-update for the frozen onedir install: check the GitHub 'latest'
release, verify its manifest, diff it against the installed files, download
ONLY what changed (core/update_fetch, HTTP Range into the release zip), and
swap files crash-safely (core/update_apply journaled rename dance). The
restart rides core/relaunch.spawn_replacement, same as always.

Pure helpers (check_for_update, exe_dir_writable) take an injected HTTP
opener and operate on explicit paths so tests never touch the network or a
real install. Version comparison, the shared request builder, and patch-notes
extraction live one layer down in core/release_feed.py. The stateful
UpdateService orchestrates
them, caches the check (manifest + plan included), tracks download progress,
and persists the 'skipped' version. Everything is guarded on is_frozen():
from source it is inert (update_available is always False) so a dev tree is
never swapped.

A release is only ever offered when ALL FOUR update assets are present
(SM64Trainer-full.zip + manifest.json + both .sha256 companions) and the
manifest text verifies against its published digest — no unverified bytes
are ever applied. The old single-exe swap path (download_and_stage /
apply_update / cleanup_old) is gone: already-shipped onefile versions
migrate through the bootstrap installer published as the SM64Trainer.exe
asset (see bootstrap/installer.py)."""
import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sm64_events.core.paths import is_frozen, update_state_path
from sm64_events.core.update_apply import STAGING_DIR, apply_plan, sweep_backup
from sm64_events.core.update_fetch import (RangeUnsupported, fetch_full_zip,
                                           fetch_plan, free_disk_ok)
from sm64_events.core.release_feed import (ReleaseNotes, http_get, is_newer,
                                           missed_releases, notes_from_release)
from sm64_events.core.update_plan import (INSTALLED_MANIFEST, MANIFEST_ASSET,
                                          ZIP_ASSET, Manifest, build_plan,
                                          parse_manifest)

log = logging.getLogger("sm64.updater")

DEFAULT_REPO = "griffinbeels/SM64-Trainer"
GITHUB_API = "https://api.github.com"
_CHECK_TTL_S = 3600.0


@dataclass
class UpdateInfo:
    version: str
    notes: str          # the offered release alone (= releases[0].notes)
    html_url: str
    zip_url: str
    zip_sha_url: str
    manifest_url: str
    manifest_sha_url: str
    # Every version between the installed one and `version`, newest first.
    # A tuple, not a list, so the default needs no field(default_factory=…).
    releases: tuple[ReleaseNotes, ...] = ()


def check_for_update(current: str, *, http=urllib.request.urlopen,
                     repo: str = DEFAULT_REPO,
                     api_base: str = GITHUB_API) -> "UpdateInfo | None":
    """GET the latest release; return UpdateInfo iff it is strictly newer AND
    carries the zip + manifest assets WITH their .sha256 companions (the
    'no unverified bytes ever applied' rule). Best-effort: any error ->
    None (no popup)."""
    try:
        url = f"{api_base}/repos/{repo}/releases/latest"
        with http_get(http, url, accept="application/vnd.github+json") as r:
            rel = json.loads(r.read().decode("utf-8"))
        tag = rel.get("tag_name") or ""
        if not is_newer(tag, current):
            return None
        assets = {a.get("name"): a.get("browser_download_url")
                  for a in rel.get("assets", [])}
        needed = (ZIP_ASSET, ZIP_ASSET + ".sha256",
                  MANIFEST_ASSET, MANIFEST_ASSET + ".sha256")
        if not all(assets.get(name) for name in needed):
            log.info("release %s is missing update assets; not offering", tag)
            return None
        # The body's leading first-time-setup section is for the GitHub page
        # only, and legacy bodies title themselves — release_feed.notes_from_release
        # (via strip_body) is THE rule for both shapes.
        offered = notes_from_release(rel)
        # Everything the user skipped, newest first. Clamped to the offered
        # tag: GitHub's 'latest' is the most RECENT publish, so a backport
        # published afterwards could otherwise stack notes for a version this
        # update does not install. Empty (history unavailable, or a lone
        # release) -> the offered release alone, exactly as before.
        history = [row for row in missed_releases(current, http=http,
                                                  repo=repo, api_base=api_base)
                   if not is_newer(row.version, tag)]
        return UpdateInfo(
            version=tag.lstrip("vV"),
            notes=offered.notes,
            html_url=rel.get("html_url") or "",
            zip_url=assets[ZIP_ASSET],
            zip_sha_url=assets[ZIP_ASSET + ".sha256"],
            manifest_url=assets[MANIFEST_ASSET],
            manifest_sha_url=assets[MANIFEST_ASSET + ".sha256"],
            releases=tuple(history) if history else (offered,))
    except Exception:
        log.info("update check failed", exc_info=True)
        return None


def exe_dir_writable(exe_dir: Path) -> bool:
    probe = exe_dir / ".sm64_update_probe"
    try:
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False


class UpdateService:
    """Stateful orchestrator the REST layer talks to: caches the check
    (release info + verified manifest + computed plan), tracks download
    progress/state, persists the skipped version. Inert unless frozen.

    SM64_UPDATE_FAKE=1 makes status() report a synthetic update (writable
    forced False, so the only action is the GitHub link) — lets the popup be
    verified in dev WITHOUT cutting a real release."""

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
        self._manifest: "Manifest | None" = None
        self._manifest_text: "str | None" = None
        self._plan = None           # UpdatePlan for the cached release
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
        # THREE releases so SM64_UPDATE_FAKE=1 renders the stacked layout —
        # version headers, dividers, and the inner scroll — without cutting
        # a real release.
        rows = (
            ReleaseNotes(
                "9.9.9", "2026-07-23",
                "## Demo release\n"
                "- **New:** a sample bullet whose text is long enough to wrap\n"
                "  onto a second source line, exercising the soft-wrap join.\n"
                "- A second bullet that mentions the `.old` backup as code.\n"
                "\n"
                "A trailing paragraph after a blank line, also wrapping across\n"
                "two source lines, to confirm paragraphs join too."),
            ReleaseNotes(
                "9.9.8", "2026-07-20",
                "- **Fix:** the middle version of the stack, here so the\n"
                "  version headers and dividers can be eyeballed in dev."),
            ReleaseNotes(
                "9.9.7", "2026-07-18",
                "- **New:** the oldest missed version, proving the popup\n"
                "  stacks every skipped release rather than just the newest."),
        )
        return UpdateInfo(
            version="9.9.9",
            notes=rows[0].notes,
            html_url=f"https://github.com/{self.repo}/releases",
            zip_url="", zip_sha_url="", manifest_url="", manifest_sha_url="",
            releases=rows)

    def _fetch_text(self, url: str) -> str:
        with http_get(self._http, url) as r:
            return r.read().decode("utf-8")

    def _read_installed(self) -> "Manifest | None":
        try:
            return parse_manifest(
                (self._exe.parent / INSTALLED_MANIFEST).read_text())
        except (OSError, ValueError):
            return None   # fresh/damaged record -> plan refetches everything

    def _check(self, force: bool) -> "UpdateInfo | None":
        fake = self._fake()
        if fake is not None:
            return fake
        if not self._frozen:
            return None
        now = time.monotonic()
        if not force and self._checked_at and (now - self._checked_at) < _CHECK_TTL_S:
            return self._cache
        self._cache = None
        self._manifest = self._manifest_text = self._plan = None
        info = check_for_update(self.current, http=self._http, repo=self.repo)
        if info is not None:
            try:
                text = self._fetch_text(info.manifest_url)
                published = self._fetch_text(info.manifest_sha_url).split()
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if not published or published[0].strip().lower() != digest:
                    raise ValueError("manifest checksum mismatch")
                remote = parse_manifest(text)
                installed = self._read_installed()
                # A FORCED check ('Check for updates' button) re-hashes every
                # local file so silent corruption self-heals via re-download;
                # the routine hourly check stays cheap (existence+size).
                self._plan = build_plan(remote, installed, self._exe.parent,
                                        verify_local=force)
                self._manifest, self._manifest_text = remote, text
                self._cache = info
            except Exception:
                log.warning("update manifest rejected", exc_info=True)
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
            "download_bytes": self._plan.download_bytes if self._plan else None,
            "releases": [{"version": row.version, "date": row.date,
                          "notes": row.notes}
                         for row in info.releases] if info else [],
        }

    # --- apply (off-thread; UI polls status for progress) ---
    def begin_apply(self, on_success) -> dict:
        info = self._check(force=False)
        if not self._frozen or info is None or self._plan is None:
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
        threading.Thread(
            target=self._run_apply,
            args=(info, self._plan, self._manifest_text, on_success),
            daemon=True, name="update-apply").start()
        return {"state": "downloading"}

    def _run_apply(self, info, plan, manifest_text, on_success) -> None:
        root = self._exe.parent
        staging = root / STAGING_DIR
        applied = False
        try:
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            needed = sum(entry.size for entry in plan.fetch)
            if not free_disk_ok(root, needed):
                raise RuntimeError("not enough free disk space for the update")
            try:
                fetch_plan(plan, info.zip_url, staging, http=self._http,
                           progress=self._set_progress)
            except RangeUnsupported:
                log.info("ranged download unavailable — full zip fallback")
                published = self._fetch_text(info.zip_sha_url).split()
                if not published:
                    raise ValueError("zip sha256 file is empty")
                fetch_full_zip(plan, info.zip_url, published[0], staging,
                               http=self._http, progress=self._set_progress)
            # The verified manifest text becomes the new installed record,
            # swapped in atomically WITH the files it describes.
            (staging / INSTALLED_MANIFEST).write_text(manifest_text)
            self._state = "installing"
            apply_plan(root, staging,
                       replace=[entry.path for entry in plan.fetch]
                       + [INSTALLED_MANIFEST],
                       delete=list(plan.delete))
            shutil.rmtree(staging, ignore_errors=True)
            applied = True
            on_success()
        except Exception:
            if applied:
                # Files ARE swapped (apply_plan succeeded) — only the restart
                # failed. The popup's "your current version is unchanged" is
                # inaccurate for this corner; the new version simply runs on
                # the next manual launch (final review, minor 6).
                log.exception("update applied but the restart failed — the "
                              "new version runs on next launch")
            else:
                log.exception("update apply failed")
            shutil.rmtree(staging, ignore_errors=True)
            self._state = "error"

    def _set_progress(self, frac: float) -> None:
        self._progress = max(0.0, min(1.0, frac))

    def startup_maintenance(self, bootstrap_path: "str | None" = None) -> None:
        """Background-reap update leftovers: the backup tree + finished
        journal from the last apply, and (post-migration) the bootstrap
        installer file + its .old sibling on whatever path the bootstrap
        handed us via --cleanup-bootstrap. Bounded retries: the previous
        process may still be exiting and holding locks."""
        if not self._frozen:
            return
        root = self._exe.parent

        def work():
            sweep_backup(root, attempts=60)
            if bootstrap_path:
                for leftover in (Path(bootstrap_path),
                                 Path(bootstrap_path + ".old")):
                    for _ in range(60):
                        try:
                            leftover.unlink(missing_ok=True)
                            break
                        except OSError:
                            time.sleep(1.0)
        threading.Thread(target=work, daemon=True,
                         name="update-maintenance").start()
