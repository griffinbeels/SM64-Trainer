"""The Rank tab's scorecard card (spec 2026-08-23-scorecard-design, task 3).

Two layers, same split every rendered-card feature in this app uses:
`ui/scorecardgoal.js` is pure and import-free (division options + gap
formatting), driven through node exactly like ui/entitysection.js
(tests/test_ui_entity_section.py); `ui/components/scorecard.js` is the
Preact card, driven through a real browser via tools/ui_fixture.py.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "src" / "sm64_events" / "ui"
SCORECARDGOAL_JS = (UI / "scorecardgoal.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def call(fn: str, *args: object) -> object:
    """One exported function, one call, JSON in and JSON out -- the exact
    pattern test_ui_entity_section.py drives entitysection.js with."""
    script = (f"import * as mod from {SCORECARDGOAL_JS!r};\n"
              f"console.log(JSON.stringify(mod.{fn}("
              + ",".join(json.dumps(a) for a in args) + ")));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_division_options_has_forty_entries_hardest_first_no_capless():
    options = call("divisionOptions")
    assert len(options) == 40
    assert options[0] == {"value": "division:Mario:I", "label": "Mario 1"}
    assert not any("Capless" in o["label"] for o in options)
    assert not any(o["value"].startswith("division:Iron:") for o in options)


def test_division_options_covers_every_non_iron_tier_five_divisions_each():
    options = call("divisionOptions")
    by_tier = {}
    for option in options:
        _, tier, division = option["value"].split(":")
        by_tier.setdefault(tier, []).append(division)
    assert set(by_tier.keys()) == {
        "Mario", "Grandmaster", "Master", "Diamond",
        "Platinum", "Gold", "Silver", "Bronze"}
    for tier, divisions in by_tier.items():
        assert sorted(divisions) == sorted(["I", "II", "III", "IV", "V"]), tier


def test_fmt_gap_cs_prints_a_signed_two_decimal_second_value():
    assert call("fmtGapCs", -437) == "-4.37"
    assert call("fmtGapCs", 40) == "+0.40"


def test_fmt_gap_cs_signs_a_positive_gap_too():
    assert call("fmtGapCs", 100) == "+1.00"


def test_goal_groups_is_no_goal_then_divisions_then_runners_with_no_runners_fetched_yet():
    """Before the picker's first open, `runners` is null -- the Runners
    group renders (so SearchMenu can find it) but carries no options."""
    groups = call("goalGroups", None)
    assert [group["label"] for group in groups] == ["", "Divisions", "Runners"]
    assert groups[0]["options"] == [{"value": "", "label": "No goal"}]
    assert len(groups[1]["options"]) == 40                    # divisionOptions()
    assert groups[2]["options"] == []


def test_goal_groups_encodes_a_fetched_runner_as_runner_colon_name():
    groups = call("goalGroups", ["808sAndBailey", "Suigi"])
    runners = groups[2]["options"]
    assert runners == [{"value": "runner:808sAndBailey", "label": "808sAndBailey"},
                       {"value": "runner:Suigi", "label": "Suigi"}]


def test_goal_groups_omits_the_custom_group_entirely_with_no_saved_names():
    """No empty 'Custom' heading ever shown -- unlike Runners (which stays
    present so SearchMenu has a drop target for the lazy fetch), a saved
    custom goal either exists or the group has nothing to add."""
    groups = call("goalGroups", None, [])
    assert [group["label"] for group in groups] == ["", "Divisions", "Runners"]


def test_goal_groups_puts_custom_names_at_the_top_after_no_goal():
    groups = call("goalGroups", None, ["Sub 90 Attempt", "PSS Skip Route"])
    assert [group["label"] for group in groups] == ["", "Custom", "Divisions", "Runners"]
    assert groups[1]["options"] == [
        {"value": "custom:Sub 90 Attempt", "label": "Sub 90 Attempt"},
        {"value": "custom:PSS Skip Route", "label": "PSS Skip Route"}]


def test_parse_gap_time_reads_the_displayed_notation_back():
    assert call("parseGapTime", "1'21\"32") == 8132
    assert call("parseGapTime", "23\"00") == 2300           # no minutes, matches fmtSeconds
    assert call("parseGapTime", "0'05\"5") == 550            # single-digit centis = tenths


def test_parse_gap_time_rejects_garbage_and_empty():
    assert call("parseGapTime", "") is None
    assert call("parseGapTime", "not a time") is None
    assert call("parseGapTime", "1:21.32") is None            # wrong punctuation


def test_apply_goal_overrides_recomputes_the_touched_tile_and_its_row_sum():
    payload = {
        "rows": [{
            "course_id": 1, "label": "Bob-omb Battlefield",
            "tiles": [
                {"key": "star:1:0", "label": "A", "you_cs": 900, "goal_cs": 1000,
                 "delta_cs": -100, "folded": False},
                {"key": "star:1:1", "label": "B", "you_cs": 1200, "goal_cs": None,
                 "delta_cs": None, "folded": False},
            ],
            "sum": {"you_cs": 900, "goal_cs": 1000, "delta_cs": -100,
                    "counted": 1, "total": 2},
        }],
        "total": {"you_cs": 900, "goal_cs": 1000, "delta_cs": -100,
                  "counted": 1, "total": 2},
        "goal_coverage": {"covered": 1, "tiles": 2},
    }
    result = call("applyGoalOverrides", payload, {"star:1:1": 1100})
    tile = result["rows"][0]["tiles"][1]
    assert tile["goal_cs"] == 1100
    assert tile["delta_cs"] == 100                            # 1200 - 1100
    assert result["rows"][0]["sum"] == {
        "you_cs": 2100, "goal_cs": 2100, "delta_cs": 0, "counted": 2, "total": 2}
    assert result["total"] == result["rows"][0]["sum"]
    assert result["goal_coverage"] == {"covered": 2, "tiles": 2}


def test_apply_goal_overrides_is_a_no_op_with_nothing_pending():
    payload = {"rows": [], "total": {"you_cs": 0, "goal_cs": 0, "delta_cs": None,
                                     "counted": 0, "total": 0},
               "goal_coverage": {"covered": 0, "tiles": 0}}
    assert call("applyGoalOverrides", payload, {}) == payload


def test_apply_goal_overrides_skips_a_folded_tile_in_the_sum():
    payload = {
        "rows": [{
            "course_id": 1, "label": "X",
            "tiles": [{"key": "star:1:6", "label": "100c", "you_cs": 5000,
                       "goal_cs": None, "delta_cs": None, "folded": True}],
            "sum": {"you_cs": 0, "goal_cs": 0, "delta_cs": None, "counted": 0, "total": 1},
        }],
        "total": {"you_cs": 0, "goal_cs": 0, "delta_cs": None, "counted": 0, "total": 1},
        "goal_coverage": {"covered": 0, "tiles": 1},
    }
    result = call("applyGoalOverrides", payload, {"star:1:6": 4800})
    tile = result["rows"][0]["tiles"][0]
    assert tile["goal_cs"] == 4800 and tile["delta_cs"] == 200
    # Still excluded from the Sigma -- folded means "draws with real numbers,
    # never joins the sum", override or not.
    assert result["rows"][0]["sum"]["counted"] == 0
    assert result["rows"][0]["sum"]["delta_cs"] is None


# --- the rendered card -------------------------------------------------

sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui  # noqa: E402
from uilab.driver import get_driver  # noqa: E402
import urllib.request  # noqa: E402

_OPEN_RANK_TAB = (
    "document.querySelector('button.nav-item[title=\"Rank\"]').click()")


def _put_division_goal(base: str, tier: str, division: str) -> None:
    body = json.dumps({"kind": "division", "tier": tier, "division": division}).encode()
    request = urllib.request.Request(
        f"{base}/api/scorecard/goal", data=body, method="PUT",
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=10).read()


def _get_scorecard(base: str) -> dict:
    with urllib.request.urlopen(f"{base}/api/scorecard", timeout=10) as response:
        return json.loads(response.read())


def test_the_card_renders_tiles_colored_against_a_real_goal():
    with serve_ui() as base:
        # A division comfortably inside the seeded fixture's own PBs, so at
        # least one tile actually grades -- the card must draw REAL good/bad
        # tiles, not just an all-dim grid a too-hard goal would also satisfy.
        _put_division_goal(base, "Bronze", "V")
        payload = _get_scorecard(base)
        assert payload["goal"] == {"kind": "division", "tier": "Bronze", "division": "V"}

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card")
            page.wait_for(".rank-page .score-tile")
            page.wait_ms(200)

            colored = page.count(
                ".rank-page .scorecard-card .score-tile.good, "
                ".rank-page .scorecard-card .score-tile.bad")
            assert colored >= 1, "no colored tile against a real division goal"

            sum_text = page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".score-foot .score-sum-value').textContent.trim()")

        total = payload["total"]
        if total["counted"] > 0:
            expected = _fmt_seconds_like_js(abs(total["delta_cs"]) / 100)
            assert sum_text == expected, (sum_text, total)
        else:
            assert sum_text == "—"


def test_the_card_reaches_a_real_goal_covers_line_when_partial():
    with serve_ui() as base:
        # The hardest division: almost nothing the fixture seeded will grade
        # it, so coverage is genuinely partial and the note has content to
        # report against the payload's own numbers.
        _put_division_goal(base, "Mario", "I")
        payload = _get_scorecard(base)
        coverage = payload["goal_coverage"]
        assert coverage["covered"] < coverage["tiles"], (
            "fixture graded every tile at Mario I -- pick a harder goal or "
            "seed less, or this test proves nothing about the partial case")

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .scorecard-note")
            note = page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".scorecard-note').textContent.trim()")
        assert note == f"goal covers {coverage['covered']}/{coverage['tiles']}"


def _real_runner_reaching(seeded_keys: set[str]) -> str:
    """A real Ultimate Sheet runner off the bundled snapshot with a sheet
    time on at least one of the fixture's own seeded (you_cs) entities --
    the exact overlap `tileView` needs to draw a colored tile rather than
    dim -- restricted to an alphanumeric name so the CSS attribute selector
    driving the picker below needs no escaping."""
    import re

    from sm64_events.core.paths import bundled_sheet_library
    from sm64_events.library.ratings import runner_times
    from sm64_events.library.store import LibraryStore

    store = LibraryStore(bundled_path=bundled_sheet_library())
    store.load()
    times = runner_times(store.payload, {}, version="us")
    return next(name for name, by_entity in times.items()
               if re.fullmatch(r"[A-Za-z0-9_]+", name)
               and seeded_keys & by_entity.keys())


def test_the_picker_gains_a_runners_group_and_picking_one_colors_tiles():
    """The Runners group is empty until the picker's first open (the lazy
    fetch this task adds) and carries real names once it has opened; picking
    one grades the card exactly like a division goal does -- real colored
    tiles and a real coverage note, not just a value round-trip."""
    with serve_ui() as base:
        # The entities the fixture itself has a PB on -- a runner goal must
        # land on one of THESE exact keys to draw anything but dim, the same
        # way a too-hard division goal (test above) would.
        seeded_keys = {tile["key"] for row in _get_scorecard(base)["rows"]
                      for tile in row["tiles"] if tile["you_cs"] is not None}
        assert seeded_keys, "the fixture seeded no PBs -- nothing to grade against"
        runner = _real_runner_reaching(seeded_keys)

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .search-select-trigger")

            # Before the open: no Runners options have landed. (Divisions
            # are present immediately -- they cost no fetch.)
            pre_open = page.evaluate(
                "document.querySelectorAll('.rank-page .scorecard-card "
                ".search-menu-option').length")
            assert pre_open == 0, "the menu must not be open yet"

            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".search-select-trigger').click()")
            # Lazy fetch: wait for THIS runner's option to actually land
            # rather than assuming a fixed delay covers the round trip.
            page.wait_for(
                ".rank-page .scorecard-card "
                f'.search-menu-option[data-value="runner:{runner}"]')

            group_heads = page.evaluate(
                "Array.from(document.querySelectorAll('.rank-page "
                ".scorecard-card .search-menu-group-head'))"
                ".map((el) => el.textContent)")
            assert "Runners" in group_heads

            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                f'.search-menu-option[data-value="runner:{runner}"]\').click()')
            page.wait_for(".rank-page .scorecard-card .score-tile.good, "
                          ".rank-page .scorecard-card .score-tile.bad")

        card = _get_scorecard(base)
        assert card["goal"] == {"kind": "runner", "runner": runner}
        coverage = card["goal_coverage"]
        assert 0 < coverage["covered"] < coverage["tiles"]


# --- the goal picker stays inside the card (feedback round 8) -----------

def test_the_goal_picker_opens_without_widening_the_page():
    """His report, verbatim: 'when I press the dropdown, it goes
    offscreen... it shouldn't mess with the width of the page at all.'

    `document.documentElement.scrollWidth` is the WRONG instrument for this:
    `.app-main` clips horizontally (`overflow-x: hidden`, index.html), so an
    overflowing popup is invisibly cut off rather than growing the page's
    own scrollable width -- measured directly, scrollWidth read the exact
    same value before and after opening the picker whether or not the
    `align=\"right\"` fix was even in the tree, which is a vacuous guard
    wearing a real-looking assertion. The actual claim is geometric: does
    the menu's own right edge stay inside `.app-main`'s clip boundary. The
    trigger sits at the far right of a wide card (`.scorecard-head`'s own
    `margin-right: auto`) -- exactly the shape that ran the panel past that
    boundary before the fix (measured: 11px over at 1500px width)."""
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .search-select-trigger")

            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".search-select-trigger').click()")
            page.wait_for(".rank-page .scorecard-card .search-menu")
            page.wait_ms(100)
            geometry = page.evaluate(
                "(() => {"
                "  const appMain = document.querySelector('.app-main').getBoundingClientRect();"
                "  const menu = document.querySelector('.rank-page .scorecard-card "
                ".search-menu').getBoundingClientRect();"
                "  return {appMainRight: appMain.right, menuRight: menu.right};"
                "})()")

        assert geometry["menuRight"] <= geometry["appMainRight"] + 1, (
            f"the goal picker's right edge ({geometry['menuRight']}) runs past "
            f".app-main's own clip boundary ({geometry['appMainRight']}) -- "
            "it will be cut off by overflow-x: hidden, not merely off the "
            "viewport")


# --- expand a row, edit a goal, save a named custom goal (round 8) ------

def _first_seeded_tile(payload: dict) -> tuple[str, str, str]:
    """(row label, tile label, entity key) of the first tile the FIXTURE
    itself has a PB on -- so an edited goal has a real `you_cs` to compare
    against and can actually recolor, not just round-trip a number."""
    for row in payload["rows"]:
        for tile in row["tiles"]:
            if tile["you_cs"] is not None:
                return row["label"], tile["label"], tile["key"]
    raise AssertionError("the fixture seeded no PBs at all -- nothing to edit against")


def _open_goal_editor(page, row_label: str, tile_label: str) -> None:
    """Expand ROW_LABEL's row and click TILE_LABEL's Goal cell into edit
    mode -- the shared first half of every scenario below, entirely through
    the real controls, never by writing state directly."""
    row_label_js = json.dumps(row_label)
    tile_label_js = json.dumps(tile_label)
    page.evaluate(
        "(() => {"
        f"  const rowLabel = {row_label_js};"
        "  const row = Array.from(document.querySelectorAll("
        "    '.rank-page .scorecard-card .score-row'))"
        "    .find((r) => r.querySelector('.score-row-name').textContent === rowLabel);"
        "  row.querySelector('.score-row-label').click();"
        "})()")
    page.wait_for(".rank-page .scorecard-card .score-detail-table")
    page.evaluate(
        "(() => {"
        f"  const rowLabel = {row_label_js}, starLabel = {tile_label_js};"
        "  const row = Array.from(document.querySelectorAll("
        "    '.rank-page .scorecard-card .score-row'))"
        "    .find((r) => r.querySelector('.score-row-name').textContent === rowLabel);"
        "  const tr = Array.from(row.querySelectorAll('.score-detail-table tbody tr'))"
        "    .find((tr) => tr.children[0].textContent.trim().startsWith(starLabel));"
        "  tr.querySelector('.score-detail-goal-btn').click();"
        "})()")
    page.wait_for(".rank-page .scorecard-card .score-detail-input")


def _type_into_goal_editor(page, typed: str) -> None:
    """Types into the already-open goal input, in its OWN `evaluate()` call
    -- see the trap noted below."""
    typed_js = json.dumps(typed)
    page.evaluate(
        "(() => {"
        "  const input = document.querySelector('.rank-page .scorecard-card "
        ".score-detail-input');"
        "  const setter = Object.getOwnPropertyDescriptor("
        "    window.HTMLInputElement.prototype, 'value').set;"
        f"  setter.call(input, {typed_js});"
        "  input.dispatchEvent(new Event('input', {bubbles: true}));"
        "})()")
    page.wait_ms(120)


def _blur_goal_editor(page) -> None:
    """Preact attaches `onblur` directly on the input node, so dispatching
    the event straight at it fires the handler without needing the native
    `blur` event's (non-bubbling) propagation."""
    page.evaluate(
        "document.querySelector('.rank-page .scorecard-card "
        ".score-detail-input').dispatchEvent(new Event('blur', {bubbles: true}))")


