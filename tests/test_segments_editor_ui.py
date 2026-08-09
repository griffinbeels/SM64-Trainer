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
    # of these from a PATCH would silently discard the user's change.
    # match_mode joined 2026-07-29 (final-review finding 4): the Matching
    # control now edits it, so its omission here would be the exact silent-
    # discard bug this test exists to catch.
    assert {"name", "enabled", "start_triggers", "end_triggers",
            "guards", "match_mode"} <= set(fields)


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


# --- match-mode control (final-review finding 4, spec 2026-07-28-multi-step-
# segments) -------------------------------------------------------------------
# match_mode was plumbed end to end (db column, dataclass, validation, vocab,
# API models) with no control reading any of it: every Builder-created segment
# silently became "loose" (SegmentBody's own default) and no definition's mode
# could be seen or changed from the editor.

def test_editor_offers_a_matching_control_driven_by_vocab():
    # The <select>'s options come from vocab.match_modes -- never a hardcoded
    # list of mode keys, which is exactly the second-door shape this repo
    # fails builds over (tests/test_single_source.py).
    assert "vocab.match_modes" in SEGMENTS_JS_SOURCE
    assert ('matchModes.map((mode) => html`<option value=${mode.key}>'
            '${mode.label}</option>`)') in SEGMENTS_JS_SOURCE


def test_matching_control_writes_match_mode_on_change():
    assert ('onchange=${(e) => setD({ ...d, match_mode: e.target.value })}'
            in SEGMENTS_JS_SOURCE)


def test_matching_control_shows_the_mode_description():
    # Two related values (loose vs strict) shown with no explanation read as
    # a bug even when the underlying behaviour is right (the user's standing
    # rule on labelled-vs-unlabelled UI) -- the description comes from vocab
    # too, alongside the label.
    assert "matchModeInfo ? matchModeInfo.description" in SEGMENTS_JS_SOURCE


def test_matching_control_keeps_an_unrecognised_current_value_selectable():
    # A stored value a filtered/rebuilt <select> drops renders BLANK, not the
    # value it actually holds -- this app has been bitten by exactly that
    # shape before (memory: select-value-must-stay-in-options). The appended
    # fallback option keeps d.match_mode selectable even if vocab somehow
    # ships no matching entry for it.
    assert '!matchModeInfo && d.match_mode' in SEGMENTS_JS_SOURCE


def test_new_segment_defaults_match_mode_from_vocab_ordering_not_a_literal():
    # blank.match_mode reads vocab.match_modes[0].key (loose-first by the
    # server's own ordering) rather than naming "loose" as a first choice --
    # the "loose" fallback only covers vocab failing to load at all.
    assert ('match_mode: (vocab.match_modes && vocab.match_modes[0]'
            in SEGMENTS_JS_SOURCE)


def _has_no_hardcoded_match_mode_copy(source: str) -> bool:
    """True when none of MATCH_MODES' server-authored label/description text
    (tracking/segments.py) appears in CODE (comments stripped first) -- a
    second copy here would be exactly the shape tests/test_single_source.py
    fails builds over elsewhere in this app."""
    stripped = strip_comments(source)
    phrases = ("ends only where I said", "cancels if I go off-route",
               "Stays armed through star grabs",
               "Cancels the moment anything happens")
    return not any(phrase in stripped for phrase in phrases)


def test_no_hardcoded_match_mode_copy_in_the_editor():
    assert _has_no_hardcoded_match_mode_copy(SEGMENTS_JS.read_text(encoding="utf-8"))


