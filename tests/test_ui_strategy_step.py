"""Source contracts for the picker's third layer: which strategy to
practice a just-picked star or segment WITH (Task 6,
2026-07-25-target-picker-strategy-step).

The real proof of behavior — the fetch actually landing, exactly one write
firing, focus actually landing on Back — is a live render (a later task).
These pin the pieces a refactor could silently drop: the string->number POST
boundary (test_header_ui.py owns the same boundary for the two-step form;
Task 7 relocates that copy here), the allow_blank gate on the "No strategy"
card, and the .needs-strat blink when nothing is picked yet.

Assertions run against STEP_CODE (comments stripped), never the raw file —
see tests/source_scan.py: a header comment naming a rule by example must
never trip a guard meant to catch the rule's absence.
"""
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"
STEP = (UI / "components" / "strategystep.js").read_text(encoding="utf-8")
STEP_CODE = strip_comments(STEP)
INDEX = (UI / "index.html").read_text(encoding="utf-8")


def test_parses_ids_through_the_shared_helpers_not_a_second_copy():
    assert "parseSegmentId" in STEP_CODE
    assert "parseStarId" in STEP_CODE
    assert 'from "../entities.js"' in STEP_CODE


def test_posts_course_and_star_as_numbers():
    # Ids off the picker are STRINGS; the endpoint needs integers -- the same
    # boundary test_header_ui.py::test_target_modal_still_posts_course_and_star_as_numbers
    # guards for the two-step form (Task 7 relocates that copy here).
    assert "course_id: Number(parsedStar.course)" in STEP_CODE
    assert "star_id: Number(parsedStar.star)" in STEP_CODE


def test_posts_segment_id_as_a_number_too():
    assert "segment_id: Number(segmentId)" in STEP_CODE


def test_strat_tag_is_always_sent_never_omitted():
    # api.py:594 tells "clear the strategy" from "leave it alone" by whether
    # the key is PRESENT at all (model_fields_set) -- omitting it here would
    # silently mean the latter for every card, including "No strategy".
    assert STEP_CODE.count("strat_tag: stratTag") == 2


def test_the_blank_card_is_gated_on_allow_blank():
    assert "allow_blank ? html" in STEP_CODE


def test_needs_strat_blinks_only_when_nothing_is_current():
    assert 'current == null ? "needs-strat" : ""' in STEP_CODE


def test_writes_exactly_once_then_closes():
    assert STEP_CODE.count("requestTarget(t, body)") == 1
    assert STEP_CODE.count("onClose()") == 1
    # onClose is GATED on the write landing -- requestTarget answers false for
    # a dropped write and for the server refusing the pick (2026-07-27: you
    # may only practice what you are standing in front of).
    assert "if (await requestTarget(t, body)) onClose();" in STEP_CODE


def test_never_writes_through_the_active_strat_endpoint():
    # /api/strat sets the active strategy WITHOUT touching the target; this
    # step is reached by picking a target, so both must move together
    # through the SAME POST /api/target call (api.py:582).
    assert '"/api/strat"' not in STEP_CODE


def test_a_refused_or_dropped_write_stays_open_and_re_enables_the_cards():
    assert "else setSaving(false);" in STEP_CODE
    # No local alert: the message belongs to the ONE door every target write
    # goes through (ui/target.js), which raises the server's own sentence --
    # "you can only practice what you are standing in" names the fix, an
    # alert() only names the failure and steals the keyboard doing it.
    assert "window.alert" not in STEP_CODE
    assert 'from "../target.js"' in STEP_CODE


def test_uses_rank_icon_not_a_direct_hat_import():
    # Originally pinned Hat against a since-deleted Medal component; the
    # mario-cap-rank-icons integration (2026-07-26) gave the app a real,
    # user-selectable Medal STYLE, dispatched through RankIcon -- a direct
    # `Hat` import here would silently ignore that setting and always draw
    # caps while every other rank-icon surface honours it (RankIcon has the
    # exact same tier/division/size prop surface Hat did, so this is a
    # mechanical import+tag swap, not a behavior change to this card).
    assert "RankIcon}" in STEP_CODE
    assert 'from "./hat.js"' not in STEP_CODE


def test_strategy_icon_is_the_detailed_32px_cap_with_its_division():
    # hat.js only draws the division numeral/wings at size >= 30 -- this is
    # the one call site in the plan that wants the detailed cap, since the
    # division is exactly what distinguishes the strategies being chosen
    # between.
    assert "size=${32}" in STEP_CODE
    assert "division=${strat.division}" in STEP_CODE