def _expand_row_and_edit_goal(page, row_label: str, tile_label: str, typed: str) -> None:
    """Click the named row open, click that star's Goal cell into edit mode,
    type `typed`, and commit with Enter -- entirely through the real
    controls, never by writing state directly."""
    _open_goal_editor(page, row_label, tile_label)
    # The input and the Enter commit are dispatched in SEPARATE evaluate()
    # calls, with a tick between: Preact's `commit` closure captures `draft`
    # from the render current at COMMIT time, and firing both events in one
    # synchronous script hands the keydown handler the PRE-input closure
    # (`.claude/rules/ui-core.md`'s own documented trap -- reading/dispatching
    # in the same tick sees the pre-render state).
    _type_into_goal_editor(page, typed)
    page.evaluate(
        "document.querySelector('.rank-page .scorecard-card "
        ".score-detail-input').dispatchEvent("
        "  new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}))")


def test_expanding_a_row_shows_every_star_goal_you_and_delta():
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-row-label")

            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".score-row-label').click()")
            page.wait_for(".rank-page .scorecard-card .score-detail-table")

            headers = page.evaluate(
                "Array.from(document.querySelectorAll('.rank-page .scorecard-card "
                ".score-detail-table th')).map((el) => el.textContent)")
            row_count = page.evaluate(
                "document.querySelectorAll('.rank-page .scorecard-card "
                ".score-detail-table tbody tr').length")
        assert headers == ["Star", "Goal", "You", "Δ"]
        assert row_count == 7      # a course row: stars 0-5 + the 100c star