def test_the_match_mode_copy_guard_can_still_fail():
    # Probed both directions (tests/source_scan.py's rule): a comment quoting
    # the server's wording to explain the feature must not trip this, and a
    # real JS copy of the same wording must.
    comment_only = (
        '// loose reads "Stays armed through star grabs, key grabs and\n'
        '// level changes until the end trigger fires" straight off vocab.\n')
    assert _has_no_hardcoded_match_mode_copy(comment_only)
    real_code = (
        'const LOOSE_LABEL = "Stays armed through star grabs and level '
        'changes";')
    assert not _has_no_hardcoded_match_mode_copy(real_code)


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
    # drop an existing segment's waypoints/category from the preview and
    # backtest against the wrong matcher branch (every seeded movement that
    # carries one). match_mode needed the same treatment until 2026-07-29,
    # when it joined SAVE_FIELDS itself (the new Matching control) — it no
    # longer needs appending here, since BACKTEST_FIELDS spreads SAVE_FIELDS
    # first and therefore always carries it.
    # `waypoints` MOVED into SAVE_FIELDS on 2026-08-03 (the editor grew a Then
    # section and can author them now), so what this pins is the PROPERTY --
    # the preview body carries every field the matcher branches on -- rather
    # than one spelling of the list.
    assert "BACKTEST_FIELDS" in SEGMENTS_JS_SOURCE
    assert '[...SAVE_FIELDS, "category"]' in SEGMENTS_JS_SOURCE
    assert '"waypoints"' in SEGMENTS_JS_SOURCE.split("BACKTEST_FIELDS")[0], (
        "waypoints must reach the backtest body -- it decides which matcher "
        "branch runs, so a preview without it tests a different definition")


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

    # The case that makes strip_comments LOAD-BEARING here, and the reason
    # this block was incomplete until 2026-07-29 (final review, finding 8):
    # the sample above never contains "Testing…" at all, so the helper
    # returns False for it whether or not comments are stripped -- the probe
    # passed while proving nothing about the mechanism it exists to check.
    # A comment quoting the WHOLE ternary is what a real drift looks like:
    # someone deletes the button and leaves the docstring that describes it.
    ternary_in_a_comment = (
        '/* The button reads html`${btBusy ? "Testing…" : "Try it against '
        'my history"}` while a backtest is in flight. */\n')
    assert not _has_backtest_button_label(ternary_in_a_comment)


# --- the RECORDER: "record what I just did" (Task 13, spec 2026-07-28-multi-
# step-segments; rewritten 2026-08-05) --------------------------------------
# ONE surface: a live list of moments, newest first, of which any number can
# be picked to define a segment. The user points at what they just did
# instead of hand-authoring TRIGGERS clauses. Consumes Task 8 (backtest),
# Task 11 (timeline), Task 12 (synthesize).

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


def _a_row_click_toggles_the_pick(source: str) -> bool:
    """A row click TOGGLES membership of the selection, both ways.

    Replaced `setStartRow(row)`/`setEndRow(row)` on 2026-08-05 along with the
    stepper those belonged to. With N picks there is no next step to advance
    to, so un-picking has to be the same gesture as picking -- and pinning
    only the ADD half would stay green against a list you can fill and never
    empty, which is a dead end reached by using the feature correctly.
    """
    stripped = strip_comments(source)
    return ("pickedIds.filter((id) => id !== row.id)" in stripped
            and "[...pickedIds, row.id].sort(" in stripped)


def _asks_what_the_recording_is_a_piece_of(source: str) -> bool:
    """The ONLY door into a subsection, and it must reach the DEFINITION.

    Two halves, both load-bearing: a control that writes the state, and that
    state riding the object `definitionFor` builds. A control writing a value
    nothing sends is exactly the shape of the bug this closes -- `SegmentBody.
    parent` has been real server-side the whole time and no control ever wrote
    one, which is why he asked "what star has subsections? I don't see a way
    to define that?" (2026-08-05).
    """
    stripped = strip_comments(source)
    return ("setParentOptions(" in stripped
            and re.search(r"waypoints,\s+parents,", stripped) is not None)


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


def test_a_row_click_toggles_that_moment_in_and_out_of_the_pick():
    assert _a_row_click_toggles_the_pick(SEGMENT_TIMELINE_JS_SOURCE)


def test_the_recorder_asks_what_the_recording_is_a_piece_of():
    assert _asks_what_the_recording_is_a_piece_of(SEGMENT_TIMELINE_JS_SOURCE)


