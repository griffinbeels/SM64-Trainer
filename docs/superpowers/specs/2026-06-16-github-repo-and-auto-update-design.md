# GitHub repo + in-app auto-update — Design

**Date:** 2026-06-16
**Status:** Approved (pending spec review)
**Repo:** https://github.com/griffinbeels/SM64-Trainer

## Goal

1. Publish this project to the new GitHub repo.
2. Let the packaged exe **auto-update**: on launch it checks GitHub for a newer
   release, shows an in-app popup with patch notes + a link, and on "Update"
   downloads the new exe, verifies it, swaps it in, and restarts — all without
   the user touching the filesystem.
3. Make publishing a new version a **one-command** local operation.

## Decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Release pipeline | **One-command local `tools/release.py`** | Keeps the build in the env where the native deps + ffmpeg already work; reliable; full control. (GitHub Actions CI rejected for now — the native-heavy onefile build is finicky on a fresh runner.) |
| Update trust model | **SHA-256 checksum** published beside the exe; updater verifies before swap | Catches corrupt/partial/tampered downloads. Free. |
| Code signing | **Not now** (documented as a future add) | Needs a paid cert or Azure Trusted Signing. Can be added later with no updater change. Only affects the *first* manual (browser) download via SmartScreen; in-app auto-updates don't hit SmartScreen. |
| First version | **v1.0.0** | The auto-updater landing is a reasonable 1.0 milestone. |
| Loose files | Move `sm64_tracker.psd` + `design_log.md` into `internal_notes/`, gitignore that dir | Organized locally, out of the public repo. |

## Why this is low-risk in this codebase

- **User state is separate from the exe.** Frozen, all state (DB, PBs, replays)
  lives in `%LOCALAPPDATA%\sm64_tracker` (`core/paths.py`), so replacing the exe
  never touches history. DB migrations already run on launch, so a newer exe
  upgrades the schema automatically.
- **Restart machinery already exists.** `core/relaunch.py` (`spawn_replacement`,
  `wait_port_free`) + `/api/admin/restart` already do a full-process relaunch
  with the `_MEI/_PYI` env scrub a onefile self-relaunch needs. The updater
  rides on exactly this.
- **WS broadcaster already exists** for pushing progress to the UI.

## Architecture

Five new single-purpose pieces:

| Piece | File | Responsibility |
|---|---|---|
| Version source of truth | `src/sm64_events/core/version.py` → `__version__` | Read by the app, the build, and the release script |
| Updater (pure, testable) | `src/sm64_events/core/updater.py` | Check GitHub, download, verify SHA-256, swap the exe, clean up old |
| Update REST surface | `src/sm64_events/server/update_api.py` | `GET /api/update/status`, `POST /api/update/apply`, `POST /api/update/skip` |
| Update popup | `src/sm64_events/ui/components/update.js` | Modal: version, patch notes, GitHub link, **Update / Skip / Later** |
| Release tool | `tools/release.py` | One command: bump → tag → build → checksum → publish |

The check runs **backend-side** (knows the running version, persists "skipped",
does the swap, avoids CORS). The UI renders status and calls the endpoints. Per
the project's browser↔GUI parity rule (domain rule 10) the popup lives in `ui/`
so it appears in **both** the browser tab and the desktop window with no native
special-casing. Self-update is **guarded on `is_frozen()`** — from source (dev)
it is a no-op so it never clobbers the working tree.

### Repo slug

`griffinbeels/SM64-Trainer` is a constant in `updater.py`, overridable via
`SM64_UPDATE_REPO` env (for tests). The GitHub API base is also overridable so
tests inject a fake.

## The self-replace mechanism (must be exactly right)

Windows won't let you *delete* a running `.exe`, but it **will** let you
*rename* one. That's the whole trick:

```
1. download  ->  <exedir>/sm64_tracker.new.exe   (verify SHA-256; abort on mismatch)
2. os.replace(sm64_tracker.exe      -> sm64_tracker.old.exe)   # move running exe aside
3. os.replace(sm64_tracker.new.exe  -> sm64_tracker.exe)       # new exe takes the canonical name
4. spawn_replacement()   # relaunches sys.executable (== canonical name == now the NEW exe),
                         #   tagged SM64_RESTART; old process waits-for-port then exits
5. on next startup: cleanup_old() deletes leftover *.old.exe (now unlocked)
```

Notes:
- `sys.executable` (captured at process start) is the canonical path string;
  after step 3 it points at the NEW exe, so `spawn_replacement()` launches the
  new exe with no change to `relaunch.py`.
- The old process keeps running from the renamed `.old.exe` (the OS tracks the
  open file object, not the path).
- `cleanup_old()` runs early in startup (desktop `app.py` / `main.py`), tolerant
  of a still-locked file (retry/next-launch).
- **Writability fallback:** before downloading, test-write a temp file in the
  exe's folder. If it fails (Program Files, synced/locked folder), skip the swap
  entirely; the popup shows a "Download from GitHub" button (the release
  `html_url`) instead of "Update now".
- Antivirus can momentarily lock the freshly-written exe → bounded retry on the
  renames.

## Updater module — `core/updater.py`

Pure functions with **injected HTTP** (urllib by default) so tests never touch
the network or a real exe:

- `current_version() -> str` — returns `__version__`.
- `parse_semver(tag) -> tuple` / `is_newer(a, b) -> bool` — strip leading `v`,
  compare numerically.
- `check_for_update(current, *, http, repo) -> UpdateInfo | None` — GET
  `…/repos/{repo}/releases/latest`; read `tag_name`, `body` (notes), `html_url`;
  locate the `sm64_tracker.exe` asset + its `.sha256`. Return info iff strictly
  newer. Best-effort: any error → `None` (no popup, no noise).
- `download_and_stage(info, exe_dir, *, http, progress=cb) -> Path` — stream to
  `sm64_tracker.new.exe`, compute SHA-256, raise on mismatch, return the path.
- `apply_update(staged, current_exe)` — the two `os.replace` renames (bounded
  retry).
- `cleanup_old(exe_dir)` — delete `*.old.exe`.
- `exe_dir_writable(exe_dir) -> bool` — the up-front fallback probe.

`UpdateInfo` is a small dataclass: `version, notes, html_url, asset_url,
sha256_url`.

## REST surface — `server/update_api.py`

- `GET /api/update/status` →
  `{current, latest, update_available, notes, html_url, skipped, writable,
    state}` where `state ∈ {idle, downloading, applying}` and a `progress`
  float when downloading. Does the GitHub check, **cached per-process** (and on
  a short TTL) so repeated UI polls don't burn the 60-req/hr unauthenticated
  limit. A `?force=1` bypasses the cache for the manual "Check for updates".
- `POST /api/update/apply` → 202; kicks the download+verify+swap+restart
  **off-thread** (mirrors `/api/admin/restart`). Progress is broadcast over the
  existing WS as `update_progress` events; on success it swaps and calls the
  same restart path the admin endpoint uses.
- `POST /api/update/skip` → persists `{skipped_version}` to
  `data/update_state.json` (via a new `paths.update_state_path()`; parallels
  `replay_settings.json`, keeps the updater DB-free).

All guarded on `is_frozen()`; from source they report `update_available: false`.

## UI — `ui/components/update.js`

- On load, `store.js` fetches `/api/update/status`. If `update_available && latest
  != skipped`, the modal mounts (in `app.js`, atop the normal UI).
- Shows: new version, **patch notes** rendered with a tiny built-in markdown
  pass (line breaks, `- ` bullets, `**bold**`, linkified URLs — no new
  dependency), and a prominent **"View on GitHub"** link (`html_url`).
- Buttons: **Update now** (`POST /apply`; swaps the modal body for a progress
  bar fed by the `update_progress` WS events, then the app auto-restarts) ·
  **Skip this version** (`POST /skip`) · **Later** (dismiss for this session
  only). If `!writable`, "Update now" is replaced by a "Download from GitHub"
  link.