def test_blurring_an_empty_goal_draft_cancels_edit_mode():
    """His acceptance rule for a multi-step control: an EMPTY draft is 'I
    changed my mind', not 'I typed garbage' -- blurring away from it must
    close the editor cleanly, no red state and no input stuck open."""
    with serve_ui() as base:
        row_label, tile_label, _tile_key = _first_seeded_tile(_get_scorecard(base))

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-row-label")

            # No goal is picked in this test, so the tile has no goal_cs and
            # the editor opens with an already-empty draft -- exactly the
            # case under test.
            _open_goal_editor(page, row_label, tile_label)
            _blur_goal_editor(page)
            page.wait_ms(150)

            closed = page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".score-detail-input') === null")

        assert closed, "an empty-draft blur must close the editor, not leave it stuck open"


def test_blurring_an_unparseable_goal_draft_shows_the_hint():
    """A NON-empty draft that cannot parse stays open with the red invalid
    state -- but the way out is now printed right under the box that
    rejected it (`.claude/rules/acceptance.md`'s "put the reason where the
    click lands" rule)."""
    with serve_ui() as base:
        row_label, tile_label, _tile_key = _first_seeded_tile(_get_scorecard(base))

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-row-label")

            _open_goal_editor(page, row_label, tile_label)
            _type_into_goal_editor(page, "not a time")
            _blur_goal_editor(page)
            page.wait_ms(150)

            invalid = page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".score-detail-input.is-invalid') !== null")
            hint = page.evaluate(
                "(() => {"
                "  const el = document.querySelector('.rank-page .scorecard-card "
                ".score-detail-hint');"
                "  return el ? el.textContent : null;"
                "})()")

        assert invalid, "an unparseable draft must keep the red invalid state on blur"
        assert hint == 'type it like 51"83'