def test_the_view_toggle_reaches_view_all():
    assert _offers_a_view_toggle(SEGMENT_TIMELINE_JS_SOURCE)


def test_the_timeline_guards_can_still_fail():
    """The three guards above shipped as bare substring checks (Task 13).
    Probed here in both directions, the rule source_scan.py states: a
    comment-only sample must NOT satisfy them, a real-code sample must.
    """
    comment_only = (
        '// Fetches /api/segments/timeline?limit=200&view=steps and lets a\n'
        '// row click do pickedIds.filter((id) => id !== row.id) or else\n'
        '// [...pickedIds, row.id].sort(); the toggle flips setView between\n'
        '// "all" and "steps", and setParentOptions(next) rides the definition\n'
        '// beside waypoints,\n'
        '// parents, so a subsection can be made.\n')
    assert not _fetches_the_timeline_with_the_view(comment_only)
    assert not _a_row_click_toggles_the_pick(comment_only)
    assert not _asks_what_the_recording_is_a_piece_of(comment_only)
    assert not _offers_a_view_toggle(comment_only)

    assert _fetches_the_timeline_with_the_view(
        'getJSON(`/api/segments/timeline?limit=200&view=${view}`)')
    assert _a_row_click_toggles_the_pick(
        'const next = pickedIds.includes(row.id)\n'
        '  ? pickedIds.filter((id) => id !== row.id)\n'
        '  : [...pickedIds, row.id].sort((l, r) => l - r);')
    assert _asks_what_the_recording_is_a_piece_of(
        'onPick=${(id) => { setParentOptions((held) => [...held, id]); }}\n'
        'return { start_triggers: [], waypoints,\n'
        '  parents, match_mode: "loose" };')
    assert _offers_a_view_toggle(
        'onchange=${(e) => setView(e.target.checked ? "all" : "steps")}')

    # The specific weakness each replacement closes: the OLD assertions all
    # pass against these, the new ones must not.
    assert not _a_row_click_toggles_the_pick(
        'const next = [...pickedIds, row.id].sort((l, r) => l - r);')
    assert not _asks_what_the_recording_is_a_piece_of(
        'onPick=${(id) => { setParentOption(id); }}   // written, never sent')
    assert not _fetches_the_timeline_with_the_view(
        'getJSON("/api/segments/timeline?limit=200&view=steps")')
    assert not _offers_a_view_toggle(
        'const LABELS = { all: "all", steps: "steps" };')


def _saves_through_one_definition_builder(source: str) -> bool:
    """The real save call, comment-immune (source_scan.py) -- a docstring
    describing what save() sends would satisfy a bare substring check just as
    easily as the POST body itself.

    CHANGED 2026-08-03. It used to pin `match_mode: "loose"` at the save site,
    because a recording was always loose. It no longer is: a recording that
    declares stops is a PATH and only means anything under the strict matcher.
    What is pinned now is the property that made the old literal safe in the
    first place -- backtest, lint and save all build their definition through
    ONE function, so the thing tested and the thing written cannot differ. A
    save posting its own object literal is exactly the drift this prevents."""
    stripped = strip_comments(source)
    return ('"POST", "/api/segments",' in stripped
            and "definitionFor(synth, required)" in stripped
            and "function definitionFor(" in stripped)


def test_save_posts_the_definition_the_backtest_actually_tested():
    assert _saves_through_one_definition_builder(SEGMENT_TIMELINE_JS_SOURCE)


def test_the_save_builder_guard_can_still_fail():
    comment_only = (
        '// save() POSTs definitionFor(synth, required) to "/api/segments",\n'
        '// the same object function definitionFor( built for the backtest.\n')
    assert not _saves_through_one_definition_builder(comment_only)
    real_code = ('function definitionFor(s, r) { return {}; }\n'
                 'await send("POST", "/api/segments", '
                 'definitionFor(synth, required));')
    assert _saves_through_one_definition_builder(real_code)


