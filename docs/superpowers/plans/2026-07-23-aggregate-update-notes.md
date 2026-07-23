# Aggregated Update Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the update popup appears, show the patch notes for **every**
version the user skipped — newest first, one header per version — instead of
only the newest release's notes.

**Architecture:** `/releases/latest` stays the sole authority for *what gets
installed*. A new lower-layer module `core/release_feed.py` adds one
best-effort `GET /releases?per_page=100` that collects notes for every version
newer than the installed one; any failure returns `[]` and the popup falls back
to today's single-release notes, so aggregation can never block an update
offer. `core/updater.py` imports from the new module (never the reverse — the
opposite direction is a module-scope import cycle that fails to load the
package).

**Tech Stack:** Python 3.12 (stdlib only in `core/`), pytest, Preact + htm
(vendored) for the popup, plain CSS in `ui/index.html`.

**Spec:** `docs/superpowers/specs/2026-07-23-aggregate-update-notes-design.md`

## Global Constraints

- `core/release_feed.py` is **stdlib-only** and imports **nothing** from
  `core/updater.py`. The dependency direction is load-bearing, not stylistic.
- Aggregation is **best-effort**: `missed_releases` must never raise. Any
  failure → `[]` → the popup still offers the update with the latest release's
  notes alone.
- One version-comparison implementation. `parse_version` / `is_newer` move to
  `core/release_feed.py` and are **deleted** from `core/updater.py`; nothing
  re-exports them.
- One strip rule. `strip_body` handles **both** published body shapes
  (marker-bearing v1.4.0+, legacy title-line v1.0.0–v1.3.0); no per-release
  special-casing anywhere.
- Ordering is by **parsed version**, descending — never by publish date.
- No new REST route. `status()` gains a field only, so
  `tests/test_docs_cover_api.py` is unaffected.
- Tests pin only the keys this feature owns (`releases`, and the fields inside
  it). Never assert whole-payload equality on `status()`.
- Run the suite with `uv` (never pip): `uv run pytest -q`.

---

### Task 1: `core/release_feed.py` — version helpers + the one body-strip rule

Creates the new lower layer by **moving** `parse_version`, `is_newer`, and the
User-Agent'd request helper out of `updater.py`, and adds `strip_body` — the
single rule that reduces either published body shape to plain patch notes.
`updater.py` starts importing from it in this same task, so the tree is green
at commit time.

**Files:**
- Create: `src/sm64_events/core/release_feed.py`
- Create: `tests/test_release_feed.py`
- Modify: `src/sm64_events/core/updater.py` (docstring, imports, delete moved
  helpers, use `strip_body`)
- Modify: `tests/test_updater.py:10-26` (delete the three moved tests)
- Modify: `CLAUDE.md` (module map row)

**Interfaces:**
- Consumes: `PATCH_NOTES_MARKER` from `sm64_events.core.update_plan`.
- Produces, for Tasks 2–3:
  - `ReleaseNotes(version: str, date: str, notes: str)` — frozen dataclass
  - `parse_version(tag: str) -> tuple[int, ...]`
  - `is_newer(candidate: str, current: str) -> bool`
  - `http_get(http, url: str, *, accept: str | None = None)` — context manager
    response, same shape `urllib.request.urlopen` returns
  - `strip_body(body: str) -> str`
  - `notes_from_release(rel: dict) -> ReleaseNotes`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_release_feed.py`:

```python
# tests/test_release_feed.py
"""The releases feed: version comparison plus the ONE body-strip rule that
turns either published body shape into plain patch notes."""
from sm64_events.core.release_feed import (is_newer, notes_from_release,
                                           parse_version, strip_body)


def test_parse_version_strips_v_and_splits():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.3") == (1, 2, 3)


def test_parse_version_stops_at_non_numeric_suffix():
    assert parse_version("1.2.3-beta") == (1, 2, 3)


def test_is_newer_compares_numerically():
    assert is_newer("1.2.10", "1.2.9") is True   # not lexicographic
    assert is_newer("1.0.0", "0.9.9") is True
    assert is_newer("1.0.0", "1.0.0") is False
    assert is_newer("0.9.9", "1.0.0") is False


