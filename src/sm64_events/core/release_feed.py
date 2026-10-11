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
import urllib.error
from dataclasses import dataclass

from sm64_events.core.update_plan import PATCH_NOTES_MARKER
from sm64_events.core.release_contract import is_forward, version_key

log = logging.getLogger("sm64.releases")

_UA = "SM64Trainer-updater"
# The legacy notes helper uses one page; release discovery below paginates.
_PER_PAGE = 100
_MAX_PAGES = 10
_MAX_JSON_BYTES = 8 << 20
# A legacy body's own title line, e.g. '## SM64 Trainer v1.2.0 - first release'.
# The popup renders its own version header, so this would render twice.
# This heuristic only ever sees marker-less bodies, i.e. releases at or below
# v1.3.0 (all audited 2026-07-23) — a future marker-less release would need
# re-checking against it.
_LEGACY_TITLE = re.compile(r"^\s*#{1,6}\s+.*v\d+\.\d+\.\d+")


@dataclass(frozen=True)
class ReleaseNotes:
    """One published release as the popup shows it."""
    version: str        # "1.4.2" - no leading v
    date: str           # "2026-07-23", or "" when published_at is absent
    notes: str          # body reduced by strip_body()


def parse_version(tag: str) -> tuple[int, ...]:
    """Numeric core for legacy displays; is_newer owns prerelease ordering."""
    # Kept for legacy consumers displaying a numeric core. Never use this
    # lossy value for ordering prereleases.
    try:
        return version_key(tag).core
    except ValueError:
        return ()


def is_newer(candidate: str, current: str) -> bool:
    try:
        return is_forward(candidate, current)
    except ValueError:
        return False


def http_get(http, url: str, *, accept: str | None = None, headers=None):
    """The one User-Agent'd request builder both this module and updater.py
    use. `http` is injected so tests never touch the network."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if accept:
        req.add_header("Accept", accept)
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    return http(req)


class ReleaseFeed:
    """Bounded release discovery with conditional metadata requests.

    Cache validators per page: metadata checks never query the ghost service.
    A failed list request still permits the separately fetched stable release.
    A truncated feed is rejected instead of claiming its first page is newest.
    """

    def __init__(self, *, http, repo: str, api_base: str):
        self.http, self.repo, self.api_base = http, repo, api_base
        self._pages = {}
        self.last_error = ""

    def _json(self, url):
        previous = self._pages.get(url)
        headers = {"If-None-Match": previous[0]} if previous and previous[0] else {}
        try:
            with http_get(self.http, url, accept="application/vnd.github+json", headers=headers) as response:
                if getattr(response, "status", 200) == 304 and previous:
                    return previous[1]
                data = response.read(_MAX_JSON_BYTES + 1)
                if len(data) > _MAX_JSON_BYTES:
                    raise ValueError("release feed exceeds size limit")
                doc = json.loads(data.decode("utf-8"))
                etag = getattr(response, "headers", {}).get("ETag", "")
                self._pages[url] = (etag, doc)
                return doc
        except urllib.error.HTTPError as error:
            if error.code == 304 and previous:
                return previous[1]
            raise

    def releases(self) -> list[dict]:
        self.last_error = ""
        latest = None
        base = f"{self.api_base}/repos/{self.repo}/releases"
        try:
            latest = self._json(base + "/latest")
        except Exception:
            log.info("stable release lookup unavailable", exc_info=True)
        rows = []
        try:
            for page in range(1, _MAX_PAGES + 1):
                url = base + f"?per_page={_PER_PAGE}" + (f"&page={page}" if page > 1 else "")
                feed = self._json(url)
                if not isinstance(feed, list) or any(not isinstance(row, dict) for row in feed):
                    raise ValueError("release feed is not a list of releases")
                rows.extend(feed)
                if len(feed) < _PER_PAGE:
                    break
            else:
                raise ValueError("release history exceeds bounded discovery limit")
        except Exception:
            # Partial pagination cannot establish the newest head. Retain the
            # independent stable fallback, not a misleading partial beta head.
            log.info("release history unavailable", exc_info=True)
            self.last_error = "Update check unavailable. Check your connection and try again."
            rows = []
        if isinstance(latest, dict):
            # Stable /latest can lead the list's CDN immediately after publish.
            rows = [row for row in rows if row.get("tag_name") != latest.get("tag_name")]
            rows.append(latest)
        return rows


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
        newer.sort(key=lambda rel: version_key(rel.get("tag_name") or ""),
                   reverse=True)
        return [notes_from_release(rel) for rel in newer]
    except Exception:
        log.info("release history unavailable", exc_info=True)
        return []
