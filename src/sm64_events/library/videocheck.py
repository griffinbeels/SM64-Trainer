"""Is a community video URL still watchable? (task 0098, round 2)

His report, verbatim: "many of the videos are actually unavailable on
YouTube / unavailable on whatever platform. this is actually a big problem
when we have a specific video as the example standard in the table. If a
video is not available for any reason, we should choose the next eligible
video as the example for a given rank standard. If none are available, then
the standard has no hyperlink."

This SUPERSEDES the 2026-08-05 "do not build link-checking" ruling, and the
override is his own: that ruling's premise — a dead link is a nuisance
because a row shows tens of entries — held on the Library page and does not
hold where ONE video is THE example for a standard.

Design:
  * Checks live in ONE committed file (`data/video_checks.seed.json.gz`,
    {url: {status, checked}}), written by `tools/check_videos.py` and read by
    the server at startup. Deliberately NOT part of the library snapshot: an
    in-app sheet refresh rebuilds the snapshot in minutes, a full URL sweep
    takes tens of minutes of network — separate files mean a refresh can
    never silently drop every verdict.
  * A verdict is only ever "ok" or "dead"; anything the checker cannot
    DETERMINE is unknown, is not stored, and is treated as watchable — a
    network hiccup must never delete a working example, so only an explicit
    dead-shaped HTTP status may.
  * Platforms: YouTube and X/Twitter answer through their oEmbed endpoints
    (no API key; 404/400/410 = gone). YouTube 401/403 means EMBEDDING is
    disabled while the watch page works — the table links out rather than
    embeds, so that is `ok`, and calling it dead would delete real examples.
    Direct video files answer by status. Twitch/bsky/everything else has no
    cheap unauthenticated liveness answer: always unknown, never checked,
    never dead.
"""
from __future__ import annotations

import gzip
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

OK, DEAD, UNKNOWN = "ok", "dead", "unknown"

#: How long a stored verdict stays fresh. Both directions on purpose: a live
#: video can be privated tomorrow, and a privated one can come back.
RECHECK_DAYS = 30

_YOUTUBE = re.compile(r"^https?://(www\.|m\.|music\.)?(youtube\.com/(watch|shorts)|youtu\.be/)", re.I)
#: The 11-char video id, wherever the sheet's link style put it. Sheet links
#: are hand-pasted and include `youtu.be/<id>&t=5s` — a `&` where a `?`
#: belongs, which browsers tolerate and oEmbed rejects as an unparseable id
#: (the second false-dead class the first sweep's calibration caught).
_YOUTUBE_ID = re.compile(
    r"(?:youtu\.be/|/shorts/|[?&]v=)([A-Za-z0-9_-]{11})")
_TWITTER = re.compile(r"^https?://(www\.)?(twitter\.com|x\.com)/[^/]+/status/\d+", re.I)
_FILE = re.compile(r"\.(mp4|webm|mov|m4v|gif|mkv)(\?|$)", re.I)

_DEAD_STATUSES = {400, 404, 410}


def probe_url(url: str) -> str | None:
    """The URL whose HTTP status answers "is this watchable", or None when no
    cheap unauthenticated probe exists (→ unknown, never checked)."""
    if _YOUTUBE.search(url):
        # CANONICALIZE to the bare watch URL for the same reason as the tweet
        # branch below: the probe must answer for the VIDEO, not for however
        # the sheet happened to write the link.
        video_id = _YOUTUBE_ID.search(url)
        canonical = (f"https://www.youtube.com/watch?v={video_id.group(1)}"
                     if video_id else url)
        return ("https://www.youtube.com/oembed?format=json&url="
                + quote(canonical, safe=""))
    tweet = _TWITTER.search(url)
    if tweet:
        # CANONICALIZE to the bare status URL: the sheet links tweets as
        # .../status/<id>/video/1, and oEmbed answers 404 for that suffix even
        # when the tweet is alive — measured 2026-08-14, the false-dead class
        # that put 30+ live examples on the dead list on the first sweep.
        return ("https://publish.twitter.com/oembed?url="
                + quote(tweet.group(0), safe=""))
    if _FILE.search(url):
        return url
    return None


def classify(url: str, status: int | None) -> str:
    """A verdict from the probe's HTTP status. `status` None = the request
    itself failed (timeout, DNS) — unknown, never dead."""
    if probe_url(url) is None or status is None:
        return UNKNOWN
    if status in _DEAD_STATUSES:
        return DEAD
    if status == 200:
        return OK
    if _YOUTUBE.search(url) and status in (401, 403):
        return OK          # embed disabled; the watch page itself works
    return UNKNOWN


def run_checks(urls, checks: dict, fetch_status, *, now=None,
               recheck_days: int = RECHECK_DAYS) -> dict:
    """Bring `checks` up to date for `urls`. Returns {"checked": n,
    "skipped_fresh": n, "unknown": n} — mutates `checks` in place.

    `fetch_status(probe_url) -> int | None` is injected so tests never touch
    the network. A fresh stored verdict is kept without a request; an unknown
    result stores nothing, so it is retried next run."""
    now = now or datetime.now(timezone.utc)
    horizon = now - timedelta(days=recheck_days)
    stats = {"checked": 0, "skipped_fresh": 0, "unknown": 0}
    for url in urls:
        probe = probe_url(url)
        if probe is None:
            continue
        stored = checks.get(url)
        if stored:
            checked_at = _parse_when(stored.get("checked"))
            if checked_at and checked_at > horizon:
                stats["skipped_fresh"] += 1
                continue
        verdict = classify(url, fetch_status(probe))
        stats["checked"] += 1
        if verdict == UNKNOWN:
            stats["unknown"] += 1
            continue
        checks[url] = {"status": verdict,
                       "checked": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    return stats


def stale_urls(urls, checks: dict, *, now=None,
               recheck_days: int = RECHECK_DAYS) -> list:
    """The subset of `urls` a sweep must actually probe: checkable at all,
    and without a fresh stored verdict. Pure — the tool pools the network
    over exactly this list, so the pool and `run_checks` cannot disagree
    about what counts as fresh."""
    now = now or datetime.now(timezone.utc)
    horizon = now - timedelta(days=recheck_days)
    out = []
    for url in urls:
        if probe_url(url) is None:
            continue
        stored = checks.get(url)
        if stored:
            checked_at = _parse_when(stored.get("checked"))
            if checked_at and checked_at > horizon:
                continue
        out.append(url)
    return out


def _parse_when(text):
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def dead_urls(checks: dict) -> set:
    """The filter every example-video surface applies. User-attached override
    URLs are exempt BY THE CALLER — a hand-picked example is his fact."""
    return {url for url, verdict in (checks or {}).items()
            if isinstance(verdict, dict) and verdict.get("status") == DEAD}


def load_checks(path) -> dict:
    """{url: {status, checked}} from the committed gz file; {} when absent or
    unreadable — no checks means no filtering, never an error."""
    try:
        raw = gzip.decompress(Path(path).read_bytes())
        data = json.loads(raw)
    except (FileNotFoundError, OSError, ValueError, EOFError):
        return {}
    return data if isinstance(data, dict) else {}


def save_checks(path, checks: dict) -> None:
    payload = json.dumps(checks, sort_keys=True).encode("utf-8")
    # mtime=0, same as the library snapshot: an unchanged set of verdicts
    # produces a byte-identical file, so re-running the tool is diff-quiet.
    Path(path).write_bytes(gzip.compress(payload, mtime=0))