def test_strip_body_takes_everything_after_the_marker():
    """v1.4.0+ bodies: setup header for the GitHub page, marker, then notes."""
    from sm64_events.core.update_plan import PATCH_NOTES_MARKER
    body = ("# First time here?\nDownload the installer.\n\n"
            + PATCH_NOTES_MARKER + "\n\n- **New:** a thing\n- **Fix:** a bug\n")
    assert strip_body(body) == "- **New:** a thing\n- **Fix:** a bug"


def test_strip_body_drops_a_legacy_leading_version_heading():
    """v1.0.0-v1.3.0 bodies carry no marker and title themselves; the popup
    renders its own version header, so that title line would double up."""
    body = "## SM64 Trainer v1.2.0\n\n- **New:** rank medals\n"
    assert strip_body(body) == "- **New:** rank medals"


def test_strip_body_keeps_a_marker_less_body_that_has_no_title():
    assert strip_body("just notes") == "just notes"


def test_strip_body_handles_empty():
    assert strip_body("") == ""


def test_notes_from_release_reads_tag_date_and_body():
    row = notes_from_release({"tag_name": "v1.1.0",
                              "published_at": "2026-06-23T19:40:08Z",
                              "body": "## SM64 Trainer v1.1.0\n\n- a fix"})
    assert (row.version, row.date, row.notes) == ("1.1.0", "2026-06-23",
                                                  "- a fix")


def test_notes_from_release_tolerates_missing_fields():
    row = notes_from_release({})
    assert (row.version, row.date, row.notes) == ("", "", "")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_release_feed.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.core.release_feed'`

- [ ] **Step 3: Create the module**

Create `src/sm64_events/core/release_feed.py`:

```python
# src/sm64_events/core/release_feed.py
"""The GitHub releases feed: version comparison, the shared HTTP request
helper, and the per-version patch-notes extraction the update popup renders.

This is the LOWER layer under core/updater.py — it imports NOTHING from
updater; updater imports from here. Written the other way round (a notes
module importing is_newer from updater while updater imports the notes
module) the two import each other at module scope and the package fails to
load.

Published release bodies come in exactly two shapes (all 15 releases audited
2026-07-23):

  * v1.4.0+          first-time-setup header for the GitHub page,
                     PATCH_NOTES_MARKER, then the patch notes
  * v1.0.0 - v1.3.0  pure hand-written notes under their own leading
                     '## <Name> vX.Y.Z' heading, no setup header

strip_body() is THE rule for both, which is why the popup can stack notes all
the way back to v1.0.0 with no per-release special-casing."""
import json
import logging
import re
import urllib.request
from dataclasses import dataclass

from sm64_events.core.update_plan import PATCH_NOTES_MARKER

log = logging.getLogger("sm64.releases")

_UA = "SM64Trainer-updater"
# One page. 100 releases is years of headroom at the current cadence, and the
# popup's "View this release on GitHub" link covers anything older, so no
# pagination is built.
_PER_PAGE = 100
# A legacy body's own title line, e.g. '## SM64 Trainer v1.2.0 - first release'.
# The popup renders its own version header, so this would render twice.
_LEGACY_TITLE = re.compile(r"^\s*#{1,6}\s+.*v\d+\.\d+\.\d+")


@dataclass(frozen=True)
class ReleaseNotes:
    """One published release as the popup shows it."""
    version: str        # "1.4.2" - no leading v
    date: str           # "2026-07-23", or "" when published_at is absent
    notes: str          # body reduced by strip_body()


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


def http_get(http, url: str, *, accept: str | None = None):
    """The one User-Agent'd request builder both this module and updater.py
    use. `http` is injected so tests never touch the network."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if accept:
        req.add_header("Accept", accept)
    return http(req)


def strip_body(body: str) -> str:
    """Reduce a release body to just its patch notes (see the module
    docstring for the two shapes this handles)."""
    body = body or ""
    if PATCH_NOTES_MARKER in body:
        return body.split(PATCH_NOTES_MARKER, 1)[1].strip()
    lines = body.strip().split("\n")
    if lines and _LEGACY_TITLE.match(lines[0]):
        return "\n".join(lines[1:]).strip()
    return body.strip()


def notes_from_release(rel: dict) -> ReleaseNotes:
    """One release's GitHub JSON -> its popup row."""
    return ReleaseNotes(version=(rel.get("tag_name") or "").lstrip("vV"),
                        date=(rel.get("published_at") or "")[:10],
                        notes=strip_body(rel.get("body") or ""))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_release_feed.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Rewire `updater.py` onto the new module**

All five edits are exact-match replacements in
`src/sm64_events/core/updater.py`. Every anchor below must match verbatim — if
one does not, stop and re-read the file rather than editing around it.

**5a.** Docstring line 8-11 — the moved helpers are no longer listed here:

```python
Pure helpers (parse_version, is_newer, check_for_update, exe_dir_writable)
take an injected HTTP opener and operate on explicit paths so tests never
touch the network or a real install. The stateful UpdateService orchestrates
```
becomes
```python
Pure helpers (check_for_update, exe_dir_writable) take an injected HTTP
opener and operate on explicit paths so tests never touch the network or a
real install. Version comparison, the shared request builder, and patch-notes
extraction live one layer down in core/release_feed.py. The stateful
UpdateService orchestrates
```

**5b.** Imports — add `release_feed`, drop the now-unused `PATCH_NOTES_MARKER`:

```python
from sm64_events.core.update_plan import (INSTALLED_MANIFEST, MANIFEST_ASSET,
                                          PATCH_NOTES_MARKER, ZIP_ASSET,
                                          Manifest, build_plan,
                                          parse_manifest)
