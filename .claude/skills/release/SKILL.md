---
name: release
description: >-
  Build and publish a new GitHub release of SM64 Trainer — the packaged
  SM64Trainer.exe plus the in-app auto-update notes. Use this whenever the user
  wants to cut, ship, or publish a release, bump the version, or "make a new
  version / new build", even if they don't name tools/release.py. It picks the
  next version, generates plain-language user-facing release notes from the
  commit history since the last release, folds in any extra notes the user
  wants to add, runs the build+publish, and verifies the result. Trigger on
  "release", "cut a release", "ship it", "publish a version", "new build",
  "/release", or any request to get the latest changes out to users.
---

# Release SM64 Trainer

This project ships as a self-updating Windows exe. A release = bump the version,
build `dist/SM64Trainer.exe`, attach it (+ a SHA-256) to a GitHub release with
**user-facing notes**, and push. The running exe checks GitHub on launch (and via
the "⟳ updates" button), so **whatever notes you publish are what users read in
the in-app update popup** — write them for a player, not a developer.

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

Show the user the final memo before publishing.

## Step 6 — build and publish

```bash
uv run python tools/release.py <version> --notes-file internal_notes/release-notes-<version>.md
```

Run it **in the background** — the PyInstaller build takes a few minutes. The
script: runs the full test suite, bumps `core/version.py` + `pyproject.toml`
(+ `uv.lock`), builds the exe with ffmpeg bundled, writes the SHA-256, pushes the
commit + annotated tag, and `gh release create`s with the exe + checksum
attached (GitHub adds the source archives automatically).

Add `--dry-run` first if you want to build + checksum without committing/tagging
/publishing (e.g. to sanity-check a heavy change).

## Step 7 — verify

When the background build finishes, confirm it actually published:

```bash
gh release view v<version> --json tagName,assets --jq '{tag: .tagName, assets: [.assets[].name]}'
git rev-parse --short HEAD; git rev-parse --short origin/main; git status --porcelain
```

Expect the two assets `SM64Trainer.exe` + `SM64Trainer.exe.sha256`, local `main`
== `origin/main`, and a clean tree. Report the release URL
(`https://github.com/griffinbeels/SM64-Trainer/releases/tag/v<version>`) and
remind the user they can verify the in-app update from an older install (or the
"⟳ updates" button) — clicking **Update now** swaps in place and restarts.

## Gotchas (learned the hard way)

- **Notes file must be in `internal_notes/`** (gitignored). Anywhere else dirties
  the tree and the preflight refuses.
- **ffmpeg must be on PATH** at build time or the exe ships without it and replay
  falls back to the slow in-process encoder. `tools/build_exe.py` only warns.
- **The build runs before the tag/push**, so a broken build aborts with nothing
  published — safe to retry.
- **If `release.py` fails *after* the build** (e.g. a transient `gh` error), the
  version commit + tag may already be local. Don't re-run from scratch — push the
  tag (`git push origin v<version>`) and finish with `gh release create` using the
  already-built `dist/SM64Trainer.exe` + `.sha256`. Re-running `release.py` with
  the same version fails because there's nothing new to commit.
- **Renaming the exe is breaking** for auto-update: an installed older exe looks
  for the *old* asset name and won't see the new release, and auto-update keeps
  the existing filename anyway. Such a release needs a one-time manual download —
  call it out in the notes.
- The notes you publish are what the **next** version's users see in the popup —
  always user-facing.
