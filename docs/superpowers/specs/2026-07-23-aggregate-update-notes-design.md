# AGGREGATED UPDATE NOTES (cumulative patch notes) — Design Spec

**Date:** 2026-07-23
**Status:** Approved design, pending implementation plan

## 1. Goal

When a user is several versions behind, the update popup must show the patch
notes for **every version they missed**, not just the newest one — newest
first, each under its own version header, in one scrollable notes box.

Today `core/updater.py::check_for_update` GETs only
`/repos/{repo}/releases/latest`, so a user on v1.2.1 updating to v1.4.2 sees
v1.4.2's notes and never learns what v1.3.0 or v1.4.0 changed.

**How far back this works: all the way to v1.0.0.** Every one of the 15
published release bodies was inspected (2026-07-23) and they come in exactly
two shapes, both already handled by one strip rule:

| Releases | Body shape | Strip rule |
|---|---|---|
| v1.4.0 – v1.4.2 | first-time-setup header + `## What's new` + notes | take everything after the marker (today's rule) |
| v1.0.0 – v1.3.0 | pure hand-written notes, leading `## <Name> vX.Y.Z` heading, no setup header | use the whole body, drop the leading version heading (the UI supplies its own) |

No release needs special-casing, and no truncation/"see GitHub for older" UI
is needed for any user who can reach this code path.

## 2. Prior art

- **In-repo:** the existing marker strip (`PATCH_NOTES_MARKER`,
  `core/update_plan.py:37`) already separates "GitHub-page content" from
  "in-app patch notes". This design generalises that one-body rule to N
  bodies rather than inventing a second notes convention.
- **Industry:** the standard desktop-updater pattern (Sparkle/WinSparkle's
  appcast, VS Code / Firefox release-notes pages) is "changelog since the
  installed version", assembled client-side from a list of published
  releases. Electron-updater's `releaseNotes` likewise becomes an array when
  multiple versions are pending. Aggregating at the client — where the
  installed version is known — is the conventional answer; baking cumulative
  notes into each published release is not a pattern anyone uses, for the
  reason in §3 (approach C).

## 3. Approaches considered

| | Approach | Verdict |
|---|---|---|
| **A** | Keep `/releases/latest` as the authority for *what to install*; add one GET `/repos/{repo}/releases?per_page=100` purely to collect notes for versions newer than the installed one. Best-effort — failure degrades to today's single-release notes. | **Chosen.** Install-target semantics untouched; aggregation can never block an update offer. Costs one extra API request per hourly check (2/hr against the 60/hr unauthenticated limit), cached with the rest of the check. |
| **B** | Drop `/releases/latest`; use the list endpoint alone and treat the first non-draft entry as latest. | Rejected. Saves one request but changes which release is installed: GitHub's `latest` ≠ `list[0]` when a prerelease or an out-of-order backport is published. Real risk, no gain. |
| **C** | Bake cumulative notes into the release body at publish time (`tools/release.py`). | Rejected — impossible in principle. The publisher cannot know how far back any given user is, so every body would have to embed the entire project history forever. |

## 4. Decisions (user-confirmed 2026-07-23)

| Question | Decision |
|---|---|
| Layout when 10 versions behind | **All expanded**, newest first, one version header per release, in a notes box that scrolls **independently** so the action buttons stay visible |
| How far back | **Every release newer than the installed version** — no cap below the 100-per-page ceiling |
| Ordering | Newest first, by **parsed version** (not publish order) |
| Per-version date | Shown, dimmed, beside the version |

## 5. New module — `core/release_feed.py`

Pure, stdlib-only, no state. Owns one concern: *the GitHub releases feed* —
fetching it, comparing versions, and extracting per-version notes. It is the
**lower** layer: it imports nothing from `updater.py`, and `updater.py`
imports from it. (Written the other way — a notes module importing
`is_newer` from `updater` while `updater` imports the notes module — the two
modules import each other at module scope and the package fails to load. The
dependency direction below is load-bearing, not stylistic.)

This also relieves `core/updater.py`, already 352 lines, at a natural seam:
after the split it is purely verify → plan → fetch → swap → service.

```python
@dataclass(frozen=True)
class ReleaseNotes:
    version: str        # "1.4.2" (no leading v)
    date: str           # "2026-07-23" from published_at; "" if absent
    notes: str          # stripped body
```

**Moved in from `updater.py` unchanged** (deleted there, imported back):

- `parse_version(tag) -> tuple[int, ...]`, `is_newer(candidate, current)`
- the `_UA` constant and the request helper, promoted to public
  **`http_get(http, url, *, accept=None)`** so both modules share one
  User-Agent'd request builder instead of duplicating it.

**New:**

- **`strip_body(body: str) -> str`** — THE strip rule for both body shapes
  (§1 table): everything after `PATCH_NOTES_MARKER` if present, else the
  whole body with a leading `## … vX.Y.Z` heading line removed. The inline
  strip currently in `check_for_update` moves here; that function calls this.
- **`missed_releases(current, *, http, repo, api_base) -> list[ReleaseNotes]`**
  — GET `{api_base}/repos/{repo}/releases?per_page=100`; drop entries with
  `draft` or `prerelease` true; keep tags where `is_newer(tag, current)`;
  sort by `parse_version(tag)` **descending**; map through `strip_body`.
  Any exception → `[]` (logged at info, never raised).

`PATCH_NOTES_MARKER` still comes from `core/update_plan.py`, which imports
nothing from either module — no cycle.

## 6. `core/updater.py` changes

- Imports `http_get`, `is_newer`, `missed_releases`, `strip_body`,
  `ReleaseNotes` from `core/release_feed.py`; `parse_version`/`is_newer`/
  `_get`/`_UA` are deleted here (they now live there — one definition).
- `UpdateInfo` gains `releases: list[ReleaseNotes]`.
- `check_for_update` calls `missed_releases(...)` after the asset/verification
  checks pass. If the result is empty (list call failed, or a lone release),
  it falls back to `[ReleaseNotes(version, date, strip_body(body))]` for the
  latest release alone — **the update is still offered exactly as today.**
- `notes` stays on `UpdateInfo` unchanged, equal to `releases[0].notes`. It is
  the single-version fallback the popup renders if `releases` is ever empty;
  existing callers and tests keep working untouched.
- `status()` gains `"releases": [{"version", "date", "notes"}, …]`.
- `_fake()` (the `SM64_UPDATE_FAKE=1` dev path) returns **three** synthetic
  releases so the stacked layout is verifiable in dev without cutting a
  release.

No change to `server/update_api.py` (no new route — `status()` just carries a
new field), and no change to `ui/store.js` (it stores the whole status dict).

## 7. UI — `ui/components/update.js` + `ui/index.html`

- Render `st.releases` as a sequence of `<version header> + renderNotes(notes)`
  blocks separated by a rule. `renderNotes` is unchanged.
- Fall back to a single `renderNotes(st.notes)` block when `releases` is
  missing or empty.
- Subtitle: `You're on v1.2.1 — 3 versions of changes.` when more than one
  release is stacked; today's `You're on v1.2.1.` otherwise.
- CSS: `.update-notes { max-height: 46vh; overflow: auto; }` plus a version
  header + divider style. **This is load-bearing, not cosmetic** — the modal
  is `max-height: 80vh; overflow: auto`, so without an inner scroll a long
  backlog pushes Update / Skip / Later below the fold.

## 8. Error handling

| Failure | Behavior |
|---|---|
| `/releases` request fails, times out, or returns malformed JSON | `missed_releases` → `[]` → popup shows the latest release's notes alone. Update still offered. |
| A single release body is malformed/empty | That entry renders with an empty notes block; the rest are unaffected. |
| More than 100 releases exist | Only the newest 100 are considered. Not reachable in the foreseeable future (15 releases as of this spec); the popup's existing "View this release on GitHub →" link covers it. No pagination is built. |
| Rate limit | Check is TTL-cached (1 hr) as today; this adds one request per check, 2/hr total against 60/hr. |

## 9. Testing

`tests/test_release_feed.py` (new, pure — fake `http` returning canned JSON).
The three existing `parse_version` / `is_newer` tests **move here** from
`tests/test_updater.py`, following their module (tests mirror modules). New
cases:

- newest-first ordering, **by version not publish order** (feed an
  out-of-order `published_at` and assert the version sort wins)
- the installed version and everything older are excluded
- both body shapes strip correctly (marker body; legacy leading-heading body)
- drafts and prereleases excluded
- HTTP failure → `[]`, no raise

`tests/test_updater.py` (additions, reusing the existing `_fake_http` routes
harness):

- `check_for_update` populates `releases` newest-first across several versions
- **list-endpoint failure still returns a valid `UpdateInfo`** with a
  one-entry `releases` — the "aggregation never blocks an offer" invariant
- `status()` carries `releases`

Per the pin-fields-not-payload-dicts rule, the `status()` assertions check the
keys this feature owns and leave the rest of the dict unpinned.

## 10. Docs

- `CLAUDE.md` module map: a row for `core/release_feed.py` (the releases-feed
  reader: version compare + per-version notes extraction, the lower layer
  `updater.py` sits on), and the `core/updater.py` row amended to say the
  check now aggregates every missed version's notes.
- No `README.md` / `docs/api.md` change: no new route is added, and
  `/api/update/*` is not part of the documented REST surface (the
  `test_docs_cover_api.py` sweep builds an app without the update router).

## 11. Out of scope

- Pagination beyond 100 releases (§8).
- Collapsing/hiding older versions behind a toggle (explicitly rejected in §4).
- Any change to skip semantics — "Skip this version" still records the latest
  version only.
- Any change to what gets downloaded or how it is verified.
