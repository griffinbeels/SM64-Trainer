# src/sm64_events/core/updater.py
"""Self-update for the frozen onedir install: select the highest eligible
GitHub release, verify its manifest, diff it against installed files, download
ONLY what changed (core/update_fetch, HTTP Range into the release zip), and
swap files crash-safely (core/update_apply journaled rename dance). The
restart rides core/relaunch.spawn_replacement, same as always.

Pure helpers (check_for_update, exe_dir_writable) take an injected HTTP
opener and operate on explicit paths so tests never touch the network or a
real install. Version comparison, the shared request builder, and patch-notes
extraction live one layer down in core/release_feed.py. The stateful
UpdateService orchestrates them, caches the check (manifest + plan included),
tracks download progress, and persists beta preferences and skipped versions. Everything is
guarded on is_frozen(): from source it is inert (update_available is always
False) so a dev tree is never swapped.

The apply worker is RESILIENT rather than one-shot: every request carries a
timeout (NET_TIMEOUT_S) so a stalled socket fails instead of hanging forever,
and the whole download+swap is retried APPLY_ATTEMPTS times with backoff before
the UI is told anything failed — the one observed real-world failure was a
transient CDN timeout that the next click succeeded through.

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

from sm64_events.core import build_channel
from sm64_events.core.paths import is_frozen, update_state_path
from sm64_events.core.release_feed import ReleaseFeed, ReleaseNotes, http_get, notes_from_release
from sm64_events.core.release_contract import release_assets, select_release, version_key
from sm64_events.core.update_apply import (STAGING_DIR, apply_plan,
                                           confirm_startup, sweep_backup)
from sm64_events.core.update_policy import (adopt_channel_default, installed_floor, read_policy,
                                            record_installed, write_policy)
from sm64_events.core.update_fetch import (RangeUnsupported, fetch_full_zip,
                                           fetch_plan, free_disk_ok)
from sm64_events.core.update_plan import (INSTALLED_MANIFEST, MANIFEST_ASSET,
                                          ZIP_ASSET, Manifest, build_plan,
                                          parse_manifest, validate_release_manifest)

log = logging.getLogger("sm64.updater")

DEFAULT_REPO = "griffinbeels/SM64-Trainer"
GITHUB_API = "https://api.github.com"
_CHECK_TTL_S = 3600.0

# Neither urlopen nor http.client applies a timeout of its own, so a socket
# that connects and then stalls blocks the update worker FOREVER — worse than
# failing, because a hang can never be retried. Applies per blocking socket
# operation (the connect, and each read), not to the whole transfer.
NET_TIMEOUT_S = 30.0

# The failure seen in the wild was transient: a TLS handshake timeout to the
# release CDN mid-download (WinError 10060, live log 2026-08-01), and the very
# next click went through. So the worker retries the whole download+swap on its
# own and only reports failure once every attempt is spent. These are the waits
# BETWEEN attempts, so the attempt count is len(...)+1 by construction and the
# two can never disagree. Every failure is retriable: the only clearly-permanent
# one (no disk space) is re-checked at the top of each attempt and costs ~37s of
# progress bar, which is cheaper than the machinery to classify the rest.
RETRY_DELAYS_S = (2.0, 5.0, 10.0, 20.0)
APPLY_ATTEMPTS = len(RETRY_DELAYS_S) + 1


def default_opener(req):
    """The shipped `http`: urlopen WITH a timeout (see NET_TIMEOUT_S). Tests
    and the bootstrap inject their own opener; this is what ships."""
    return urllib.request.urlopen(req, timeout=NET_TIMEOUT_S)


class _RestartFailed(RuntimeError):
    """The files ARE swapped and only the relaunch failed — never retried."""


class _UnsafeApply(RuntimeError):
    """Player resumed during download; preserve the current application."""


@dataclass
class UpdateInfo:
    version: str
    notes: str          # the offered release alone; == releases[0].notes,
                        # enforced by construction (see check_for_update)
    html_url: str
    zip_url: str
    zip_sha_url: str
    manifest_url: str
    manifest_sha_url: str
    # Every version between the installed one and `version`, newest first.
    # releases[0] is ALWAYS the offered release itself (see check_for_update)
    # — never a feed row, so it can never disagree with `notes`. A tuple, not
    # a list, so the default needs no field(default_factory=…).
    releases: tuple[ReleaseNotes, ...] = ()
    prerelease: bool = False


def check_for_update(current: str, *, http=default_opener,
                     repo: str = DEFAULT_REPO,
                     api_base: str = GITHUB_API, include_beta: bool = False,
                     allow_same: bool = False, feed=None) -> "UpdateInfo | None":
    """Highest complete eligible forward release, with stable network fallback."""
    try:
        feed = feed or ReleaseFeed(http=http, repo=repo, api_base=api_base)
        rows = feed.releases()
        rel = select_release(rows, current, include_beta=include_beta, allow_same=allow_same)
        if rel is None:
            return None
        tag = rel["tag_name"]
        assets = release_assets(rel)
        offered = notes_from_release(rel)
        current_key, offered_key = version_key(current), version_key(tag)
        history = {}
        for row in rows:
            try:
                key = version_key(row.get("tag_name"))
                if (not row.get("draft") and not row.get("withdrawn")
                        and (include_beta or not key.prerelease)
                        and current_key < key < offered_key):
                    history[key] = notes_from_release(row)
            except (ValueError, TypeError):
                continue
        return UpdateInfo(
            version=tag.lstrip("vV"),
            notes=offered.notes,
            html_url=rel.get("html_url") or "",
            zip_url=assets[ZIP_ASSET],
            zip_sha_url=assets[ZIP_ASSET + ".sha256"],
            manifest_url=assets[MANIFEST_ASSET],
            manifest_sha_url=assets[MANIFEST_ASSET + ".sha256"],
            # `offered` always heads the stack, so releases[0] and `notes`
            # cannot disagree — even when the history fetch is empty/stale.
            releases=(offered, *(history[key] for key in sorted(history, reverse=True))),
            prerelease=bool(offered_key.prerelease))
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
                 http=default_opener,
                 state_path: "Path | None" = None,
                 frozen: "bool | None" = None,
                 retry_delays: "tuple[float, ...]" = RETRY_DELAYS_S):
        self._build = build_channel.current_build()
        self._enabled = self._build.updates_enabled
        self.current = current_version
        self.repo = repo
        self._http = http
        self._frozen = is_frozen() if frozen is None else frozen
        self._exe = Path(exe_path) if exe_path else Path(sys.executable)
        self._state_path = state_path or update_state_path()
        self._lock = threading.Lock()
        self._check_lock = threading.RLock()
        self._feed = ReleaseFeed(http=http, repo=repo, api_base=GITHUB_API)
        self._state = "idle"        # idle | downloading | installing | error
        self._apply_stage = "idle"
        self._progress = 0.0
        # Injected so a test can drive all five attempts without sleeping.
        self._retry_delays = tuple(retry_delays)
        self._attempts = len(self._retry_delays) + 1
        self._attempt = 1           # 1-based; which attempt is in flight
        self._cache: "UpdateInfo | None" = None
        self._manifest: "Manifest | None" = None
        self._manifest_text: "str | None" = None
        self._plan = None           # UpdatePlan for the cached release
        self._checked_at = 0.0      # monotonic of last real check; 0 = never
        self._writable: "bool | None" = None   # probed once, then cached
        self._repair = False
        self._error = ""
        self._files_changed = False
        if self._enabled and self._frozen:
            try:
                record_installed(self._state_path, self.current)
            except ValueError:
                # Packaged diagnostic probes use a deliberately synthetic version.
                pass
            try:
                adopt_channel_default(self._state_path, self._build.channel)
            except (OSError, ValueError):
                log.warning("beta update preference could not be recorded", exc_info=True)

    def beta_preferences(self) -> dict:
        policy = read_policy(self._state_path)
        try:
            floor = installed_floor(self._state_path, self.current)
        except ValueError:
            floor = self.current
        return {"receive_beta_updates": policy["receive_beta_updates"],
                "installed_version": floor, "updates_enabled": self._enabled}

    def set_beta_updates(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise ValueError("beta preference must be a boolean")
        with self._check_lock:
            if self._state in ("downloading", "installing"):
                raise ValueError("wait for the current update to finish")
            write_policy(self._state_path, receive_beta_updates=enabled, beta_choice="player")
            self._cache = self._manifest = self._manifest_text = self._plan = None
            self._checked_at = 0.0
        return self.status(force=True)

    # --- skipped-version overlay ---
    def _skipped(self) -> "str | None":
        try:
            return json.loads(self._state_path.read_text()).get("skipped")
        except Exception:
            return None

    def skip(self, version: str) -> None:
        if not self._enabled:
            return
        version_key(version)
        write_policy(self._state_path, skipped=version)

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
            data = r.read((16 << 20) + 1)
            if len(data) > (16 << 20):
                raise ValueError("update metadata exceeds size limit")
            return data.decode("utf-8")

    def _read_installed(self) -> "Manifest | None":
        try:
            return parse_manifest(
                (self._exe.parent / INSTALLED_MANIFEST).read_text())
        except (OSError, ValueError):
            return None   # fresh/damaged record -> plan refetches everything

    def _check(self, force: bool, repair: bool = False) -> "UpdateInfo | None":
        requested_at = time.monotonic()
        with self._check_lock:
            if force and repair == self._repair and self._checked_at >= requested_at:
                return self._cache  # another launch/login check completed for us
            return self._check_once(force, repair)

    def _check_once(self, force: bool, repair: bool) -> "UpdateInfo | None":
        if not self._enabled:
            return None
        fake = self._fake()
        if fake is not None:
            return fake
        if not self._frozen:
            return None
        now = time.monotonic()
        if self._state in ("downloading", "installing"):
            return self._cache
        if not force and repair == self._repair and self._checked_at and (now - self._checked_at) < _CHECK_TTL_S:
            return self._cache
        self._cache = None
        self._error = ""
        self._manifest = self._manifest_text = self._plan = None
        self._repair = repair
        preferences = self.beta_preferences()
        info = check_for_update(preferences["installed_version"], http=self._http, repo=self.repo,
                                include_beta=preferences["receive_beta_updates"],
                                allow_same=repair, feed=self._feed)
        if info is None:
            self._error = self._feed.last_error
        if info is not None:
            try:
                text = self._fetch_text(info.manifest_url)
                published = self._fetch_text(info.manifest_sha_url).split()
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if not published or published[0].strip().lower() != digest:
                    raise ValueError("manifest checksum mismatch")
                remote = parse_manifest(text)
                validate_release_manifest(remote, info.version)
                installed = self._read_installed()
                # A FORCED check ('Check for updates' button) re-hashes every
                # local file so silent corruption self-heals via re-download;
                # the routine hourly check stays cheap (existence+size).
                self._plan = build_plan(remote, installed, self._exe.parent,
                                        verify_local=force or repair)
                self._manifest, self._manifest_text = remote, text
                self._cache = info
            except Exception:
                log.warning("update manifest rejected", exc_info=True)
                self._error = "The update package could not be verified. Try checking again."
        self._checked_at = time.monotonic()
        return self._cache

    def status(self, force: bool = False, repair: bool = False) -> dict:
        preferences = self.beta_preferences()
        if not self._enabled:
            return {
                "current": self.current, "frozen": self._frozen,
                "channel": self._build.channel, "build_id": self._build.build_id,
                "updates_enabled": False,
                "disabled_reason": "This legacy private beta uses a separate installer.",
                **preferences, "repair": False, "error": self._error, "files_changed": False,
                "update_available": False, "latest": None, "notes": "", "html_url": "",
                "skipped": None, "writable": False, "state": "disabled", "progress": 0.0,
                "attempt": 0, "attempts": 0, "download_bytes": None, "releases": [],
            }
        # Ordinary progress polls keep an explicit repair offer visible.
        # A fresh normal check intentionally leaves repair mode.
        info = self._check(force, repair or (self._repair and not force))
        fake = bool(os.environ.get("SM64_UPDATE_FAKE"))
        if self._writable is None and self._frozen and info is not None and not fake:
            self._writable = exe_dir_writable(self._exe.parent)
        writable = bool(self._writable and not fake
                        and self._state not in ("downloading", "installing"))
        return {
            "current": self.current,
            "channel": self._build.channel, "build_id": self._build.build_id,
            **preferences, "repair": self._repair, "error": self._error,
            "files_changed": self._files_changed,
            "frozen": self._frozen,
            "update_available": info is not None,
            "latest": info.version if info else None,
            "notes": info.notes if info else "",
            "html_url": info.html_url if info else "",
            "skipped": self._skipped(),
            "writable": writable,
            "state": self._state,
            "progress": self._progress,
            # The popup explains a progress bar that restarts, and names the
            # count in the failure copy. Silent retries would read as a stall.
            "attempt": self._attempt,
            "attempts": self._attempts,
            "download_bytes": self._plan.download_bytes if self._plan else None,
            "releases": [{"version": row.version, "date": row.date,
                          "notes": row.notes}
                         for row in info.releases] if info else [],
        }

    # --- apply (off-thread; UI polls status for progress) ---
    def begin_apply(self, on_success, *, repair: bool = False, can_apply=None) -> dict:
        with self._check_lock:
            return self._begin_apply(on_success, repair=repair, can_apply=can_apply)

    def _begin_apply(self, on_success, *, repair: bool, can_apply) -> dict:
        if not self._enabled:
            return {"state": "error", "error": "public updates are disabled for this private beta"}
        if self._files_changed:
            return {"state": "error", "error": "Relaunch the trainer to finish the installed update."}
        if can_apply and (reason := can_apply()):
            return {"state": "error", "error": reason}
        info = self._check(force=False, repair=repair)
        if not self._frozen or info is None or self._plan is None:
            return {"state": "error", "error": "no update available"}
        if os.environ.get("SM64_UPDATE_FAKE"):
            return {"state": "error", "error": "fake update"}
        with self._lock:
            if self._state in ("downloading", "installing"):
                return {"state": self._state}
            if not exe_dir_writable(self._exe.parent):
                return {"state": "error", "error": "exe folder not writable"}
            # Preferences may have changed since the popup was displayed.
            if not self._eligible_apply(info, repair=repair):
                return {"state": "error", "error": "beta updates are disabled"}
            self._state = "downloading"
            self._error = ""
            self._progress = 0.0
            self._attempt = 1       # also resets after a failed run's "Retry"
        threading.Thread(
            target=self._run_apply,
            args=(info, self._plan, self._manifest_text, on_success, can_apply),
            daemon=True, name="update-apply").start()
        return {"state": "downloading"}

    def _eligible_apply(self, info, *, repair: bool) -> bool:
        preferences = self.beta_preferences()
        return (not info.prerelease or preferences["receive_beta_updates"] or
                (repair and version_key(info.version) == version_key(preferences["installed_version"])))

    def _run_apply(self, info, plan, manifest_text, on_success, can_apply=None) -> None:
        """Attempt the update up to `self._attempts` times, sleeping
        `self._retry_delays` in between, and only report 'error' once every
        attempt is spent (see RETRY_DELAYS_S)."""
        for attempt in range(1, self._attempts + 1):
            self._attempt = attempt
            try:
                self._apply_once(info, plan, manifest_text, on_success, can_apply)
                return
            except (_RestartFailed, _UnsafeApply) as error:
                self._error = str(error) or "The update was installed. Relaunch the trainer to finish."
                self._state = "error"
                return
            except Exception as error:
                context = self._failure_context(info, plan, error)
                if attempt == self._attempts:
                    log.exception("update apply failed after %d attempts",
                                  attempt, extra=context)
                    self._state = "error"
                    self._error = str(error)
                    return
                delay = self._retry_delays[attempt - 1]
                # A player can close the app during retries. Report the first
                # failed attempt now; terminal failure has its own ERROR log.
                if attempt == 1:
                    from sm64_events.core.error_reporting import capture_problem
                    # A distinct message keeps this bounded retry report from
                    # deduplicating the later terminal exception for an hour.
                    capture_problem("Automatic update attempt failed; retrying", context=context)
                log.warning("update attempt %d/%d failed at %s (%s -> %s); retrying in %.0fs",
                            attempt, self._attempts, context["stage"], self.current,
                            info.version, delay, exc_info=True, extra=context)
                self._state = "downloading"
                self._progress = 0.0
                time.sleep(delay)

    def _failure_context(self, info, plan, error):
        """Bounded operational evidence; no request URLs, headers or file paths."""
        context = dict(component="updater", operation="apply", stage=self._apply_stage,
                       error_type=type(error).__name__, update={
                           "current_version": self.current, "target_version": info.version,
                           "attempt": self._attempt, "attempts": self._attempts,
                           "download_bytes": plan.download_bytes})
        for key, field in (("status_code", "code"), ("errno", "errno"), ("winerror", "winerror")):
            value = getattr(error, field, None)
            if type(value) is int:
                context[key] = value
        return context

    def _apply_once(self, info, plan, manifest_text, on_success, can_apply=None) -> None:
        """One attempt: stage -> fetch -> verify -> swap -> restart. Raises on
        any failure so the caller can retry; a failure AFTER the swap becomes
        _RestartFailed, which must not be retried."""
        root = self._exe.parent
        staging = root / STAGING_DIR
        applied = False
        try:
            self._apply_stage = "staging"
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            needed = sum(entry.size for entry in plan.fetch)
            if not free_disk_ok(root, needed):
                raise RuntimeError("not enough free disk space for the update")
            try:
                self._apply_stage = "range_download"
                fetch_plan(plan, info.zip_url, staging, http=self._http,
                           progress=self._set_progress)
            except RangeUnsupported as error:
                log.info("ranged download unavailable — full zip fallback")
                self._apply_stage = "full_checksum"
                published = self._fetch_text(info.zip_sha_url).split()
                if not published:
                    raise ValueError("zip sha256 file is empty") from error
                self._apply_stage = "full_download"
                fetch_full_zip(plan, info.zip_url, published[0], staging,
                               http=self._http, progress=self._set_progress)
            # The verified manifest text becomes the new installed record,
            # swapped in atomically WITH the files it describes.
            self._apply_stage = "manifest_stage"
            (staging / INSTALLED_MANIFEST).write_text(manifest_text)
            if can_apply and (reason := can_apply()):
                raise _UnsafeApply(reason)
            if not self._eligible_apply(info, repair=self._repair):
                raise _UnsafeApply("Beta updates were disabled during download.")
            self._state = "installing"
            self._apply_stage = "swap"
            apply_plan(root, staging,
                       replace=[entry.path for entry in plan.fetch]
                       + [INSTALLED_MANIFEST],
                       delete=list(plan.delete))
            applied = True
            self._files_changed = True
            self._apply_stage = "record_installed"
            record_installed(self._state_path, info.version)
            shutil.rmtree(staging, ignore_errors=True)
            self._apply_stage = "restart"
            on_success()
        except Exception as err:
            shutil.rmtree(staging, ignore_errors=True)
            if applied:
                # Files ARE swapped (apply_plan succeeded) — only the restart
                # failed. Retrying would re-download and re-apply what is
                # already installed, so this one never goes round the loop.
                # The popup's "your current version is unchanged" is
                # inaccurate for this corner; the new version simply runs on
                # the next manual launch (final review, minor 6).
                log.exception("update applied but the restart failed — the "
                              "new version runs on next launch",
                              extra=self._failure_context(info, plan, err))
                raise _RestartFailed from err
            raise

    def _set_progress(self, frac: float) -> None:
        self._progress = max(0.0, min(1.0, frac))

    def startup_maintenance(self, bootstrap_path: "str | None" = None) -> None:
        """Background-reap update leftovers: the backup tree + finished
        journal from the last apply, and (post-migration) the bootstrap
        installer file + its .old sibling on whatever path the bootstrap
        handed us via --cleanup-bootstrap. Bounded retries: the previous
        process may still be exiting and holding locks."""
        if not self._enabled or not self._frozen:
            return
        root = self._exe.parent
        # Confirm the journal belonging to this healthy launch before the
        # background thread can be delayed behind another completed update.
        confirm_startup(root)

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
