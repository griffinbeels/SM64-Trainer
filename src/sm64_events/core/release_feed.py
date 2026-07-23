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