def test_rank_text_never_prints_the_raw_tier_key():
    # tests/test_ui_cap_names.py is the general sweep; this just confirms
    # the specific call site routes through capName()/divisionDigit()
    # rather than printing `rank`/`strat.rank` bare.
    assert "capName(strat.rank)" in STEP_CODE
    assert "divisionDigit(strat.division)" in STEP_CODE


def test_back_button_carries_a_focus_ref_like_the_grids_own():
    # Entering this step unmounts the clicked cell, so focus falls to
    # <body> and Modal's mount-only focus effect never re-runs across the
    # grid->step transition (Preact reuses the Modal instance). Back must
    # grab focus itself, mirroring entitymodal.js's focusOnDrillIn.
    assert 'class="entity-back"' in STEP_CODE
    assert "ref=${focusOnEntry}" in STEP_CODE


def test_new_strategy_card_opens_stratmodal_and_commits_on_save():
    assert "StratModal}" in STEP_CODE
    assert "existing=${" in STEP_CODE
    assert "onSaved=${(stratName) => { setShowNew(false); commit(stratName); }}" in STEP_CODE


def test_zero_strategies_gets_a_one_line_note_not_the_cast_art_empty_state():
    assert "strategies.length === 0" in STEP_CODE
    assert "stable-empty compact" in STEP_CODE
    assert "emptystate" not in STEP_CODE.lower()


def test_fetches_keyed_on_value_not_on_every_render():
    assert "getJSON" in STEP_CODE
    assert 'from "../api.js"' in STEP_CODE
    assert "}, [value]);" in STEP_CODE


def test_css_reuses_the_entity_grid_geometry_idiom():
    css = strip_comments(INDEX)
    assert ".strat-grid" in css
    assert ".strat-card" in css
    grid_start = css.index(".strat-grid")
    grid_rules = css[grid_start:grid_start + 400]
    assert "repeat(auto-fill, minmax(" in grid_rules


def _writes_once_then_closes_holds(source: str) -> bool:
    """Mirrors test_writes_exactly_once_then_closes' three assertions as one
    probe function, so a comment-only/broken-code fixture can exercise the
    guard without duplicating the real STEP_CODE checks above (M6, final
    review 2026-07-26 -- the original test only proved strip_comments strips
    a comment, a property of source_scan.py already tested there, and never
    fed a sample to the count/ordering assertions themselves)."""
    code = strip_comments(source)
    if code.count('send("POST", "/api/target"') != 1:
        return False
    if code.count("onClose()") != 1:
        return False
    if "catch (writeError)" not in code:
        return False
    return code.index("onClose()") < code.index("catch (writeError)")


def test_the_guards_can_still_fail():
    # Probed in both directions (tests/source_scan.py), same pattern as
    # test_the_grid_rank_guard_can_still_fail in test_ui_entitymodal.py.
    # Comment-only: a note naming these calls by example must not satisfy
    # the guard on its own.
    assert not _writes_once_then_closes_holds(
        '// send("POST", "/api/target", body) used to fire here\n'
        "// onClose() followed it\n"
        "// catch (writeError) handled a dropped write\n")
    # Real code, correctly shaped: exactly what STEP_CODE has today.
    assert _writes_once_then_closes_holds(
        'await send("POST", "/api/target", body);\n'
        "onClose();\n"
        "} catch (writeError) {\n"
        "  window.alert(String(writeError));\n"
        "}")
    # Real code, onClose AFTER the catch -- closing on a dropped write
    # instead of a successful one, the exact ordering bug this guards.
    assert not _writes_once_then_closes_holds(
        "try {\n"
        '  await send("POST", "/api/target", body);\n'
        "} catch (writeError) {\n"
        "  window.alert(String(writeError));\n"
        "}\n"
        "onClose();")
    # Real code, the write fires twice -- the double-write bug the count
    # guards against.
    assert not _writes_once_then_closes_holds(
        'await send("POST", "/api/target", body);\n'
        'await send("POST", "/api/target", body);\n'
        "onClose();\n"
        "catch (writeError) {}")

    # Probed in both directions (tests/source_scan.py): a comment mentioning
    # onClose()/window.alert by name must not satisfy the ordering checks
    # above on its own -- covered by the real file already having exactly
    # one "onClose()" occurrence (asserted above), which a comment-only
    # mention would double.
    assert strip_comments("// onClose() used to be called here\n").count(
        "onClose()") == 0
