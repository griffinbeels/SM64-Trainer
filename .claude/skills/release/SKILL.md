---
name: release
description: >-
  Build and publish a new GitHub release of SM64 Trainer — the six-asset
  incremental-update set (app zip + manifest + bootstrap installer, each with
  a .sha256) plus the in-app auto-update notes. Use this whenever the user
  wants to cut, ship, or publish a release, bump the version, or "make a new
  version / new build", even if they don't name tools/release.py. It picks the
  next version, generates plain-language user-facing release notes from the
  commit history since the last release, folds in any extra notes the user
  wants to add, runs the build+publish, and verifies the result. Trigger on
  "release", "cut a release", "ship it", "publish a version", "new build",
  "/release", or any request to get the latest changes out to users.
---

# Release SM64 Trainer

This project ships as a self-updating Windows app with **incremental updates**
(since v1.4.0, 2026-07-23; the mechanism is documented in
`docs/architecture.md` → "Incremental updates"). A release =
bump the version, build the **onedir app + bootstrap installer**, and publish
**SIX assets** with **user-facing notes**:

| Asset | What it is |
|---|---|
| `SM64Trainer-full.zip` (+`.sha256`) | The whole onedir app tree — first installs, portable users, full-download fallback |
| `manifest.json` (+`.sha256`) | Per-file SHA-256 + zip byte offsets — what lets installed apps download ONLY changed files (typically ~25 MB) |
| `SM64Trainer.exe` (+`.sha256`) | The **BOOTSTRAP INSTALLER** (~11 MB), NOT the app itself — new-user installer AND the migration vehicle pre-1.4.0 onefile installs update through. **This asset name is load-bearing and must be published under exactly this name in EVERY release, forever** (old shipped updaters can only install that name; names come from THE registry `core/update_plan.py`) |

The running app checks GitHub on launch (and via the "⟳ updates" button), so
**whatever notes you publish are what users read in the in-app update popup**
(which also shows the exact download size) — write them for a player, not a
developer.

`tools/release.py` does the mechanical part (bump → tag → build → checksum →
`gh release create`). Your job in this skill is the part a script can't do well:
**turn the commit history into a release memo a human actually wants to read**,
and to drive + verify the whole thing safely.

## Before you start — preflight

`tools/release.py` refuses unless these hold, so check them up front and fix
before going further (a failure mid-release is annoying — see Gotchas). Run all
shell snippets in this skill via the **Bash tool** (POSIX sh), not PowerShell:

```bash
git rev-parse --abbrev-ref HEAD    # must be: main
git status --porcelain             # must be empty (clean tree)
gh auth status                     # must be authenticated
command -v ffmpeg                  # must be on PATH — it gets bundled into the exe
```

If the tree is dirty, stop and ask the user — don't release uncommitted work.
If you're not on `main`, the work probably needs merging first.

## Step 1 — find the last release and the changes since

```bash
gh release list                                        # newest tag is the baseline
LAST=$(gh release view --json tagName -q .tagName)     # e.g. v1.0.4
git log "$LAST"..HEAD --no-merges --pretty=format:'%h %s%n%b'
```

Read the **full** log including bodies — this repo's commit messages explain the
*why*, which is exactly what good notes need. Also skim `git diff "$LAST"..HEAD
--stat` to catch user-visible changes a terse subject hides.

If there are **no commits** since the last tag, there's nothing to release — tell
the user and stop.

## Step 2 — choose the next version

Versions are `MAJOR.MINOR.PATCH` (the latest is in `src/sm64_events/core/version.py`).
Suggest a bump from the change set, then confirm with the user:

- **patch** (x.y.**Z+1**) — fixes, docs, internal/tooling only. The common case.
- **minor** (x.**Y+1**.0) — a new user-facing feature or capability.
- **major** (**X+1**.0.0) — a breaking change to how users run/upgrade it (e.g.
  the exe rename in v1.0.2 was effectively breaking — it needed a manual
  re-download; flag that kind of thing loudly).

Always confirm the number with the user before publishing — it's the one thing
that can't be un-published cleanly.

## Step 3 — draft the release memo (the important part)

Translate commits into **user-facing notes**, not a changelog of raw subjects.
A player doesn't care that you "lifted update state into the store"; they care
that there's now a "Check for updates" button. Rules of thumb:

- Lead with what's **new or fixed for the user**. Group as a short bulleted list;
  drop pure-internal commits (refactors, test-only, CI) unless they change
  behavior.
- One bullet per user-visible change, in plain language. Start features with
  **New:** and fixes with **Fix:**.
- If a change needs the user to *do* something (e.g. a manual re-download, a
  settings step), say so explicitly in its own short paragraph.
- Keep it tight — a few bullets and maybe a sentence of context. The popup is
  small.
- Markdown: use `-` bullets, `**bold**`, `` `code` ``, and `[links](https://…)`.
  The popup renderer joins soft-wrapped lines into one bullet/paragraph (a blank
  line separates blocks), so you can wrap naturally.

**Example — commits → memo:**

Input commits:
```
feat(ui): Check for updates button + lift update state into the store
fix(update): reliable post-update .old cleanup + rename EXE_NAME
docs: tidy api.md
```
Good memo:
```markdown
## SM64 Trainer vX.Y.Z

- **New: a "⟳ updates" button** in the top bar — check for a newer version any
  time without relaunching.
- **Fix:** updating no longer leaves a leftover `.old` file next to the program.
```
(The docs commit is dropped — not user-facing.)