def _recordings_save_strict(source: str) -> bool:
    return 'match_mode: "strict"' in strip_comments(source)


def test_a_recording_saves_strict_stops_or_no_stops():
    """Round 15, his ruling verbatim: "They should also be default Strict."
    The previous rule (strict only once a stop was declared) kept a stop-less
    recording byte-compatible with the pre-waypoint tool; that guarantee is
    retired on his word — a recorded piece voids when you deviate. The
    BUILDER's blank default is a different control and untouched
    (vocab.match_modes[0], loose-first, its own test above)."""
    assert _recordings_save_strict(SEGMENT_TIMELINE_JS_SOURCE)
    assert 'match_mode: waypoints.length ? "strict" : "loose"' \
        not in strip_comments(SEGMENT_TIMELINE_JS_SOURCE), (
        "the retired stops-only rule is back beside the strict default")


def test_the_mode_rule_guard_can_still_fail():
    comment_only = ('// Recordings save STRICT unconditionally:\n'
                    '// match_mode: "strict" is what save() sends.\n')
    assert not _recordings_save_strict(comment_only)


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


# --- split / merge (Task 18, spec 2026-07-28-multi-step-segments) ---------
# tracking/segments.py::split_definition/merge_definitions (Task 17) sit
# behind two new endpoints; this is their editor UI. Most checks are plain
# substrings against SEGMENTS_JS_SOURCE (already comment-stripped above,
# same as the origin-override / topology-filter checks elsewhere in this
# file) -- a dedicated comment-immune probe is only added for the two wire-
# level POST calls, matching _saves_as_a_loose_segment's rigor for the
# equivalent save call in segmenttimeline.js.

def test_split_is_offered_only_for_a_saved_segment_with_one_waypoint():
    # 0 waypoints has no natural split point to offer; 2+ is refused server-
    # side anyway (folding several into one shared `mid` would silently drop
    # the rest) -- this is the one shape split_definition can act on without
    # asking the author to invent a boundary from nothing.
    assert ("initial.id != null && (initial.waypoints || []).length === 1"
            in SEGMENTS_JS_SOURCE)


def test_split_sends_the_segments_own_waypoint_as_the_mid_boundary():
    # Nothing else for the user to author here -- the panel above is only
    # offered when there is exactly one waypoint, so it IS the boundary.
    assert "mid: initial.waypoints[0]" in SEGMENTS_JS_SOURCE


def test_split_lands_on_the_first_new_half_like_save_lands_on_what_it_saved():
    assert "onSaved(result.first_id)" in SEGMENTS_JS_SOURCE


def test_merge_reuses_the_shared_segment_picker_not_a_new_control():
    # The SAME picker the Routes tab's step editor already uses for a segment
    # candidate (entities.js::segmentOptions + EntityPicker) -- not a second,
    # hand-rolled select built for this one form.
    assert "segmentOptions(mergeCandidates" in SEGMENTS_JS_SOURCE
    assert "${EntityPicker} groups=${mergeGroups}" in SEGMENTS_JS_SOURCE


def test_merge_excludes_the_segment_being_edited_from_its_own_picker():
    assert "def.id !== initial.id" in SEGMENTS_JS_SOURCE


def test_merge_lands_on_the_new_merged_segment_like_save_lands_on_what_it_saved():
    assert "onSaved(result.id)" in SEGMENTS_JS_SOURCE


def _posts_the_split(source: str) -> bool:
    stripped = strip_comments(source)
    return bool(re.search(
        r'"POST",\s*`/api/segments/\$\{initial\.id\}/split`', stripped))


def test_split_posts_to_the_segment_split_endpoint():
    assert _posts_the_split(SEGMENTS_JS_SOURCE)


def _posts_the_merge(source: str) -> bool:
    stripped = strip_comments(source)
    return '"POST", "/api/segments/merge"' in stripped


def test_merge_posts_to_the_segment_merge_endpoint():
    assert _posts_the_merge(SEGMENTS_JS_SOURCE)


