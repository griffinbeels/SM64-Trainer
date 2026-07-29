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
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

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


# --- the timeline picker: "record what I just did" (Task 13, spec
# 2026-07-28-multi-step-segments) -------------------------------------------
# Three states in one modal (pick start -> pick end -> review): the user
# points at what they just did instead of hand-authoring TRIGGERS clauses.
# Consumes Task 8 (backtest), Task 11 (timeline), Task 12 (synthesize).

SEGMENT_TIMELINE_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
                       / "ui" / "components" / "segmenttimeline.js")
SEGMENT_TIMELINE_JS_SOURCE = strip_comments(
    SEGMENT_TIMELINE_JS.read_text(encoding="utf-8"))


def _fetches_the_timeline_with_the_view(source: str) -> bool:
    """The endpoint AND the view param, in one interpolated call.

    A bare `"/api/segments/timeline" in source` passes while the component
    fetches a hard-coded `view=steps` forever — which is precisely the
    concern the toggle below exists to answer, so the two must be pinned
    together or the pair can drift apart silently.
    """
    stripped = strip_comments(source)
    return bool(re.search(r"/api/segments/timeline\?[^`\"']*view=\$\{view\}",
                          stripped))


def _a_row_click_sets_each_end(source: str) -> bool:
    stripped = strip_comments(source)
    return "setStartRow(row)" in stripped and "setEndRow(row)" in stripped


def _offers_a_view_toggle(source: str) -> bool:
    """A control that WRITES the view, not two loose string literals.

    Task 11's own carried concern: the default (`view=steps`) hides the
    rarer reset/spawn-triggered starts, reachable only one query param away
    with nothing in the picker offering it. `'"all"' in source` would stay
    green if both words survived anywhere for any reason — including in the
    fetch URL this component already builds.
    """
    stripped = strip_comments(source)
    return bool(re.search(r'setView\([^)]*"all"[^)]*"steps"[^)]*\)', stripped))


def test_the_timeline_component_fetches_the_recent_journal():
    assert _fetches_the_timeline_with_the_view(SEGMENT_TIMELINE_JS_SOURCE)


def test_a_row_click_sets_the_start_or_the_end():
    assert _a_row_click_sets_each_end(SEGMENT_TIMELINE_JS_SOURCE)


def test_the_view_toggle_reaches_view_all():
    assert _offers_a_view_toggle(SEGMENT_TIMELINE_JS_SOURCE)


def test_the_timeline_guards_can_still_fail():
    """The three guards above shipped as bare substring checks (Task 13).
    Probed here in both directions, the rule source_scan.py states: a
    comment-only sample must NOT satisfy them, a real-code sample must.
    """
    comment_only = (
        '// Fetches /api/segments/timeline?limit=200&view=steps and lets a\n'
        '// row click call setStartRow(row) / setEndRow(row); the toggle\n'
        '// flips setView between "all" and "steps".\n')
    assert not _fetches_the_timeline_with_the_view(comment_only)
    assert not _a_row_click_sets_each_end(comment_only)
    assert not _offers_a_view_toggle(comment_only)

    assert _fetches_the_timeline_with_the_view(
        'getJSON(`/api/segments/timeline?limit=200&view=${view}`)')
    assert _a_row_click_sets_each_end(
        'onclick=${() => { setStartRow(row); setEndRow(row); }}')
    assert _offers_a_view_toggle(
        'onchange=${(e) => setView(e.target.checked ? "all" : "steps")}')

    # The specific weakness each replacement closes: the OLD assertions all
    # pass against these, the new ones must not.
    assert not _fetches_the_timeline_with_the_view(
        'getJSON("/api/segments/timeline?limit=200&view=steps")')
    assert not _offers_a_view_toggle(
        'const LABELS = { all: "all", steps: "steps" };')


def _saves_as_a_loose_segment(source: str) -> bool:
    """The real save call, comment-immune (source_scan.py) -- a docstring or
    comment describing "saves as a loose segment" would satisfy a bare
    substring check just as easily as the real POST body. Pin the ACTUAL
    call site: the method+path pair and the match_mode literal both present
    in the same COMMENT-STRIPPED source (the function strips it itself, so
    a caller can feed it either raw or already-stripped text)."""
    stripped = strip_comments(source)
    return ('"POST", "/api/segments"' in stripped
            and 'match_mode: "loose"' in stripped)


def test_save_posts_the_recorded_definition_as_a_loose_segment():
    assert _saves_as_a_loose_segment(SEGMENT_TIMELINE_JS_SOURCE)


def test_the_save_loose_guard_can_still_fail():
    comment_only = (
        '// save() POSTs the recording to "/api/segments" as a\n'
        '// match_mode: "loose" definition once the backtest has returned.\n')
    assert not _saves_as_a_loose_segment(comment_only)
    real_code = ('await send("POST", "/api/segments", '
                 '{ ...body, match_mode: "loose" });')
    assert _saves_as_a_loose_segment(real_code)


def _save_button_waits_for_the_backtest(source: str) -> bool:
    """Save's own disabled expression, comment-immune (strips it itself, so
    a caller can feed either raw or already-stripped text). Not a bare
    "btReport" substring check -- that would stay green even if the button
    were unconditionally enabled and btReport were merely READ somewhere
    else on the page (e.g. rendered in the summary line)."""
    return "disabled=${!btReport" in strip_comments(source)


def test_the_backtest_result_renders_before_save_is_enabled():
    assert _save_button_waits_for_the_backtest(SEGMENT_TIMELINE_JS_SOURCE)