```
becomes
```python
from sm64_events.core.release_feed import (ReleaseNotes, http_get, is_newer,
                                           notes_from_release, strip_body)
from sm64_events.core.update_plan import (INSTALLED_MANIFEST, MANIFEST_ASSET,
                                          ZIP_ASSET, Manifest, build_plan,
                                          parse_manifest)
```

(`ReleaseNotes` and `notes_from_release` are unused until Task 3 — importing
them now keeps the import block a single edit. If your linter objects, add
them in Task 3 instead.)

**5c.** Delete `_UA` and the two moved functions. Delete this whole run —
from the `_UA` line through `is_newer`'s body — leaving `_CHECK_TTL_S` and
the `UpdateInfo` dataclass in place:

```python
_UA = "SM64Trainer-updater"
```
(delete just that line; keep `DEFAULT_REPO`, `GITHUB_API`, `_CHECK_TTL_S`)

and delete:

```python
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
```

**5d.** Delete `_get` and route its two call sites to `http_get`:

```python
def _get(http, url: str, *, accept: str | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if accept:
        req.add_header("Accept", accept)
    return http(req)
```
(delete the whole function)

```python
        with _get(http, url, accept="application/vnd.github+json") as r:
```
becomes
```python
        with http_get(http, url, accept="application/vnd.github+json") as r:
```

```python
        with _get(self._http, url) as r:
```
becomes
```python
        with http_get(self._http, url) as r:
```

**5e.** `check_for_update` uses the shared strip rule:

```python
        notes = rel.get("body") or ""
        if PATCH_NOTES_MARKER in notes:
            # The body's leading first-time-setup section is for the GitHub
            # page only — the popup shows just the patch notes.
            notes = notes.split(PATCH_NOTES_MARKER, 1)[1].lstrip()
        return UpdateInfo(
```
becomes
```python
        # The body's leading first-time-setup section is for the GitHub page
        # only, and legacy bodies title themselves — release_feed.strip_body
        # is THE rule for both shapes.
        notes = strip_body(rel.get("body") or "")
        return UpdateInfo(
```

- [ ] **Step 6: Delete the moved tests from `tests/test_updater.py`**

Delete lines 10-26 — the import and the three helper tests, which now live in
`tests/test_release_feed.py`:

```python
from sm64_events.core.updater import is_newer, parse_version


def test_parse_version_strips_v_and_splits():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.3") == (1, 2, 3)


def test_parse_version_stops_at_non_numeric_suffix():
    assert parse_version("1.2.3-beta") == (1, 2, 3)


def test_is_newer_compares_numerically():
    assert is_newer("1.2.10", "1.2.9") is True   # not lexicographic
    assert is_newer("1.0.0", "0.9.9") is True
    assert is_newer("1.0.0", "1.0.0") is False
    assert is_newer("0.9.9", "1.0.0") is False
```

`test_version_is_semver` above them stays.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. `test_check_strips_setup_header_from_notes` and
`test_check_keeps_notes_without_marker` in `tests/test_updater.py` still pass
unchanged — `strip_body` reproduces the old behavior for both bodies they
feed it.

- [ ] **Step 8: Add the module-map row**

In `CLAUDE.md`, insert this row immediately **above** the
`| Update contracts (manifest schema, ...) |` row:

```markdown
| Releases feed (version compare, patch-notes extraction) | `core/release_feed.py` — THE lower layer under `updater.py` (imports nothing from it; the reverse direction is an import cycle): `parse_version`/`is_newer`, the shared `http_get`, and `strip_body` — the ONE rule covering both published body shapes (v1.4.0+ carry `PATCH_NOTES_MARKER`; v1.0.0–v1.3.0 are pure notes under their own `## … vX.Y.Z` title), which is why the popup can stack notes back to v1.0.0 |
```

- [ ] **Step 9: Commit**

```bash
git add src/sm64_events/core/release_feed.py tests/test_release_feed.py \
        src/sm64_events/core/updater.py tests/test_updater.py CLAUDE.md
git commit -m "refactor(update): split the releases feed out of updater.py

Version comparison, the User-Agent'd request builder, and the release-body
strip rule move to core/release_feed.py — the lower layer the coming
notes aggregation needs. updater.py (352 lines) keeps verify/plan/fetch/swap.

strip_body() now covers BOTH published body shapes in one place: the
PATCH_NOTES_MARKER bodies (v1.4.0+) and the legacy self-titled ones
(v1.0.0-v1.3.0), so nothing downstream needs per-release special-casing."
```

---

### Task 2: `missed_releases` — every version newer than the installed one

**Files:**
- Modify: `src/sm64_events/core/release_feed.py` (append one function)
- Modify: `tests/test_release_feed.py` (append a section)

**Interfaces:**
- Consumes: `http_get`, `is_newer`, `parse_version`, `notes_from_release`,
  `ReleaseNotes`, `_PER_PAGE` (all from Task 1).
- Produces, for Task 3:
  `missed_releases(current: str, *, http, repo: str, api_base: str) -> list[ReleaseNotes]`
  — newest-first by parsed version, `[]` on any failure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_release_feed.py`:

```python
# --- missed_releases -------------------------------------------------------

import io  # noqa: E402
import json  # noqa: E402

from sm64_events.core.release_feed import (ReleaseNotes,  # noqa: E402
                                           missed_releases)

API = "https://api.github.com"
LIST_URL = f"{API}/repos/owner/repo/releases?per_page=100"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _feed(releases):
    """An http opener serving exactly one url: the releases list."""
    payload = json.dumps(releases).encode()

    def opener(req):
        assert req.full_url == LIST_URL, req.full_url
        return _Resp(payload)
    return opener


def _rel(tag, *, body="notes", published="2026-07-01T00:00:00Z", **flags):
    return {"tag_name": tag, "body": body, "published_at": published, **flags}


def _missed(releases, current="1.0.0"):
    return missed_releases(current, http=_feed(releases), repo="owner/repo",
                           api_base=API)


def test_missed_releases_returns_every_newer_version_newest_first():
    rows = _missed([_rel("v1.2.0"), _rel("v1.1.0"), _rel("v1.0.0")])
    assert [row.version for row in rows] == ["1.2.0", "1.1.0"]


def test_missed_releases_sorts_by_version_not_publish_date():
    """A backported patch published AFTER a higher minor must still read in
    version order, or the stack claims 1.1.1 supersedes 1.2.0."""
    rows = _missed([_rel("v1.1.1", published="2026-07-20T00:00:00Z"),
                    _rel("v1.2.0", published="2026-07-10T00:00:00Z")])
    assert [row.version for row in rows] == ["1.2.0", "1.1.1"]


def test_missed_releases_excludes_the_installed_version_and_older():
    assert _missed([_rel("v1.0.0"), _rel("v0.9.0")]) == []


def test_missed_releases_excludes_drafts_and_prereleases():
    rows = _missed([_rel("v1.3.0", draft=True),
                    _rel("v1.2.0", prerelease=True),
                    _rel("v1.1.0")])
    assert [row.version for row in rows] == ["1.1.0"]


def test_missed_releases_carries_date_and_stripped_notes():
    rows = _missed([_rel("v1.1.0", body="## SM64 Trainer v1.1.0\n\n- a fix",
                         published="2026-06-23T19:40:08Z")])
    assert rows == [ReleaseNotes("1.1.0", "2026-06-23", "- a fix")]


def test_missed_releases_empty_on_http_failure():
    """Best-effort: a dead list endpoint must NOT raise, so the caller can
    still offer the update with single-version notes."""
    def boom(req):
        raise OSError("network down")
    assert missed_releases("1.0.0", http=boom, repo="owner/repo",
                           api_base=API) == []


def test_missed_releases_empty_on_malformed_payload():
    """A rate-limit body is a dict, not a list of releases."""
    def opener(req):
        return _Resp(b'{"message": "API rate limit exceeded"}')
    assert missed_releases("1.0.0", http=opener, repo="owner/repo",
                           api_base=API) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_release_feed.py -q`
Expected: FAIL — `ImportError: cannot import name 'missed_releases'`

- [ ] **Step 3: Implement**

Append to `src/sm64_events/core/release_feed.py`:

```python
def missed_releases(current: str, *, http, repo: str,
                    api_base: str) -> list[ReleaseNotes]:
    """Every published release strictly newer than `current`, newest first —
    the stack of notes a user who skipped N versions needs to read.

    Best effort by design: ANY failure (network, rate limit, malformed
    payload) returns [] so the caller degrades to the single latest release's
    notes. Aggregation must never cost the user the update itself."""
    try:
        url = f"{api_base}/repos/{repo}/releases?per_page={_PER_PAGE}"
        with http_get(http, url,
                      accept="application/vnd.github+json") as response:
            feed = json.loads(response.read().decode("utf-8"))
        newer = [rel for rel in feed
                 if not rel.get("draft") and not rel.get("prerelease")
                 and is_newer(rel.get("tag_name") or "", current)]
        # Sort by PARSED VERSION, never publish order: a backport published
        # after a higher minor must not jump to the top of the stack.
        newer.sort(key=lambda rel: parse_version(rel.get("tag_name") or ""),
                   reverse=True)
        return [notes_from_release(rel) for rel in newer]
    except Exception:
        log.info("release history unavailable", exc_info=True)
        return []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_release_feed.py -q`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/release_feed.py tests/test_release_feed.py
git commit -m "feat(update): read every release newer than the installed one

missed_releases() pages the GitHub releases feed once and returns the notes
for every version the user skipped, newest first BY PARSED VERSION (a
backport published after a higher minor must not sort to the top).

Best-effort on purpose: network error, rate limit, or a malformed payload
all return [] rather than raising, so a dead history endpoint can never cost
the user the update offer itself."
```

---

### Task 3: Stack the notes on `UpdateInfo` and `status()`

**Files:**
- Modify: `src/sm64_events/core/updater.py` (`UpdateInfo`, `check_for_update`,
  `_fake`, `status`)
- Modify: `tests/test_updater.py` (new `RELEASES` route + four tests)
- Modify: `CLAUDE.md` (amend the updater row)

**Interfaces:**
- Consumes: `missed_releases`, `notes_from_release`, `ReleaseNotes`,
  `is_newer` from `core/release_feed.py`.
- Produces, for Task 4: `GET /api/update/status` gains
  `"releases": [{"version": str, "date": str, "notes": str}, …]`, newest
  first, always at least one entry when `update_available` is true.

- [ ] **Step 1: Write the failing tests**

In `tests/test_updater.py`, add the list-endpoint route constant directly
below the existing `LATEST` constant:

```python
LATEST = "https://api.github.com/repos/griffinbeels/SM64-Trainer/releases/latest"
RELEASES = ("https://api.github.com/repos/griffinbeels/SM64-Trainer"
            "/releases?per_page=100")
```

Then add these four tests immediately after
`test_check_keeps_notes_without_marker`:

```python
def test_check_aggregates_every_missed_version_newest_first(tmp_path):
    """A user several releases behind must see EVERY skipped version's
    notes, not just the newest release's."""
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"X"})
    rel = _json.loads(routes[LATEST])
    rel["body"] = "newest notes"
    rel["published_at"] = "2026-07-23T00:00:00Z"
    routes[LATEST] = _json.dumps(rel).encode()
    routes[RELEASES] = _json.dumps([
        {"tag_name": "v2.0.0", "body": "newest notes",
         "published_at": "2026-07-23T00:00:00Z"},
        {"tag_name": "v1.5.0", "body": "middle notes",
         "published_at": "2026-07-10T00:00:00Z"},
        {"tag_name": "v1.0.0", "body": "already installed",
         "published_at": "2026-06-01T00:00:00Z"},
    ]).encode()
    info = check_for_update("1.0.0", http=_fake_http(routes))
    assert [(row.version, row.notes) for row in info.releases] == [
        ("2.0.0", "newest notes"), ("1.5.0", "middle notes")]
    assert info.releases[0].date == "2026-07-23"
    assert info.notes == "newest notes"     # single-version field unchanged


def test_check_still_offers_when_release_history_is_unavailable(tmp_path):
    """The list endpoint is best-effort — losing it must not lose the OFFER.
    _fake_http raises for any unmapped url, so RELEASES is already dead."""
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"X"})
    info = check_for_update("1.0.0", http=_fake_http(routes))
    assert info is not None
    assert [row.version for row in info.releases] == ["2.0.0"]
    assert info.notes == "notes here"


