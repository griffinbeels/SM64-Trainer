# tests/test_segments_editor_ui.py
"""The segment editor's save body vs the server's strict patch model.

Regression 2026-07-24: the Builder (ui/components/segments.js) built its PUT
body by DENYLIST — spreading the GET /api/segments row and stripping
id/created_utc. GET returns raw db rows, so when migration v11 grew them
(seed_key/seed_dirty) the new columns leaked into SegmentPatch
(extra="forbid") and every save of a seeded segment 422'd with
"Extra inputs are not permitted".

The fix is an ALLOWLIST (SAVE_FIELDS in segments.js). This test cross-checks
that list against the pydantic models, so either side changing breaks loudly:
- a field the JS sends that the server stopped accepting -> caught here
  (instead of a 422 in the running app);
- the denylist pattern creeping back -> caught by the no-spread assertion.
"""
import re
from pathlib import Path

from sm64_events.server.api import SegmentBody, SegmentPatch

SEGMENTS_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
               / "ui" / "components" / "segments.js")


def _save_fields() -> list[str]:
    source = SEGMENTS_JS.read_text(encoding="utf-8")
    match = re.search(r"SAVE_FIELDS\s*=\s*\[([^\]]*)\]", source)
    assert match, "segments.js lost its SAVE_FIELDS allowlist"
    return re.findall(r'"(\w+)"', match.group(1))


def test_editor_allowlist_is_accepted_by_both_server_models():
    fields = _save_fields()
    assert fields, "SAVE_FIELDS parsed empty"
    for model in (SegmentBody, SegmentPatch):
        unknown = set(fields) - set(model.model_fields)
        assert not unknown, (
            f"segments.js sends fields {sorted(unknown)} that "
            f"{model.__name__} (extra=forbid) rejects")
    # the editor must send everything it lets the user EDIT — dropping one
    # of these from a PATCH would silently discard the user's change
    assert {"name", "enabled", "start_triggers", "end_triggers",
            "guards"} <= set(fields)


def test_editor_save_never_spreads_the_get_row():
    source = SEGMENTS_JS.read_text(encoding="utf-8")
    assert "created_utc: _c, ...body" not in source, (
        "the denylist spread is back — GET rows carry db-only columns that "
        "SegmentPatch rejects (see this file's docstring)")