def test_split_and_merge_post_guards_can_still_fail():
    comment_only = (
        "// doSplit() posts to `/api/segments/${initial.id}/split` and\n"
        '// doMerge() posts to "/api/segments/merge".\n')
    assert not _posts_the_split(comment_only)
    assert not _posts_the_merge(comment_only)
    assert _posts_the_split(
        'await send("POST", `/api/segments/${initial.id}/split`, body)')
    assert _posts_the_merge(
        'await send("POST", "/api/segments/merge", body)')


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


# --- author-time lint (Task 16, spec 2026-07-28-multi-step-segments) -------
# tracking/lint.py (Task 15) had no server endpoint and no reference under
# ui/ until this task -- POST /api/segments/lint plus these two wire-ups are
# what actually ships it. Two surfaces: the Builder (re-checks on every edit,
# unlike the button-triggered backtest above) and the recorder's review step
# (alongside its own backtest, since lint is what explains an arms=0/fires=0
# result rather than leaving the user at a dead end).

def _lints_on_every_edit(source: str) -> bool:
    """The real lint POST, comment-immune -- a docstring describing "lints on
    every edit" would satisfy a bare substring check just as easily as the
    real call. Pin the wire-level call itself, same rigor as
    _posts_the_split/_posts_the_merge above."""
    return '"POST", "/api/segments/lint"' in strip_comments(source)


def test_editor_lints_on_every_edit():
    assert _lints_on_every_edit(SEGMENTS_JS_SOURCE)
    # Debounced on the FORM itself (not button-triggered like backtest) --
    # the one useEffect in this file keyed on `d` alone.
    assert "}, [d]);" in SEGMENTS_JS_SOURCE


def test_editor_lint_guard_can_still_fail():
    comment_only = ('// The lint effect posts "POST", "/api/segments/lint"\n'
                    '// on every edit, debounced.\n')
    assert not _lints_on_every_edit(comment_only)
    assert _lints_on_every_edit(
        'send("POST", "/api/segments/lint", { definition, segment_id })')


def test_editor_lint_call_excludes_the_definition_being_edited():
    # lint_definition's `duplicate` rule excludes by id (tracking/lint.py) --
    # without sending the definition's OWN id here, an unmodified edit of an
    # existing segment would report itself as a duplicate of its own row.
    assert ("segment_id: initial && initial.id != null ? initial.id : null"
            in SEGMENTS_JS_SOURCE)


def _save_disabled_on_lint_error(source: str) -> bool:
    return "disabled=${lintHasError}" in strip_comments(source)


def test_editor_save_is_disabled_on_a_lint_error():
    assert _save_disabled_on_lint_error(SEGMENTS_JS_SOURCE)
    # Gated on SEVERITY, not mere presence -- a warning must not block Save.
    assert 'lintFindings.some((f) => f.severity === "error")' in SEGMENTS_JS_SOURCE


def test_editor_save_disable_guard_can_still_fail():
    comment_only = ('// Save is disabled=${lintHasError} whenever an error-\n'
                    '// severity finding exists.\n')
    assert not _save_disabled_on_lint_error(comment_only)
    assert _save_disabled_on_lint_error(
        'onclick=${save} disabled=${lintHasError}')


def _lints_alongside_the_recorder_backtest(source: str) -> bool:
    stripped = strip_comments(source)
    # `runLint(body)` became `runLint(definition)` behind a shared `recheck`
    # on 2026-08-03, when toggling a stop had to re-run BOTH checks. What is
    # pinned is still the pairing -- lint runs wherever the backtest runs --
    # not the argument's spelling.
    return ('"POST", "/api/segments/lint"' in stripped
            and "runBacktest(definition);" in stripped
            and "runLint(definition);" in stripped)


def test_the_recorder_lints_alongside_its_backtest():
    assert _lints_alongside_the_recorder_backtest(SEGMENT_TIMELINE_JS_SOURCE)
    # A create excludes nothing; a RE-RECORD (round 16) excludes the row it
    # replaces, or an unchanged walk reports itself as its own duplicate.
    assert ("segment_id: replaces ? replaces.id : null"
            in strip_comments(SEGMENT_TIMELINE_JS_SOURCE))