def test_check_ignores_history_newer_than_the_offered_release(tmp_path):
    """GitHub's 'latest' is the most RECENT publish, not the highest version.
    A backport published last would otherwise stack notes for a version this
    update does not install."""
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"X"})
    routes[RELEASES] = _json.dumps([
        {"tag_name": "v3.0.0", "body": "not installed by this update",
         "published_at": "2026-07-25T00:00:00Z"},
        {"tag_name": "v2.0.0", "body": "newest notes",
         "published_at": "2026-07-23T00:00:00Z"},
    ]).encode()
    info = check_for_update("1.0.0", http=_fake_http(routes))
    assert [row.version for row in info.releases] == ["2.0.0"]


def test_status_carries_the_release_stack(tmp_path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    routes[RELEASES] = _json.dumps([
        {"tag_name": "v2.0.0", "body": "newest notes",
         "published_at": "2026-07-23T00:00:00Z"},
        {"tag_name": "v1.5.0", "body": "middle notes",
         "published_at": "2026-07-10T00:00:00Z"},
    ]).encode()
    st = _svc(tmp_path, _fake_http(routes)).status()
    # Pin only the keys this feature owns; the rest of the payload is other
    # features' and must stay unpinned.
    assert [row["version"] for row in st["releases"]] == ["2.0.0", "1.5.0"]
    assert st["releases"][0]["date"] == "2026-07-23"
    assert st["releases"][1]["notes"] == "middle notes"
```

`test_status_carries_the_release_stack` calls `_svc`, which is defined lower
in the file — move this one test below `_svc`'s definition (just after
`test_status_reports_available_with_download_bytes`) rather than forward-
referencing it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_updater.py -q`
Expected: FAIL — `AttributeError: 'UpdateInfo' object has no attribute 'releases'`
(and `KeyError: 'releases'` for the status test).

- [ ] **Step 3: Implement — `UpdateInfo` gains the stack**

In `src/sm64_events/core/updater.py`:

```python
@dataclass
class UpdateInfo:
    version: str
    notes: str
    html_url: str
    zip_url: str
    zip_sha_url: str
    manifest_url: str
    manifest_sha_url: str
```
becomes
```python
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
```

- [ ] **Step 4: Implement — `check_for_update` aggregates**

```python
        # The body's leading first-time-setup section is for the GitHub page
        # only, and legacy bodies title themselves — release_feed.strip_body
        # is THE rule for both shapes.
        notes = strip_body(rel.get("body") or "")
        return UpdateInfo(
            version=tag.lstrip("vV"),
            notes=notes,
            html_url=rel.get("html_url") or "",
            zip_url=assets[ZIP_ASSET],
            zip_sha_url=assets[ZIP_ASSET + ".sha256"],
            manifest_url=assets[MANIFEST_ASSET],
            manifest_sha_url=assets[MANIFEST_ASSET + ".sha256"])
```
becomes
```python
        # The body's leading first-time-setup section is for the GitHub page
        # only, and legacy bodies title themselves — release_feed.strip_body
        # is THE rule for both shapes.
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
```

- [ ] **Step 5: Implement — `status()` exposes the stack**

```python
            "download_bytes": self._plan.download_bytes if self._plan else None,
        }
```
becomes
```python
            "download_bytes": self._plan.download_bytes if self._plan else None,
            "releases": [{"version": row.version, "date": row.date,
                          "notes": row.notes}
                         for row in info.releases] if info else [],
        }
```

- [ ] **Step 6: Implement — the dev fake shows a stack**

```python
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
            zip_url="", zip_sha_url="", manifest_url="", manifest_sha_url="")
```
becomes
```python
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
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. `tests/test_update_cycle.py` still passes: its `_range_http`
raises `KeyError` for the unmapped list url, which `missed_releases` swallows.

- [ ] **Step 8: Amend the module-map row**

In `CLAUDE.md`, in the `| Self-update orchestrator |` row, replace:

```
`check_for_update` (needs ALL FOUR assets: zip+manifest+both `.sha256`s — no unverified bytes ever applies)
```
with
```
`check_for_update` (needs ALL FOUR assets: zip+manifest+both `.sha256`s — no unverified bytes ever applies; also stacks `releases` = the notes for EVERY version between the installed one and the offered tag, newest first, via `release_feed.missed_releases` — best-effort, so losing the history never costs the offer)
```

- [ ] **Step 9: Commit**

```bash
git add src/sm64_events/core/updater.py tests/test_updater.py CLAUDE.md
git commit -m "feat(update): carry every missed version's notes to the popup

UpdateInfo.releases (and status()['releases']) now stack the notes for every
version between the installed one and the offered release, newest first, so
a user 10 versions behind reads all 10 sets instead of only the newest.

Clamped to the offered tag: GitHub's 'latest' is the most RECENT publish, so
a backport published afterwards would otherwise show notes for a version the
update does not install. History failure falls back to the offered release
alone — the offer never depends on aggregation."
```

---

### Task 4: Render the stack in the popup

**Files:**
- Modify: `src/sm64_events/ui/components/update.js`
- Modify: `src/sm64_events/ui/index.html:199-204` (the `.update-notes` rules)

**Interfaces:**
- Consumes: `t.update.releases` — `[{version, date, notes}, …]` newest first
  (Task 3); falls back to `t.update.notes` when absent or empty.
- Produces: nothing downstream.

There is no JS test harness in this repo; verification is the dev server plus
a human eyeball (Step 4).

- [ ] **Step 1: Render one block per version**

In `src/sm64_events/ui/components/update.js`, add this component immediately
after `renderNotes` (keep `renderNotes` itself unchanged — it renders ONE
body):

```js
// One block per missed version, newest first. `releases` comes from
// status(); the `fallback` single-body path covers a status payload without
// it (older server mid-update, or a hand-rolled response).
function NotesStack({ releases, fallback }) {
  const rows = (releases && releases.length)
    ? releases
    : [{ version: "", date: "", notes: fallback }];
  return html`<div class="update-notes">
    ${rows.map((r, i) => html`
      <div class=${i ? "update-rel sep" : "update-rel"}>
        ${r.version
          ? html`<div class="update-ver">v${r.version}${
              r.date ? html`<span class="update-date">${r.date}</span>` : ""}
            </div>`
          : ""}
        <div dangerouslySetInnerHTML=${{ __html: renderNotes(r.notes || "") }}></div>
      </div>`)}
  </div>`;
}
```

Then replace the popup's single notes div:

```js
  return html`<${Modal} title=${`Update available — v${st.latest}`}>
    <div class="meta">You're on v${st.current}.</div>
    <div class="update-notes"
         dangerouslySetInnerHTML=${{ __html: renderNotes(st.notes) }}></div>
```
with
```js
  const missed = (st.releases || []).length;
  return html`<${Modal} title=${`Update available — v${st.latest}`}>
    <div class="meta">You're on v${st.current}.${
      missed > 1 ? ` ${missed} versions of changes.` : ""}</div>
    <${NotesStack} releases=${st.releases} fallback=${st.notes} />