- A manual **"Check for updates"** entry (re-fetches with `?force=1`) so the
  user is never stuck waiting for a relaunch.

## Release tool — `tools/release.py 1.1.0`

```
clean tree + on master + pytest green   (refuse otherwise)
  -> bump core/version.py (+ pyproject.toml [project].version) , commit
  -> git tag vX.Y.Z ; push commit + tag
  -> build dist/sm64_tracker.exe via tools/build_exe.py   (assert ffmpeg bundled)
  -> write dist/sm64_tracker.exe.sha256
  -> gh release create vX.Y.Z  (--generate-notes | --notes-file)  exe + .sha256
```

- **Notes** default to GitHub's auto-generated notes (commits/PRs since the last
  tag); `--notes-file` overrides for a curated changelog. The popup fetches the
  release body **live**, so editing the notes on GitHub afterward updates the
  popup with no rebuild.
- **Source downloads** (zip + tar.gz of the tag) are attached by GitHub to every
  release automatically — nothing to do.
- The script shells out to the **`gh` CLI** (a standalone script can't use the
  GitHub MCP tools; `gh` is the right tool here). It asserts `gh auth status`
  before starting.

## Error handling

| Failure | Behavior |
|---|---|
| GitHub unreachable / API error | `check_for_update` → `None`; no popup, no error spam |
| SHA-256 mismatch | abort before any rename; keep current exe; surface "download failed, try again / open GitHub" |
| Exe dir not writable | detected up front; popup offers browser download instead of in-app update |
| Rename briefly blocked (AV) | bounded retry; on give-up, keep current exe and report |
| Running from source | all endpoints report no update available (guarded on `is_frozen()`) |

The relaunch only happens **after both renames succeed**, so there is no
half-swapped state.

## Testing

- `tests/test_updater.py` — `parse_semver`/`is_newer`; `check_for_update` with an
  **injected fake HTTP** returning canned GitHub JSON (newer / older / equal /
  missing-asset / error); `download_and_stage` (good hash vs. bad hash → raises);
  `apply_update` on `tmp_path` temp files (rename chain + retry); `cleanup_old`;
  `exe_dir_writable`. No network, no real exe.
- `tests/test_update_api.py` — `/status` shape (frozen vs. source), `/skip`
  persistence round-trip, `/apply` dispatches off-thread (mirrors `test_app.py`
  admin tests) with a fake updater.
- `tests/test_release.py` — pure parts of `release.py`: version-bump rewrite of
  `version.py`, SHA-256 computation. The `gh`/build steps are integration,
  exercised by cutting the first real release.

## Concrete rollout steps

1. **Repo setup:** create `internal_notes/`, move `sm64_tracker.psd` +
   `design_log.md` into it, add `internal_notes/` to `.gitignore`. Add the
   `griffinbeels/SM64-Trainer` remote, push `master`. (`.gitignore` already
   protects `data/`, `replays/`, `logs/`, `dist/`, `build/`.)
2. **Build the feature** (updater + endpoints + popup + release tool + tests);
   `uv run pytest -q` green.
3. **First release:** set `__version__ = "1.0.0"`, run `tools/release.py 1.0.0`.
   The updater ships **inside** v1.0.0 so every downloader can auto-update from
   then on.
4. **Live-verify the loop:** cut a trivial v1.0.1, confirm an installed v1.0.0
   shows the popup, updates, and restarts onto v1.0.1 — with PBs/replays intact.

## Out of scope (noted for later)

- Code signing / SmartScreen removal (needs a cert).
- GitHub Actions CI build (revisit if local releases become a chore).
- Delta/partial updates (full-exe swap is fine for this size).
- Auto-update **rollback** beyond "keep current on failure".

## Module-map / docs updates owed on merge

- `CLAUDE.md` module map: rows for `core/version.py`, `core/updater.py`,
  `server/update_api.py`, `ui/components/update.js`, `tools/release.py`,
  `paths.update_state_path()`.
- `README.md`: the `/api/update/*` surface + how to cut a release.
- `docs/architecture.md`: the Windows running-exe rename trick (hard-won fact)
  with its evidence.
