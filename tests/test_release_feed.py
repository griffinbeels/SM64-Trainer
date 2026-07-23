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
