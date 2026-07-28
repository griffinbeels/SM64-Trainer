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

from source_scan import strip_comments
from sm64_events.server.api import SegmentBody, SegmentPatch

SEGMENTS_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
               / "ui" / "components" / "segments.js")

# Comment-stripped ONCE and shared by every test in this file (source_scan.py)
# -- a raw substring scan can't tell code from prose, and this file shipped
# exactly that hole (Task 8 review, 2026-07-28): the runBacktest block
# comment quotes "Try it against my history" in prose, so a bare check on
# that phrase against RAW source stayed green after the real <button> was
# deleted outright. See test_editor_offers_a_backtest_preview_beside_save.
SEGMENTS_JS_SOURCE = strip_comments(SEGMENTS_JS.read_text(encoding="utf-8"))


def _save_fields() -> list[str]:
    match = re.search(r"SAVE_FIELDS\s*=\s*\[([^\]]*)\]", SEGMENTS_JS_SOURCE)
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
    assert "created_utc: _c, ...body" not in SEGMENTS_JS_SOURCE, (
        "the denylist spread is back — GET rows carry db-only columns that "
        "SegmentPatch rejects (see this file's docstring)")


# --- grouped library (spec 2026-07-24-segment-origin-categories) -----------

def test_library_groups_by_origin_through_the_shared_primitives():
    assert "buildTree" in SEGMENTS_JS_SOURCE and "GroupedList" in SEGMENTS_JS_SOURCE
    assert "sm64.segOriginsOpen" in SEGMENTS_JS_SOURCE   # its OWN new open-set key


def test_library_groups_come_from_the_server_stamp_not_a_js_copy():
    # The JS must never re-derive region membership — one taxonomy, server-side.
    # Name the DERIVATION artifacts, not the word "WORLD_EDGES": that string
    # legitimately appears in prose (the dropdown filter's comment cites
    # addresses.WORLD_EDGES_* by name, which is what makes it greppable), and
    # an assertion that cannot tell code from a comment gets "fixed" by
    # rewording the comment — which is exactly what happened once.
    # "origin.region" is dead here (that literal never appears — "origin ??
    # {}" is what passes, review M9); only assert the half that's real.
    assert "originOf(segment).region ?? null" in SEGMENTS_JS_SOURCE
    for derivation in ("world_regions", "CASTLE_REGION", "region_for_node"):
        assert derivation not in SEGMENTS_JS_SOURCE, derivation


def test_search_opens_matching_groups():
    # Everything starts collapsed; a search that dropped its hits into shut
    # boxes would look broken. Assert the actual predicate, not just the
    # word "forceOpen" — a comment mentioning it would satisfy a bare
    # substring check (review M9).
    assert "forceOpen=${() => needle.length > 0}" in SEGMENTS_JS_SOURCE


# --- editor origin override (spec 2026-07-24-segment-origin-categories) ----

def test_editor_offers_an_origin_override_with_the_detected_value_visible():
    # Both substrings are prose-satisfiable alone (review M9) — pin the real
    # call site and the real option label together.
    assert "`/api/segments/${initial.id}/origin`" in SEGMENTS_JS_SOURCE
    # "Auto" must NAME what was detected, or a wrong classification is
    # invisible to the person who has to fix it.
    assert 'Auto (${detected ? detected.label : "Anywhere"})' in SEGMENTS_JS_SOURCE


def test_origin_override_is_offered_only_for_saved_segments():
    # The override is keyed by id, so an unsaved segment has nowhere to hang
    # one — same rule the icon override follows. Pin the "id != null" core
    # only, not the full "initial && initial.id != null" expression — an
    # equivalent `initial?.id != null` rewrite shouldn't break this (M9).
    assert "id != null" in SEGMENTS_JS_SOURCE


# --- shared entity picker (spec 2026-07-25-shared-entity-picker) -----------
# Superseded by the icon modal (spec 2026-07-25-entity-picker-icons): the
# select-based GroupedPicker this section originally pinned is gone, replaced
# by EntityPicker below. levelOptions and the topology filter are unchanged
# by the control swap.

def test_clause_params_use_the_modal_picker():
    assert "EntityPicker" in SEGMENTS_JS_SOURCE
    assert "GroupedPicker" not in SEGMENTS_JS_SOURCE   # the select is gone


def test_the_topology_filter_still_lives_here():
    # Unchanged by the control swap: the picker never learns about world edges.
    assert "allowedIds" in SEGMENTS_JS_SOURCE
    assert "allow=${" in SEGMENTS_JS_SOURCE


def test_icons_are_resolved_by_the_call_site():
    # iconFor is the caller's, so the picker stays domain-free.
    assert "iconFor=${" in SEGMENTS_JS_SOURCE
    assert "optionIcon" in SEGMENTS_JS_SOURCE


# --- backtest preview (Task 8, spec 2026-07-28-multi-step-segments) --------
# The whole point of tracking/backtest.py: find out whether a candidate
# definition would have worked BEFORE saving it, not live mid-run.

def _has_backtest_button_label(source: str) -> bool:
    """The real backtest button's ternary, comment-immune (source_scan.py).

    Not a bare "Try it against my history" substring check: the block
    comment right above runBacktest quotes that exact phrase in prose to
    explain the feature, so a raw-source check on the phrase alone stayed
    green after the real <button> element was deleted outright (Task 8
    review, 2026-07-28) -- see test_the_backtest_button_guard_can_still_fail.
    """
    return '"Testing…" : "Try it against my history"' in strip_comments(source)


def test_editor_offers_a_backtest_preview_beside_save():
    assert '"/api/segments/backtest"' in SEGMENTS_JS_SOURCE
    assert _has_backtest_button_label(SEGMENTS_JS.read_text(encoding="utf-8"))


def test_backtest_preview_names_the_unclosed_arm_diagnostic():
    # THE diagnostic this feature exists for: a definition that looks right
    # and never fires. Pin the actual distinguishing sentence, not just the
    # word "unclosed" -- a comment alone would satisfy a bare substring check
    # (ui-core.md's guard-can-still-fail rule).
    assert "Never fired — but it DID arm, and never closed" in SEGMENTS_JS_SOURCE


def test_backtest_preview_sends_the_full_unsaved_form_not_just_save_fields():
    # Regression this guards against: sending only SAVE_FIELDS would silently
    # drop an existing segment's match_mode/waypoints from the preview and
    # backtest against the wrong matcher branch (every non-plain, non-loose
    # seeded movement).
    assert "BACKTEST_FIELDS" in SEGMENTS_JS_SOURCE
    assert '"waypoints", "category", "match_mode"' in SEGMENTS_JS_SOURCE


def test_the_backtest_button_guard_can_still_fail():
    # source_scan.py: a guard a comment can satisfy is no guard at all. Feed
    # the exact shape that broke this once -- a comment quoting the button's
    # label in prose, the runBacktest docstring's own opening line -- and
    # confirm it does NOT pass; then confirm the real ternary does.
    comment_only = (
        '// "Try it against my history" -- the whole point is finding out\n'
        '// BEFORE Save, so this sends whatever is CURRENTLY in the form.\n')
    assert not _has_backtest_button_label(comment_only)
    real_code = 'html`${btBusy ? "Testing…" : "Try it against my history"}`'
    assert _has_backtest_button_label(real_code)
