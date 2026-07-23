# Incremental Updates — Design Spec

**Date:** 2026-07-23
**Status:** Approved design, pending implementation plan
**Supersedes:** the onefile full-exe self-update flow in `core/updater.py` (v1.0.x–v1.3.x)

## Problem

Every release currently re-downloads the entire 220 MB onefile exe, even for a
one-line fix. There are no "files" to update selectively — the whole app
(Python runtime, ffmpeg, numpy, PyAV, webview, ~5 MB of our code) is fused
into a single PyInstaller onefile binary. Goals:

1. Updates download **only what changed** (typically ~10–30 MB, not 220 MB).
2. User data (db, PBs, replays, settings) is never touched — already true
   (data lives in `%LOCALAPPDATA%\SM64Trainer`, separate from the program),
   and must stay true.
3. The user keeps **one Desktop shortcut forever**; updates are invisible
   file swaps underneath a stable exe path.
4. Keep the existing popup UX (notes + Update/Skip/Later + progress) and the
   existing "no unverified bytes ever applied" SHA-256 discipline.

## Decisions (made with the user, 2026-07-23)

| Decision | Choice |
|---|---|
| Packaging | PyInstaller **onedir** folder (was onefile) |
| Install location | `%LOCALAPPDATA%\Programs\SM64Trainer` (per-user, no admin); app remains movable — updater always works relative to its own exe |
| Update UX | Keep the current popup flow (explicit consent before download) |
| Delta mechanism | Hand-rolled **per-file manifest sync** with HTTP Range fetch from the release zip (approach A below) |

## Prior Art

- **Canonical name(s):** *differential / delta updates*; *manifest-based file
  sync*; *block-based differential download*; *binary diff patching*.