def test_editing_a_goal_time_recomputes_the_tile_and_row_sum_before_saving():
    """His rule: 'This should automatically adjust my goal time comparison +
    my stage rta comparison' -- BEFORE any save, purely client-side."""
    with serve_ui() as base:
        row_label, tile_label, tile_key = _first_seeded_tile(_get_scorecard(base))

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-row-label")

            # A generous, easily-beaten goal (5 minutes) so the edited tile
            # grades GOOD regardless of which real PB the fixture happened
            # to seed -- the content under test is "did it recolor at all",
            # not which colour a specific PB earns.
            _expand_row_and_edit_goal(page, row_label, tile_label, "5'00\"00")
            page.wait_for(".rank-page .scorecard-card .scorecard-savebar")

            savebar_text = page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".scorecard-savebar .meta').textContent")
            colored = page.count(
                ".rank-page .scorecard-card .score-tile.good, "
                ".rank-page .scorecard-card .score-tile.bad")
            # The API was never called -- this is still an unsaved edit.
            unsaved_goal = _get_scorecard(base)["goal"]

        assert "1" in savebar_text
        assert colored >= 1, "editing a goal must recolor its tile immediately, unsaved"
        assert unsaved_goal is None, "an unsaved edit must not have reached the server"


def test_saving_a_custom_goal_persists_it_and_lists_it_first_in_the_picker():
    with serve_ui() as base:
        row_label, tile_label, _tile_key = _first_seeded_tile(_get_scorecard(base))

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-row-label")

            _expand_row_and_edit_goal(page, row_label, tile_label, "5'00\"00")
            page.wait_for(".rank-page .scorecard-card .scorecard-savebar")

            page.evaluate(
                "(() => {"
                "  const input = document.querySelector('.rank-page .scorecard-card "
                ".scorecard-savebar-input');"
                "  const setter = Object.getOwnPropertyDescriptor("
                "    window.HTMLInputElement.prototype, 'value').set;"
                "  setter.call(input, 'My Sub 5 Attempt');"
                "  input.dispatchEvent(new Event('input', {bubbles: true}));"
                "})()")
            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".scorecard-savebar button:not([disabled])').click()")
            saved = page.evaluate(
                "(async () => {"
                "  const start = Date.now();"
                "  while (Date.now() - start < 5000) {"
                "    if (!document.querySelector('.rank-page .scorecard-card "
                ".scorecard-savebar')) return true;"
                "    await new Promise((resolve) => setTimeout(resolve, 40));"
                "  }"
                "  return false;"
                "})()")
            assert saved, "the save bar never cleared -- the save did not complete"

            trigger_label = page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".search-select-value').textContent")

            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".search-select-trigger').click()")
            page.wait_for(".rank-page .scorecard-card .search-menu-group-head")
            group_heads = page.evaluate(
                "Array.from(document.querySelectorAll('.rank-page .scorecard-card "
                ".search-menu-group-head')).map((el) => el.textContent)")

        assert trigger_label == "My Sub 5 Attempt"
        # Custom sits right after "No goal" -- ahead of Divisions/Runners.
        assert group_heads[0] == "Custom"

        card = _get_scorecard(base)
        assert card["goal"] == {"kind": "custom", "name": "My Sub 5 Attempt"}
        assert card["custom_goals"] == ["My Sub 5 Attempt"]