def test_the_recorder_lint_guard_can_still_fail():
    comment_only = ('// runLint(definition); posts "/api/segments/lint"\n'
                    '// right beside runBacktest(definition); in recheck.\n')
    assert not _lints_alongside_the_recorder_backtest(comment_only)
    real_code = ('runBacktest(definition);\nrunLint(definition);\n'
                 'send("POST", "/api/segments/lint", body)')
    assert _lints_alongside_the_recorder_backtest(real_code)


def _recorder_save_waits_for_lint(source: str) -> bool:
    return ("disabled=${!btReport || saving || lintHasError}"
            in strip_comments(source))


def test_the_recorder_save_is_disabled_on_a_lint_error():
    assert _recorder_save_waits_for_lint(SEGMENT_TIMELINE_JS_SOURCE)


def test_the_recorder_save_disable_guard_can_still_fail():
    comment_only = ('// Save is disabled=${!btReport || saving || lintHasError}\n'
                    '// once the review step lands.\n')
    assert not _recorder_save_waits_for_lint(comment_only)
    assert _recorder_save_waits_for_lint(
        'disabled=${!btReport || saving || lintHasError} onclick=${save}')


# --- round 18: the pin wears his name, and Save says it saved ---------------

def _pin_reads_the_landmark_name(source: str) -> bool:
    """Item 1: the editor's pinned-moment control renders the CATALOGUE name
    ("✕ CCM Wooden Door"), falling back to the generic wording only for a
    key he never named. Both halves scanned: the lookup and the render."""
    stripped = strip_comments(source)
    return ('(vocab.landmark_names || {})[value]' in stripped
            and '${pinName || "this specific one"}' in stripped)


def test_the_pinned_moment_wears_his_landmark_name():
    assert _pin_reads_the_landmark_name(SEGMENTS_JS_SOURCE)
    # The names have to actually ARRIVE: the page fetches the same store the
    # recorder's rows read and threads it in on vocab.
    assert '"/api/landmarks"' in SEGMENTS_JS_SOURCE
    assert "landmark_names: landmarkNames" in SEGMENTS_JS_SOURCE


def test_the_pin_name_guard_can_still_fail():
    comment_only = ('// (vocab.landmark_names || {})[value] feeds the pin\n'
                    '// ${pinName || "this specific one"} renders it\n')
    assert not _pin_reads_the_landmark_name(comment_only)
    real = ('const pinName = (vocab.landmark_names || {})[value];\n'
            'x = `${pinName || "this specific one"}`;\n')
    assert _pin_reads_the_landmark_name(real)


def _save_flashes_saved(source: str) -> bool:
    """Item 2: ✓ + "Saved" for ~2 s, set only AFTER the write resolves —
    the flash call sits after onSaved in the source, which only runs once
    the PUT/POST returned."""
    stripped = strip_comments(source)
    save_site = stripped.find("setSavedFlash(true)")
    return (save_site != -1
            and stripped.rfind("onSaved(savedId)", 0, save_site) != -1
            and '${savedFlash ? "Saved" : "Save segment"}' in stripped
            and 'name=${savedFlash ? "check" : "save"}' in stripped)


def test_the_save_button_confirms_an_actual_save():
    assert _save_flashes_saved(SEGMENTS_JS_SOURCE)


def test_the_save_flash_guard_can_still_fail():
    comment_only = ('// setSavedFlash(true) after onSaved(savedId)\n'
                    '// ${savedFlash ? "Saved" : "Save segment"}\n')
    assert not _save_flashes_saved(comment_only)
    real = ('onSaved(savedId);\nsetSavedFlash(true);\n'
            'label = `${savedFlash ? "Saved" : "Save segment"}`;\n'
            'icon = `name=${savedFlash ? "check" : "save"}`;\n')
    assert _save_flashes_saved(real)
