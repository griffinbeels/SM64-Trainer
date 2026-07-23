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