# --- the two Copy buttons (Task 5) --------------------------------------

def _stub_workbook():
    """A tiny two-target sheet, same shape as `test_scorecard_api.py`'s own
    `_bob_workbook` -- kept local so this file's fixture setup does not
    reach into a sibling test module's private helper."""
    from library_fixture import GREY, build_workbook
    from sm64_events.library import workbook as wb

    cells = {
        (1, 1): {"text": "Xcam IGT !"}, (1, 2): {"text": "Sheet Best"},
        (1, 3): {"text": "Player"}, (1, 4): {"text": "Ideal Run"},
        (1, 5): {"text": "Fill Rate"},
        (2, 1): {"text": "1. Bob-omb Battlefield"},
        (3, 1): {"text": "[1] Big Bob-omb on the Summit", "bold": True},
        (3, 2): {"text": "43.63"},
        (4, 1): {"text": "[1|2] Warp fadeout", "rgb": GREY},
        (4, 2): {"text": "15.90"},
    }
    return build_workbook({wb.SHEET_MAIN: cells,
                           wb.SHEET_LOG: {(1, 1): {"text": "46238.5"}}})


# Replaces ONLY `writeText`, per the brief -- `navigator.clipboard` itself is
# real (127.0.0.1 is a secure context in Chromium), so this leaves every
# other clipboard method alone and proves the shim actually installed by
# returning `true` rather than letting a missing `navigator.clipboard`
# fail silently.
_INSTALL_CLIPBOARD_SHIM = """(() => {
  window.__scorecardCopied = [];
  navigator.clipboard.writeText = (text) => {
    window.__scorecardCopied.push(text);
    return Promise.resolve();
  };
  return true;
})()"""