## Step 4 — add the user's own notes

Ask: *"Anything specific you want to call out in this release? (a known issue, a
shout-out, a heads-up — or 'no' to ship the auto-generated notes)."* Weave their
additions into the memo naturally rather than tacking them on at the end.

## Step 5 — write the notes file (must be gitignored)

Write the memo to **`internal_notes/release-notes-<version>.md`**. This location
is deliberate: `internal_notes/` is gitignored, so the file doesn't dirty the
working tree — and `tools/release.py` refuses to run on a dirty tree. A notes
file anywhere tracked would block the release.

**Write ONLY the patch notes — no setup instructions.** `release.py`
automatically composes the published release body as:
`docs/release_setup_header.md` (the standing first-time-setup section every
release page carries for new users) + the `## What's new` marker
(`PATCH_NOTES_MARKER` in `core/update_plan.py`) + your memo. The in-app
popup strips through the marker, so **recurring users see only your patch
notes** while the GitHub page shows setup + notes. Never hand-add setup
steps to the memo (they'd duplicate the header), and never remove the
marker mechanism. If the setup flow ever changes, edit
`docs/release_setup_header.md` AND the README's Install section together.
They are worded differently on purpose (the header opens with "Already
installed? You don't need anything from this page"), so they are NOT compared
as text — but `tests/test_release.py::test_readme_and_release_page_agree_on_every_setup_fact`
fails if they disagree on any fact a user acts on: asset names, install
location, the Project64 and Usamune versions, the WebView2 and SmartScreen
notices. Change one and the suite tells you about the other.

Show the user the final memo before publishing.

## Step 6 — build and publish

```bash
uv run python tools/release.py <version> --notes-file internal_notes/release-notes-<version>.md
```

Run it **in the background** — the two PyInstaller builds + zipping the ~550 MB
onedir tree take ~10-15 minutes. The script: runs the full test suite, bumps
`core/version.py` + `pyproject.toml` (+ `uv.lock`), builds `--mode all`
(onedir app with ffmpeg bundled + bootstrap), zips the tree
(`tools/make_manifest.build_zip`), generates `manifest.json`, copies the
bootstrap to `dist/SM64Trainer.exe`, writes all three `.sha256`s, pushes the
commit + annotated tag, and `gh release create`s with the six assets (GitHub
adds the source archives automatically).

Add `--dry-run` first if you want to build + checksum without committing/tagging
/publishing (e.g. to sanity-check a heavy change).

## Step 7 — verify

When the background build finishes, confirm it actually published:

```bash
gh release view v<version> --json tagName,assets --jq '{tag: .tagName, assets: [.assets[].name]}'
git rev-parse --short HEAD; git rev-parse --short origin/main; git status --porcelain
```

Expect ALL SIX assets — `SM64Trainer-full.zip`, `manifest.json`,
`SM64Trainer.exe`, each with its `.sha256` — local `main` == `origin/main`,
and a clean tree. **A release missing the zip/manifest/checksums is never
offered by the in-app updater and is refused by the bootstrap** (the
"no unverified bytes" rule), so a partial upload silently strands users —
verify the full set. Report the release URL
(`https://github.com/griffinbeels/SM64-Trainer/releases/tag/v<version>`) and
remind the user they can verify the in-app update from an older install (or
the "⟳ updates" button) — the popup shows the download size; **Update now**
downloads only changed files and restarts.

## Gotchas (learned the hard way)

- **Notes file must be in `internal_notes/`** (gitignored). Anywhere else dirties
  the tree and the preflight refuses.
- **ffmpeg must be on PATH** at build time or the exe ships without it and replay
  falls back to the slow in-process encoder. `tools/build_exe.py` only warns.
- **The build runs before the tag/push**, so a broken build aborts with nothing
  published — safe to retry.
- **If `release.py` fails *after* the build** (e.g. a transient `gh` error), the
  version commit + tag may already be local. Don't re-run from scratch — push the
  tag (`git push origin v<version>`) and finish with `gh release create` listing
  ALL SIX already-built assets from `dist/` (zip, manifest, exe + three
  `.sha256`s — `release_assets()` in tools/release.py is the authoritative
  list). Re-running `release.py` with the same version fails because there's
  nothing new to commit.
- **NEVER rename or drop the `SM64Trainer.exe` asset** — it's the bootstrap
  under the only name pre-1.4.0 updaters can install, AND what the README
  tells new users to download. Asset names live in `core/update_plan.py`
  (ZIP_ASSET / MANIFEST_ASSET / BOOTSTRAP_ASSET); release.py imports them —
  a rename there without a migration story strands every old install.
- **`dist/SM64Trainer.exe` in a fresh build is the ~11 MB BOOTSTRAP, not the
  app** — the app is the `dist/SM64Trainer/` folder. Don't "fix" this.
- **Delta size sanity:** any Python change costs ~25 MB (the onedir exe embeds
  the PYZ); UI/data-only changes cost KBs; a dependency bump adds those
  packages. Measured facts in docs/architecture.md → "Incremental updates".
- The notes you publish are what the **next** version's users see in the popup —
  always user-facing.