def test_the_save_disabled_guard_can_still_fail():
    comment_only = (
        '// Save is disabled=${!btReport || saving} until the backtest\n'
        '// has returned -- see runBacktest above.\n')
    assert not _save_button_waits_for_the_backtest(comment_only)
    real_code = 'disabled=${!btReport || saving} onclick=${save}'
    assert _save_button_waits_for_the_backtest(real_code)


# --- backtestSummary's arm count (live-audit regression, 2026-07-28) --------
# Attempts are only written when an arm CLOSES (tracking/backtest.py's
# BacktestReport.arms docstring: "THE AMBIGUITY BacktestReport.arms closes").
# backtestSummary used to count report.attempts.length as the arm count, so a
# definition that arms and is silently disarmed every time understated its own
# arm count -- and, whenever EVERY arm ended that way (attempts.length == 0),
# fell through to the flatly false "Never armed anywhere in your history."
# These tests run the REAL function (extracted from source and evaluated by
# node — the same technique tests/test_cross_language_parity.py uses for a
# component that pulls in Preact), not a restatement of its logic, because a
# regex over the source text can confirm a literal is present but cannot
# confirm what number actually comes out for a given report.

pytestmark_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")


def _extract_function(source: str, name: str) -> str:
    """The source text of one top-level `function NAME(...) { ... }`,
    brace-matched -- unlike the `const NAME = ...;` declarations
    tests/test_cross_language_parity.py extracts, a function declaration has
    no trailing `;` to anchor on, and its body may itself contain braces
    (object literals, template `${}` blocks). Comments are stripped first so
    a commented-out older version of the function is never the one picked up
    (tests/source_scan.py)."""
    stripped = strip_comments(source)
    start_match = re.search(rf"function\s+{name}\s*\([^)]*\)\s*\{{", stripped)
    assert start_match, f"no `function {name}(...) {{` found in source"
    depth = 0
    for index in range(start_match.end() - 1, len(stripped)):
        if stripped[index] == "{":
            depth += 1
        elif stripped[index] == "}":
            depth -= 1
            if depth == 0:
                return stripped[start_match.start():index + 1]
    raise AssertionError(f"unbalanced braces extracting function {name}")


def _run_backtest_summary(reports: list[dict]) -> list[str]:
    fn_source = _extract_function(
        SEGMENTS_JS.read_text(encoding="utf-8"), "backtestSummary")
    script = (f"{fn_source}\n"
              f"const reports = {json.dumps(reports)};\n"
              "console.log(JSON.stringify(reports.map(backtestSummary)));")
    result = subprocess.run(["node", "--input-type=module", "-"], input=script,
                            capture_output=True, text=True, timeout=60,
                            encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytestmark_node
def test_arms_greater_than_zero_never_reads_as_never_armed():
    # The live-measured shape from the bug report: 67 arms, 0 fires, 29
    # CLOSED attempts (all non-success -- so attempts.length undercounts arms
    # rather than reading zero), 0 unclosed.
    live_shape = {"fires": 0, "attempts": [{}] * 29, "unclosed": [], "arms": 67}
    never_armed = {"fires": 0, "attempts": [], "unclosed": [], "arms": 0}
    [live_message, never_armed_message] = _run_backtest_summary(
        [live_shape, never_armed])
    assert "never armed" not in live_message.lower(), (
        f"a definition that armed 67 times must not read as never armed: "
        f"{live_message!r}")
    assert "never armed" in never_armed_message.lower()


@pytestmark_node
def test_the_shown_arm_count_is_report_arms_not_attempts_length():
    # Same live-measured shape: the count shown must be 67 (arms), never 29
    # (attempts.length) -- the bug this whole task exists to close.
    report = {"fires": 0, "attempts": [{}] * 29, "unclosed": [], "arms": 67}
    [message] = _run_backtest_summary([report])
    assert "67" in message, f"expected the arm count 67 in {message!r}"
    assert "29" not in message, (
        f"attempts.length (29) leaked into the arm-count sentence: {message!r}")


@pytestmark_node
def test_arms_with_no_completion_avoids_the_wiped_data_false_claim():
    # Unlike the recorder (segmenttimeline.js only ever backtests
    # replaces: null), the Builder backtests a real `replaces` when editing
    # an existing segment, so arms>0/fires=0/unclosed=[] here can ALSO mean
    # "it fired, and those attempts were wiped" (backtest.py replays
    # journaled data_wiped clears against `current`) -- not only "it never
    # completed". "never completed successfully" would be false in that case.
    report = {"fires": 0, "attempts": [], "unclosed": [], "arms": 3}
    [message] = _run_backtest_summary([report])
    assert "never completed successfully" not in message
    assert "no completion is recorded" in message


@pytestmark_node
def test_fires_and_unclosed_branches_are_unaffected():
    fired = {"fires": 2, "attempts": [{}, {}, {}], "unclosed": [], "arms": 5}
    unclosed = {"fires": 0, "attempts": [], "unclosed": [{}], "arms": 4}
    [fired_message, unclosed_message] = _run_backtest_summary([fired, unclosed])
    assert fired_message == "2 fires out of 3 attempts in your history."
    assert unclosed_message == (
        "Never fired — but it DID arm, and never closed. See below.")


def test_the_arm_count_extraction_can_still_fail():
    # Probed in both directions (tests/source_scan.py's rule): the extractor
    # must find the real function and must NOT find a commented-out one.
    real = _extract_function(SEGMENTS_JS.read_text(encoding="utf-8"),
                              "backtestSummary")
    assert "report.arms" in real
    commented = ("// function backtestSummary(report) { return 'OLD'; }\n"
                 "function backtestSummary(report) { return report.arms; }\n")
    extracted = _extract_function(commented, "backtestSummary")
    assert "OLD" not in extracted
    assert "return report.arms;" in extracted
