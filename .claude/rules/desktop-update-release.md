---
paths:
  - "src/sm64_events/desktop/**"
  - "src/sm64_events/bootstrap/**"
  - "src/sm64_events/core/update_*.py"
  - "src/sm64_events/core/updater.py"
  - "src/sm64_events/core/release_feed.py"
  - "src/sm64_events/core/relaunch.py"
  - "src/sm64_events/core/version.py"
  - "gui_entry.py"
  - "bootstrap_entry.py"
  - "tools/build_exe.py"
  - "tools/make_manifest.py"
  - "tools/release.py"
  - "tools/rthook_comtypes.py"
---

# Desktop shell, self-update, build & release — where to change what

| To change... | Edit |
|---|---|
| Full-process restart primitives | `core/relaunch.py` — `server_alive`/`port_in_use`/`wait_port_free`/`spawn_replacement`; backs the one-click restart + the `SM64_RESTART` handoff (waits on real port bindability AND `instance_lock.wait_lock_free`, scrubs PyInstaller `_MEIPASS2`/`_PYI_*`) |
| Desktop GUI shell (window, tray, single-instance, server runner) | `desktop/` — additive wrapper over the SAME server/UI: `app.py` (composition + native takeover dialog + restart/quit wiring + `_repair_interrupted_update`), `server_runner.py` (uvicorn in a thread), `single_instance.py`, `window.py` (resizable pywebview + geometry), `tray.py`; entry `python -m sm64_events.desktop` / `gui_entry.py`. The desktop shell adds ONLY native chrome — it must never fork or special-case the UI (browser↔GUI parity) |
| One-command build (app + installer) | `tools/build_exe.py` (+ `tools/rthook_comtypes.py`, `assets/ukiki.ico`) — `--mode app` = PyInstaller **onedir** `dist/SM64Trainer/` (auto-bundles ffmpeg from PATH); `--mode bootstrap` = tiny onefile `dist/SM64TrainerSetup.exe` (entry `bootstrap_entry.py`); default `all`. Re-execs itself with `PYTHONHASHSEED=1` + `SOURCE_DATE_EPOCH=<HEAD %ct>` so unchanged files hash identically across releases (keeps update deltas small) |
| Runtime version constant | `core/version.py` — THE `__version__`; read by the app (update-check baseline), the build, and `tools/release.py` (which rewrites it). The frozen exe can't read pyproject, so this in-package constant is authoritative |
| Releases feed (version compare, patch-notes extraction) | `core/release_feed.py` — THE lower layer under `updater.py` (imports nothing from it; the reverse is an import cycle): `parse_version`/`is_newer`, shared `http_get`, and `strip_body` — the ONE rule covering both published body shapes (v1.4.0+ carry `PATCH_NOTES_MARKER`; v1.0.0–v1.3.0 are pure notes), which is why the popup can stack notes back to v1.0.0 |
| Update contracts (manifest schema, asset-name registry, plan diff) | `core/update_plan.py` — THE registry for release asset names (`ZIP_ASSET`/`MANIFEST_ASSET`/`BOOTSTRAP_ASSET`/`INSTALLED_MANIFEST`) + the per-file manifest format (sha256 + zip byte range) + `build_plan` (remote vs installed vs disk → fetch/delete/download_bytes; `verify_local` re-hash on FORCED checks only). Stdlib-only — the bootstrap imports it |
| Update download (Range fetch) | `core/update_fetch.py` — coalesced HTTP Range requests into the release zip (per-file sha verify on decode), `RangeUnsupported` → `fetch_full_zip` fallback (whole-zip digest + extract planned files), `free_disk_ok` |
| Update apply (crash-safe swap) | `core/update_apply.py` — journal written BEFORE any file op → rename originals into `.update_backup/` → move staged in → journal `done`; ANY failure/crash rolls back from journal+backup (idempotent); `startup_repair` at launch (rolled_back → single relaunch), `sweep_backup` reaps leftovers with bounded retries. Generalizes the rename-a-running-exe trick to N files |
| Self-update orchestrator | `core/updater.py` — `check_for_update` (needs ALL FOUR assets: zip+manifest+both `.sha256`s — no unverified bytes ever applies; also stacks `releases` = notes for EVERY version between installed and offered, newest first, via `release_feed.missed_releases` — best-effort) + `UpdateService` (cached check downloads+verifies the manifest and caches the PLAN; `status()` carries `download_bytes`; `begin_apply` = stage→fetch→apply→restart; `startup_maintenance` sweeps backups + deletes the migration bootstrap via `--cleanup-bootstrap`). Inert from source; `SM64_UPDATE_FAKE=1` renders the popup in dev |
| Bootstrap installer (migration vehicle + new-user setup) | `bootstrap/installer.py` (+ `bootstrap_entry.py`) — stdlib-only onefile exe published as the `SM64Trainer.exe` release asset FOREVER (old shipped updaters can only install that name): downloads the full zip, verifies, atomic-dir-swap installs to `%LOCALAPPDATA%\Programs\SM64Trainer`, writes `installed_manifest.json`, creates the Desktop shortcut, launches the app with `--cleanup-bootstrap <own path>`. Idempotent = repair tool; tk progress UI, `--silent` for automation |
| Release zip + manifest generator | `tools/make_manifest.py` — deterministic zip (sorted, 1980 timestamps) + manifest with per-entry data offsets read from LOCAL headers (central-dir extra can differ); test proves every recorded range slices+inflates back to the file |
| One-command release | `tools/release.py X.Y.Z` — preflight (main + clean tree + `gh` auth) → tests → bump `core/version.py`+pyproject → build `--mode all` → zip+manifest → SHA-256s → `gh release create` with SIX assets. **Build runs before any tag/push** so a broken build aborts cleanly. `--dry-run` builds + checksums only. Use the project `release` skill for the full flow incl. user-facing notes |

Live-instance reminder: the user's running app is the onedir install at
`%LOCALAPPDATA%\Programs\SM64Trainer\` (data/logs in `%LOCALAPPDATA%\SM64Trainer`);
the repo `data/` is dev-only. Fixes reach the user via release/in-app update.