- **Landscape:**
  - *Full-file replacement* — download the whole new artifact (our current
    system; most small indie tools).
  - *Per-file manifest sync* — per-file hashes in a manifest; client diffs
    local vs remote, downloads only changed files (ClickOnce, Chrome's
    component updater, MSIX AppxBlockMap at 64 KB-block granularity, Steam
    depots via content-addressed chunks).
  - *Chunked differential download over HTTP Range* — hash chunks of one big
    asset, Range-request only changed spans (electron-updater `.blockmap`
    against GitHub Releases — production-proven for thousands of Electron
    apps; zsync/AppImageUpdate; casync).
  - *Binary diff between version pairs* — bsdiff/zstd patch old→new
    (Chrome Courgette→Zucchini, Sparkle on macOS, tufup for Python apps,
    Velopack's per-file zstd deltas).
  - *Versioned dirs + launcher stub* — Squirrel.Windows/Velopack layout with
    an `Update.exe` shim and a stable `current/` path.
- **Dominant pattern(s):** for GitHub-Releases-hosted desktop apps, chunk- or
  file-level differential download (electron-updater) dominates; for
  framework-managed apps, Velopack's delta packages. Manifest sync is the
  standard where the artifact is naturally many files (our onedir case).
- **Real examples:** electron-builder differential updates
  (github.com/electron-userland/electron-builder issues #7023, #6198 —
  confirms GitHub release assets serve HTTP Range requests);
  Velopack delta docs (docs.velopack.io/packaging/deltas); tufup
  (github.com/dennisvang/tufup) — bsdiff of uncompressed tars.
- **Failed attempts:** PyUpdater — archived/unmaintained; Squirrel.Windows —
  effectively abandoned, superseded by Velopack. Whole-archive compressed
  diffs (early tufup) produced bloated patches — fixed by diffing
  *uncompressed* archives; lesson: never diff through a compression layer.
- **Known gotchas (from practitioners):**
  - Many small Range requests can be slower than one full download when many
    blocks changed → coalesce ranges; fall back to full download past a
    threshold.
  - PyInstaller builds are not byte-reproducible by default (`PYTHONHASHSEED`
    randomizes pyc/PYZ content) → set `PYTHONHASHSEED` + `SOURCE_DATE_EPOCH`
    at build time so unchanged code hashes identically across releases.
  - Some proxies/CDNs mishandle Range → always keep a full-download fallback
    (electron-updater does the same).
- **Verdict on our starting idea:** the user's instinct ("only install what's
  new") is exactly the industry-dominant pattern; the blocker was onefile
  packaging, which we change.
- **Divergences & why:** we hand-roll instead of adopting Velopack — its
  Python-SDK-with-GitHub path has open issues (velopack/velopack #726), it
  adds a .NET build dep + native runtime binding + framework-owned install
  layout/restart, and our existing updater already owns tested seams
  (injectable http, staged swap, restart handoff) that extend naturally.
  tufup passed over for TUF key ceremony + patch-chain model (version
  skippers pay full price; manifest sync is version-skip free).

## Design

### 1. Distribution & layout

- `tools/build_exe.py` builds PyInstaller **onedir**: `SM64Trainer.exe` +
  `_internal\` (runtime, deps, ffmpeg, ui tree, seeds). `sys._MEIPASS`
  resolves to `_internal`, so `bundled_ffmpeg()` / `bundled_rank_standards()`
  keep working unchanged.
- Install root = the directory containing the running exe (movable/portable);
  canonical location `%LOCALAPPDATA%\Programs\SM64Trainer` is set by the
  installer, never assumed by the updater.
- Data root is untouched: `%LOCALAPPDATA%\SM64Trainer` (existing
  `core/paths.py` logic, keyed on `is_frozen()`, unchanged).
- Build sets `PYTHONHASHSEED=1` and `SOURCE_DATE_EPOCH=<HEAD commit
  timestamp>` for cross-release stability of unchanged files.
- Implementation must **measure** the actual volatile set (build two releases
  from adjacent commits, diff manifests) to validate the ~10–30 MB typical
  update estimate; record the result in architecture.md.

### 2. Release artifacts (per release, uploaded by `tools/release.py`)

| Asset | Purpose |
|---|---|
| `SM64Trainer-full.zip` | The whole onedir tree; first installs + fallback |
| `SM64Trainer-full.zip.sha256` | Integrity of the zip |
| `manifest.json` | Per-file: relative path, SHA-256 of uncompressed content, size, and the exact byte range (offset + compressed size + compression method) of that entry's data inside the zip, computed from the zip's **local headers** at release time; plus release version |
| `manifest.json.sha256` | Integrity of the manifest |
| `SM64Trainer.exe` (+ `.sha256`) | The **bootstrap installer** (§4). Name is load-bearing: already-shipped onefile updaters can only install an asset with exactly this name. Published with every release, forever |

The updater refuses to offer a release missing the manifest or checksum
assets — same "no unverified exe ever applies" rule as today.

### 3. Update flow (new app, steady state)

**Check** (unchanged shape): poll latest release, compare versions, honor the
skipped-version overlay. Additionally fetch + verify `manifest.json` and
compute the **plan** — so the popup can display the exact download size
("Update to 1.4.0 — 12.3 MB").

**Plan** (pure): diff the remote manifest against the locally installed one
(`installed_manifest.json`, kept in the install root, written by the
installer and after every successful apply). Local files are hash-verified
lazily: any file whose hash mismatches its recorded manifest entry is treated
as changed (corruption self-heals via re-download). Output: files to
add/replace, files to delete (present in local manifest but not remote —
files *not* in either manifest are never touched), total download bytes.

**Fetch**: coalesce adjacent zip entries into ranged GETs against
`SM64Trainer-full.zip`; inflate each entry locally (raw deflate), verify
per-file SHA-256, write into a staging dir mirroring the tree. Any Range
failure or excessive fragmentation → fall back to downloading the full zip
and extracting the planned files. Free-disk check before staging. Progress =
planned bytes fetched (drives the existing progress bar).

**Apply** (on the user clicking Update, after staging completes) — a
generalization of today's two-rename trick, made crash-safe:

1. Write `update_journal.json` (the plan + state) in the install root.
2. For each planned path: rename the live file into a `.update_backup\` tree
   (Windows allows renaming running exes and loaded DLLs), then move the
   staged file in. Deletions rename into the backup tree only.
3. On any failure: reverse the completed steps from the journal (restore
   backups), surface the error; the install is never left half-updated.
4. Write the new `installed_manifest.json`, mark the journal complete, then
   restart via the existing `core/relaunch.spawn_replacement` path.
5. **Startup repair**: on every launch, before the server starts, an
   incomplete journal triggers rollback-from-backup, then a single relaunch
   (journal marked resolved first — no restart loop).
6. On a successful start, sweep `.update_backup\` in the background with
   bounded retries (generalizes `cleanup_old`, which handles the old process
   still holding its exe/DLLs briefly).

### 4. Migration & new users — the bootstrap installer

A second, tiny PyInstaller **onefile** build (~25 MB): stdlib-only
(`urllib`, `zipfile`, `hashlib`, plus a minimal `tkinter` progress window), entry
`src/sm64_events/bootstrap/installer.py`, built under a distinct name and
uploaded as `SM64Trainer.exe`.

Behavior (idempotent — safe to run any time, also serves as a repair tool):

1. Download `SM64Trainer-full.zip` + `.sha256` from the latest release,
   verify, with a minimal progress window.
2. Extract to a temp dir, then move into `%LOCALAPPDATA%\Programs\SM64Trainer`
   (staged, so a failed download never breaks an existing install).
3. Write `installed_manifest.json` from the release manifest.
4. Create/refresh the Desktop shortcut "SM64 Trainer" (WScript.Shell COM via
   PowerShell one-liner) pointing at the installed exe.
5. Launch the installed app, passing its own path; the app deletes the
   bootstrap file on first run (rename-aside + background retry — the
   bootstrap can't delete itself while running).

**Existing users** (onefile v1.3.x): their shipped updater sees the next
release's `SM64Trainer.exe` asset, downloads (~25 MB), swaps, restarts —
which launches the bootstrap in place of the old exe. It performs the install
(the one-time 220 MB full download), creates the shortcut, launches the new
app, and removes itself from wherever the old exe lived. Old Desktop
exe → Desktop shortcut; total migration cost ≈ one full download, as
expected. Users who never update simply stay compatible: the asset name and
`.sha256` remain valid forever.

**New users**: the GitHub "download SM64Trainer.exe and double-click" habit
is preserved — the same file is now a proper installer. `SM64Trainer-full.zip`
also remains manually extract-and-run for portable users.

### 5. Code changes

| Where | What |
|---|---|
| `core/updater.py` | Slims to UpdateService orchestration + release check (same injectable-http, cached-check, skip-overlay, `is_frozen` gating) |
| `core/update_plan.py` (new) | Pure: manifest schema, local-state diff → plan (adds/replaces/deletes/bytes) |
| `core/update_fetch.py` (new) | Range coalescing + ranged download + inflate + verify + stage; full-zip fallback; disk-space check |
| `core/update_apply.py` (new) | Journaled swap, rollback, startup repair, backup sweep |
| `core/paths.py` | `install_root()` (parent of `sys.executable` when frozen) |
| `server/update_api.py` | Additive: `download_bytes` in status payload |
| `ui/components/update.js` | Show download size; progress semantics unchanged |
| `src/sm64_events/bootstrap/installer.py` (new) | Stdlib-only bootstrap installer |
| `tools/build_exe.py` | Onedir app build + separate bootstrap onefile build; reproducibility env vars |
| `tools/make_manifest.py` (new) | Zip the onedir tree; emit manifest with per-entry data offsets (parsing local headers); pure + testable |
| `tools/release.py` | Build both artifacts, generate manifest + checksums, upload the 6 assets; `--dry-run` covers the new pipeline |
| `main.py` / `desktop/app.py` | Startup-repair hook + bootstrap-file cleanup handoff |

Removed: the single-exe `download_and_stage`/`apply_update` path (lives on
only in already-shipped versions; the bootstrap supersedes it as the
migration vehicle).

### 6. Error handling summary

- Hash mismatch anywhere → abort, delete staged bytes, keep current install.
- Range failure → full-zip fallback; both paths verify per-file hashes.
- Crash mid-swap → journal-driven rollback on next launch (single relaunch).
- AV file locks during swap → bounded retries (as today), then rollback.
- Install dir not writable → popup offers the GitHub link only (as today).
- Updater remains inert when not frozen (`SM64_UPDATE_FAKE` dev preview kept).

### 7. Testing & verification

- Pure-function tests: plan diffs (add/replace/delete/corrupt-local),
  range coalescing, zip offset math (crafted zips incl. entries with extra
  fields), journal replay/rollback from every interruption point.
- Integration: fake release assets behind the injectable http opener →
  full check→plan→fetch→apply→restart-handoff cycle against a temp install
  tree; bootstrap install cycle likewise.
- Live gate with the human: (1) dry-run release build; (2) real test release
  — watch a v1.3.x onefile install migrate end-to-end; (3) second test
  release — confirm the update downloads only a few MB and the Desktop
  shortcut survives; (4) one real-GitHub Range probe.

## Out of scope (explicit)

- Auto-download / background updates (user chose the popup flow).
- Chunk-level diffing *within* changed files (per-file granularity suffices;
  revisit only if the measured volatile set disappoints).
- Code signing (unchanged from today).
- Slimming the 220 MB base — we bundle both a standalone `ffmpeg.exe` *and*
  PyAV's FFmpeg DLLs; a follow-up feature could likely cut 50–100 MB, which
  would shrink first installs and the migration download.