```

(`const missed` goes with the other `const` declarations above the `return`,
after `const pct = …`.)

- [ ] **Step 2: Style the stack and give it its own scroll**

In `src/sm64_events/ui/index.html`, replace:

```css
  .update-notes { background: #14161a; border: 1px solid #2c3140;
    border-radius: 6px; padding: .5rem .7rem; margin: .6rem 0; font-size: .9em;
    line-height: 1.5; }
```
with
```css
  /* max-height is load-bearing, not cosmetic: .modal already scrolls at
     80vh, so without an inner scroll a 10-version backlog pushes
     Update/Skip/Later below the fold. */
  .update-notes { background: #14161a; border: 1px solid #2c3140;
    border-radius: 6px; padding: .5rem .7rem; margin: .6rem 0; font-size: .9em;
    line-height: 1.5; max-height: 46vh; overflow: auto; }
  .update-rel.sep { border-top: 1px solid #2c3140; margin-top: .7rem;
    padding-top: .6rem; }
  .update-ver { color: #ffd75f; font-weight: 600; margin-bottom: .2em; }
  .update-date { color: #8a93a5; font-weight: 400; font-size: .85em;
    margin-left: .4em; }
```

- [ ] **Step 3: Verify the payload against a running server**

```powershell
$env:SM64_UPDATE_FAKE = "1"
uv run python -m sm64_events.main
```
(run it in the background; it binds **:8065** from source)

Then, in another shell:

```powershell
curl.exe -s http://127.0.0.1:8065/api/update/status
```

Expected: JSON whose `releases` is a 3-element array with versions
`9.9.9`, `9.9.8`, `9.9.7` and dates `2026-07-23`, `2026-07-20`, `2026-07-18`.

- [ ] **Step 4: Human eyeball (this project's UI gate)**

Ask the user to open `http://127.0.0.1:8065/` with `SM64_UPDATE_FAKE=1` still
set and confirm:
1. Three version headers — **v9.9.9**, **v9.9.8**, **v9.9.7** — each with its
   dimmed date and a divider between them.
2. The subtitle reads `You're on v… 3 versions of changes.`
3. **Update now / Skip this version / Later stay visible** — the notes box
   scrolls internally rather than pushing the buttons off-screen.
4. Markdown still renders as before inside each block (bullets, bold, code,
   soft-wrapped lines joined).

Do not proceed to the commit until the user confirms. Then stop the server
and clear `$env:SM64_UPDATE_FAKE`.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/components/update.js src/sm64_events/ui/index.html
git commit -m "feat(ui): stack every missed version's notes in the update popup

One block per skipped version, newest first, each under its own header and
date. Falls back to the single-body render when status() carries no
'releases'.

.update-notes gets its own max-height + scroll: .modal already scrolls at
80vh, so a 10-version backlog would otherwise push Update/Skip/Later below
the fold and leave the popup looking actionless."
```

---

## Self-review notes

Checked against the spec:

| Spec section | Task |
|---|---|
| §5 `core/release_feed.py` (moves, `strip_body`, `notes_from_release`) | Task 1 |
| §5 `missed_releases` | Task 2 |
| §6 `updater.py` (`UpdateInfo.releases`, `check_for_update`, `status()`, `_fake`) | Task 3 |
| §7 UI + CSS | Task 4 |
| §8 error handling (history failure, malformed body, rate limit) | Task 2 Step 1 (two tests), Task 3 Step 1 (`…still_offers_when_release_history_is_unavailable`) |
| §9 testing | Tasks 1–3 |
| §10 docs (module map rows) | Task 1 Step 8, Task 3 Step 8 |

Two deliberate deviations from the spec, both noted at their step:

1. **`releases` is a `tuple`, not a `list`,** on `UpdateInfo` — an immutable
   default needs no `field(default_factory=…)`. `status()` still emits a JSON
   array, so the wire contract in §6 is unchanged.
2. **`check_for_update` clamps the history to the offered tag** (Task 3 Step
   4). The spec did not call this out; it is required because GitHub's
   `latest` is the most recent *publish*, not the highest version.