def test_copy_sheet_column_writes_every_line_to_the_clipboard(monkeypatch):
    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", _stub_workbook)
    with serve_ui() as base:
        column = json.loads(urllib.request.urlopen(
            f"{base}/api/scorecard/column", timeout=10).read())

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            # The head (and its buttons) only renders once `GET
            # /api/scorecard` resolves -- `.scorecard-card` alone mounts
            # immediately with just a loading state, so waiting on it is not
            # enough and races the card's own data fetch.
            page.wait_for(".rank-page .scorecard-copy-column")

            assert page.evaluate(_INSTALL_CLIPBOARD_SHIM) is True
            page.evaluate(
                "document.querySelector('.scorecard-copy-column').click()")
            page.wait_ms(400)
            # A SEPARATE evaluate call, after the wait -- reading the
            # captured text in the same call as the click would race the
            # button's own async fetch-then-copy chain.
            copied = page.evaluate("window.__scorecardCopied")

        assert len(copied) == 1
        assert len(copied[0].split("\n")) == column["total_rows"]


def test_copy_sheet_column_shows_the_doors_own_sentence_inline_on_a_503(monkeypatch):
    """Mirrors `test_scorecard_api.py`'s own 503 wording check -- the
    button must show the SAME sentence, inline, never a toast."""
    def boom(*_args, **_kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", boom)
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-copy-column")

            page.evaluate(
                "document.querySelector('.scorecard-copy-column').click()")
            page.wait_for(".rank-page .scorecard-exports .inline-state.error")
            message = page.evaluate(
                "document.querySelector('.rank-page .scorecard-exports "
                ".inline-state.error').textContent")

    assert "could not read the sheet" in message


def test_copy_scorecard_csv_writes_the_real_csv_text_to_the_clipboard():
    with serve_ui() as base:
        expected = urllib.request.urlopen(
            f"{base}/api/scorecard/export.csv", timeout=10).read().decode("utf-8")

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-copy-csv")

            assert page.evaluate(_INSTALL_CLIPBOARD_SHIM) is True
            page.evaluate(
                "document.querySelector('.scorecard-copy-csv').click()")
            page.wait_ms(400)
            copied = page.evaluate("window.__scorecardCopied")

        assert copied == [expected]


def _fmt_seconds_like_js(seconds: float) -> str:
    """The Python mirror of format.js::fmtSeconds -- used only to compute the
    EXPECTED text for the render assertion above, never as a second
    implementation the app itself could drift from (the app's own value
    comes straight off the DOM)."""
    centis_total = round(seconds * 100)
    minutes = centis_total // 6000
    secs = (centis_total % 6000) // 100
    centis = centis_total % 100
    body = f"{minutes}'{secs:02d}\"{centis:02d}"
    return body[2:] if body.startswith("0'") else body
