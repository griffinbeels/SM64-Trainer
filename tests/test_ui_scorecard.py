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


def test_apply_goal_overrides_reaches_a_previously_uncovered_tile():
    """Round 6 deleted the folded-tile concept (the 100c cell is combined
    with its companion structurally, so nothing is ever excluded from a sum
    by flag any more): an override that gives a goal-less tile its first
    goal pulls it INTO the recomputed sums."""
    payload = {
        "rows": [{
            "course_id": 1, "label": "X",
            "tiles": [{"key": "star:1:6", "label": "100c", "you_cs": 5000,
                       "goal_cs": None, "delta_cs": None}],
            "sum": {"you_cs": 0, "goal_cs": 0, "delta_cs": None, "counted": 0, "total": 1},
        }],
        "total": {"you_cs": 0, "goal_cs": 0, "delta_cs": None, "counted": 0, "total": 1},
        "goal_coverage": {"covered": 0, "tiles": 1},
    }
    result = call("applyGoalOverrides", payload, {"star:1:6": 4800})
    tile = result["rows"][0]["tiles"][0]
    assert tile["goal_cs"] == 4800 and tile["delta_cs"] == 200
    assert result["rows"][0]["sum"]["counted"] == 1
    assert result["rows"][0]["sum"]["delta_cs"] == 200
    assert result["total"]["counted"] == 1
    assert result["goal_coverage"]["covered"] == 1


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


def _put_custom_goal(base: str, name: str, times: dict) -> None:
    body = json.dumps({"kind": "custom", "name": name, "times": times}).encode()
    request = urllib.request.Request(
        f"{base}/api/scorecard/goal", data=body, method="PUT",
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=10).read()


def _put_runner_goal(base: str, runner: str) -> None:
    body = json.dumps({"kind": "runner", "runner": runner}).encode()
    request = urllib.request.Request(
        f"{base}/api/scorecard/goal", data=body, method="PUT",
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=10).read()


def _any_sheet_runner() -> str:
    """A real runner off the bundled snapshot with SOME star times but not
    all of them -- derived rather than named, so a re-scrape can move the
    corpus without stranding this test on a runner who stopped playing."""
    from sm64_events.core.paths import bundled_sheet_library
    from sm64_events.library.ratings import runner_times
    from sm64_events.library.store import LibraryStore

    store = LibraryStore(bundled_path=bundled_sheet_library())
    store.load()
    times = runner_times(store.payload, {}, version="us")
    for name, by_entity in sorted(times.items()):
        stars = [key for key in by_entity if key.startswith("star:")]
        if 5 <= len(stars) <= 60:
            return name
    raise AssertionError("no partially-covering runner in the snapshot")


def _get_scorecard(base: str) -> dict:
    with urllib.request.urlopen(f"{base}/api/scorecard", timeout=10) as response:
        return json.loads(response.read())


def test_the_cards_render_colored_lines_against_a_real_goal():
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
            page.wait_for(".rank-page .score-line")
            page.wait_ms(200)

            colored = page.count(
                ".rank-page .scorecard-card .score-line .score-gap.good, "
                ".rank-page .scorecard-card .score-line .score-gap.bad")
            assert colored >= 1, "no colored line against a real division goal"

            # Round 9: each payload row is a CARD; its foot restates the
            # Σ as You · Goal · gap. Content, not existence: the first
            # card's foot must print the payload's own sums.
            first_row = payload["rows"][0]
            card_count = page.count(".rank-page .scorecard-card .score-card")
            line_count = page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".score-card').querySelectorAll('.score-line').length")
            you_text, goal_text = page.evaluate(
                "(() => {"
                "  const foot = document.querySelector('.rank-page "
                ".scorecard-card .score-card .score-card-foot');"
                "  return [foot.querySelector('.score-line-you')"
                ".textContent.trim(),"
                "          foot.querySelector('.score-line-goal')"
                ".textContent.trim()];"
                "})()")

        assert card_count == len(payload["rows"])
        assert line_count == len(first_row["tiles"])
        row_sum = first_row["sum"]
        if row_sum["counted"] > 0:
            assert you_text == _fmt_seconds_like_js(row_sum["you_cs"] / 100)
            assert goal_text == _fmt_seconds_like_js(row_sum["goal_cs"] / 100)
        else:
            assert you_text == "—" and goal_text == "—"


def test_the_card_reaches_a_real_goal_covers_line_when_partial():
    with serve_ui() as base:
        # A RUNNER goal, not a division: a division now covers every tile on
        # the card, because every star the community publishes standards for
        # carries a ladder (2026-08-31 gave the last exception, Slide Star
        # (Under 21 Seconds), its own entity). A runner is partial BY NATURE
        # -- they have no time wherever they never recorded one, which is
        # `runner_times`' own absent-never-zero rule -- so the note has real
        # content to report against the payload's own numbers.
        _put_runner_goal(base, _any_sheet_runner())
        payload = _get_scorecard(base)
        coverage = payload["goal_coverage"]
        assert 0 < coverage["covered"] < coverage["tiles"], (
            "this runner covers all or none of the card -- pick another, or "
            "this test proves nothing about the partial case")

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
            page.wait_for(
                ".rank-page .scorecard-card .score-gap.good, "
                ".rank-page .scorecard-card .score-gap.bad")

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
    """Click TILE_LABEL's Goal cell into edit mode, inside ROW_LABEL's card
    -- the shared first half of every scenario below, entirely through the
    real controls. Round 9: no expand step -- the goal button sits right on
    the card's line."""
    row_label_js = json.dumps(row_label)
    tile_label_js = json.dumps(tile_label)
    page.evaluate(
        "(() => {"
        f"  const rowLabel = {row_label_js}, starLabel = {tile_label_js};"
        "  const card = Array.from(document.querySelectorAll("
        "    '.rank-page .scorecard-card .score-card'))"
        "    .find((c) => c.querySelector('.score-card-name').textContent === rowLabel);"
        "  const line = Array.from(card.querySelectorAll('.score-line'))"
        "    .find((l) => l.querySelector('.score-line-name')"
        ".textContent.trim().startsWith(starLabel));"
        "  line.querySelector('.score-detail-goal-btn').click();"
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
    """Click the named star's Goal cell into edit mode, type `typed`, and
    commit with Enter -- entirely through the real controls. (The name
    predates round 9; nothing expands any more, the editor is on the
    line.)"""
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
            page.wait_for(".rank-page .scorecard-card .score-card")

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
            page.wait_for(".rank-page .scorecard-card .score-card")

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
            page.wait_for(".rank-page .scorecard-card .score-card")

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
                ".rank-page .scorecard-card .score-gap.good, "
                ".rank-page .scorecard-card .score-gap.bad")
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
            page.wait_for(".rank-page .scorecard-card .score-card")

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


def test_the_card_offers_only_the_sheet_column_button():
    """Round 19 (his call): the CSV button is gone. The export endpoint
    stays reachable by URL -- what left is the control, so the card has
    exactly one export door and it is the sheet column."""
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-copy-column")
            page.wait_ms(200)
            buttons = page.evaluate(
                "Array.from(document.querySelectorAll('.rank-page "
                ".scorecard-exports .scorecard-copy-btn'))"
                ".map((el) => el.textContent.trim())")
        assert buttons == ["Copy sheet column"], buttons


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


def test_a_typed_goal_snaps_onto_the_displayable_set_in_the_cell():
    """Round 5 (2026-08-24): "leverage the existing time entry validation
    system... it rounds to the nearest valid time (based on what's actually
    possible in the frame data)". 51"01 is not a time the 30fps timer can
    ever show; the existing door (format.js::attainableCs, the import
    field's own) rounds it up to 51"03, and the cell shows the snapped
    value the moment the edit commits -- never a number nobody typed."""
    with serve_ui() as base:
        row_label, tile_label, _tile_key = _first_seeded_tile(_get_scorecard(base))

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")

            _expand_row_and_edit_goal(page, row_label, tile_label, "51\"01")
            page.wait_ms(150)

            row_label_js = json.dumps(row_label)
            tile_label_js = json.dumps(tile_label)
            cell = page.evaluate(
                "(() => {"
                f"  const rowLabel = {row_label_js}, starLabel = {tile_label_js};"
                "  const card = Array.from(document.querySelectorAll("
                "    '.rank-page .scorecard-card .score-card'))"
                "    .find((c) => c.querySelector('.score-card-name').textContent === rowLabel);"
                "  const line = Array.from(card.querySelectorAll('.score-line'))"
                "    .find((l) => l.querySelector('.score-line-name')"
                ".textContent.trim().startsWith(starLabel));"
                "  return line.querySelector('.score-detail-goal-btn').textContent.trim();"
                "})()")

        assert cell == "51\"03", (
            f"a typed 51\"01 must land as the snapped 51\"03, got {cell!r}")


def _create_route(base: str, name: str, steps: list) -> int:
    """Create a route through the real endpoint, so a route SCOPE exists to
    select -- `tools/ui_fixture.py` seeds none (reconcile_defaults is
    main.py's boot step, not the service's)."""
    body = json.dumps({"name": name, "steps": steps}).encode()
    request = urllib.request.Request(
        f"{base}/api/routes", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())["id"]


def test_the_row_x_removes_that_row_and_ignores_it_in_ranking():
    """Round 8: "add a red X next to each of the rows in the scorecard. If
    the user presses that red X, then it removes it from the scorecard
    tracking."

    Driven through the real button, and asserted on BOTH surfaces the one
    exclusion door feeds: the row leaves the card, and `/api/marelo` for the
    same scope stops carrying that entity. Two ignore lists that could
    disagree is exactly what this makes impossible."""
    with serve_ui() as base:
        route_id = _create_route(base, "Removable", [
            {"need": 1, "candidates": [{"type": "star", "course": 1, "star": 0}]},
            {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}])

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.evaluate(
                "(() => {"
                "  const select = document.querySelector('.rank-page "
                ".route-focus-control select');"
                f"  select.value = 'route:{route_id}';"
                "  select.dispatchEvent(new Event('change', {bubbles: true}));"
                "})()")
            page.wait_ms(400)
            assert page.count(".rank-page .scorecard-card .score-card") == 2

            removed_label = page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".score-card .score-card-name').textContent.trim()")
            page.evaluate(
                "document.querySelectorAll('.rank-page .scorecard-card "
                ".score-row-remove')[0].click()")
            page.wait_ms(700)

            rows_left = page.count(".rank-page .scorecard-card .score-card")
            labels_left = page.evaluate(
                "Array.from(document.querySelectorAll('.rank-page "
                ".scorecard-card .score-card-name')).map((el) => "
                "el.textContent.trim())")

        assert rows_left == 1, f"the X left {rows_left} cards, expected 1"
        assert removed_label not in labels_left

        # The SAME exclusion the Rank tab writes -- so the scope's own
        # RATING dropped it too, not just this card's view of it. `/api/marelo`
        # keeps an excluded entity in `entities` as an inert row on purpose
        # (`_append_excluded_rows`, so the breakdown can offer it back), so
        # the honest check is the flag and the slot COUNT, not absence.
        with urllib.request.urlopen(
                f"{base}/api/marelo?scope=route%3A{route_id}", timeout=10) as response:
            marelo = json.loads(response.read())
        by_key = {entity["key"]: entity for entity in marelo["entities"]}
        assert by_key["star:1:0"]["excluded"] is True
        assert by_key["star:2:0"].get("excluded") is not True
        assert marelo["n"] == 1, (
            "the removed star must leave the rating's denominator, not just "
            f"wear a flag: n={marelo['n']}")


def test_the_caps_toggle_defaults_off_and_enabling_persists():
    """Round 10 flipped round 9's default — "No rank caps by default
    (disable by default)" — so a fresh browser sees the lean sheet and
    turning caps ON is what persists (same key). Persistence is proved by a
    RELOAD -- the preference must survive the page, not just the render."""
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(200)

            assert page.count(".rank-page .scorecard-card .score-line-cap") == 0, (
                "a fresh browser must see the lean sheet — no caps")

            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".scorecard-caps-toggle input').click()")
            page.wait_ms(200)
            caps_on = page.count(".rank-page .scorecard-card .score-line-cap")
            assert caps_on >= 1, "with a goal set and PBs seeded, the toggle must draw caps"

            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(200)
            assert page.count(".rank-page .scorecard-card .score-line-cap") >= 1, (
                "the enabled preference must survive a reload")


def test_the_cards_sit_in_aligned_columns_with_secret_before_fights():
    """Round 10's placement, his words as geometry: "3 columns of 5 cards
    (for each of the courses, in order), and then a 4th column on the far
    right for the remainder (secret stars card, followed by bowser fights
    card)... The tops and bottoms of each card should end in the same
    place." Course columns of equal card count must agree on every card's
    top AND bottom; the specials column leads with Secret. Driven at
    1920px: round 11 raised the 4-column floor to a 1320px pane, and only
    the 4-up layout puts all three course columns in ONE grid row — at
    2-up they wrap, and cross-row tops legitimately differ."""
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")

        with get_driver().launch(headless=True, viewport=(1920, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(200)

            columns = page.evaluate(
                "Array.from(document.querySelectorAll('.rank-page "
                ".scorecard-card .score-cards > .score-col')).map((col) => ({"
                "  kind: col.className.includes('score-col-courses')"
                "    ? 'courses' : 'specials',"
                "  cards: Array.from(col.querySelectorAll('.score-card'))"
                "    .map((card) => ({"
                "      label: card.querySelector('.score-card-name')"
                "        .textContent.trim(),"
                "      top: card.getBoundingClientRect().top,"
                "      bottom: card.getBoundingClientRect().bottom,"
                "    })),"
                "}))")

        course_cols = [col for col in columns if col["kind"] == "courses"]
        special_cols = [col for col in columns if col["kind"] == "specials"]
        assert len(course_cols) == 3, (
            f"the overall card chunks its courses into 3 columns, got "
            f"{len(course_cols)}")
        assert len(special_cols) == 1
        assert special_cols[0]["cards"][0]["label"] == "Secret", (
            "the specials column leads with Secret ('secret stars card, "
            "followed by bowser fights card')")
        assert columns[-1]["kind"] == "specials", (
            "the specials column sits on the far right")

        counts = {len(col["cards"]) for col in course_cols}
        assert counts == {len(course_cols[0]["cards"])}, (
            f"the fixture's course columns should chunk evenly, got {counts}"
            " — re-derive this test's alignment claim if the seed changed")
        for card_index in range(len(course_cols[0]["cards"])):
            tops = [col["cards"][card_index]["top"] for col in course_cols]
            bottoms = [col["cards"][card_index]["bottom"] for col in course_cols]
            assert max(tops) - min(tops) <= 1.5, (
                f"card {card_index}'s tops drift across columns: {tops}")
            assert max(bottoms) - min(bottoms) <= 1.5, (
                f"card {card_index}'s bottoms drift across columns: {bottoms}")


def test_every_card_labels_its_columns_and_keeps_them_in_register():
    """Round 11, both items in one render. Labels: round 7's ruling ("there
    should be column labels, because otherwise it's not obvious what each
    number means") regressed in the round-9 rebuild — every card owes a
    You/Goal/Δ row. Register: each line used to be its OWN grid, so a long
    time (1'00"00+) widened only its own line's column; the card is one
    column system now (subgrid), so within a card every line's You, Goal
    and Δ cells must share their right edge — "even if the times are
    XX'XX"XX long, they're all still aligned correctly"."""
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(200)

            cards = page.evaluate(
                "Array.from(document.querySelectorAll('.rank-page "
                ".scorecard-card .score-card')).map((card) => ({"
                "  labels: Array.from(card.querySelectorAll('.score-labels "
                "span')).map((el) => el.textContent.trim()),"
                "  you: Array.from(card.querySelectorAll('"
                ".score-lines .score-line-you'))"
                "    .map((el) => el.getBoundingClientRect().right),"
                "  goal: Array.from(card.querySelectorAll('"
                ".score-lines .score-line-goal'))"
                "    .map((el) => el.getBoundingClientRect().right),"
                "  gap: Array.from(card.querySelectorAll('"
                ".score-lines .score-gap'))"
                "    .map((el) => el.getBoundingClientRect().right),"
                "}))")

        assert cards, "no cards rendered"
        for card in cards:
            assert card["labels"] == ["You", "Goal", "Δ"], (
                f"a card is missing its column labels: {card['labels']}")
            for column in ("you", "goal", "gap"):
                edges = card[column]
                assert edges, f"a card drew no {column} cells"
                assert max(edges) - min(edges) <= 1.0, (
                    f"the {column} column drifts within one card: {edges}")


def test_a_full_monitor_gets_the_five_course_column_shape():
    """Round 12, his 4K read: "Maybe it should be 5 columns of 3 for the
    main courses, and then the two secret/bowser fights cards as the 6th
    column. Need enough width to support that." The component measures its
    own pane and rebuckets — the same cards, five course stacks wide, the
    specials column still last."""
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")

        with get_driver().launch(headless=True, viewport=(2860, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(400)  # the ResizeObserver's rebucket lands post-mount

            state = page.evaluate(
                "(() => {"
                "  const cards = document.querySelector('.rank-page "
                ".scorecard-card .score-cards');"
                "  const cols = Array.from(cards.querySelectorAll(':scope > "
                ".score-col'));"
                "  return {"
                "    dataCols: cards.dataset.cols,"
                "    width: cards.getBoundingClientRect().width,"
                "    kinds: cols.map((col) => col.className.includes("
                "'score-col-courses') ? 'courses' : 'specials'),"
                "    courseCounts: cols.filter((col) => col.className"
                ".includes('score-col-courses')).map((col) =>"
                " col.querySelectorAll('.score-card').length),"
                "  };"
                "})()")

        assert state["width"] >= 1900, (
            f"the pane itself is only {state['width']}px wide — the "
            "workspace cap is back")
        assert state["dataCols"] == "6", state
        course_count = sum(state["courseCounts"])
        per_column = -(-course_count // 5)          # ceil
        expected_columns = -(-course_count // per_column)
        assert len(state["courseCounts"]) == expected_columns, state
        assert state["kinds"][-1] == "specials", (
            "the Secret/Fights column stays on the far right")


def test_every_line_is_a_door_to_that_stars_library_page():
    """Round 11: "the star text hyperlinks to the library page for that
    star... We should also make the icon for each star link to the library
    in the same way." One button carries icon AND name; clicking it lands
    on that entity's Library target page. The hover glyph reserves its slot
    at rest (visibility hidden, never display none) so a wrapped name
    cannot reflow when it appears."""
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(200)

            line_count = page.count(
                ".rank-page .scorecard-card .score-lines .score-line")
            link_count = page.count(
                ".rank-page .scorecard-card .score-lines .score-line-link")
            assert link_count == line_count, (
                f"{line_count} lines but {link_count} doors — every line "
                "links, icon and name together")

            glyph = page.evaluate(
                "(() => {"
                "  const lib = document.querySelector('.rank-page "
                ".scorecard-card .score-line-lib');"
                "  if (!lib) return null;"
                "  const style = getComputedStyle(lib);"
                "  return { visibility: style.visibility, display: style.display };"
                "})()")
            assert glyph is not None, "no hidden library glyph in the name"
            assert glyph["visibility"] == "hidden", glyph
            assert glyph["display"] != "none", (
                "the glyph must RESERVE its slot (visibility), or hovering "
                "reflows the wrapped name")

            clicked_label = page.evaluate(
                "(() => {"
                "  const link = document.querySelector('.rank-page "
                ".scorecard-card .score-line-link');"
                "  const label = link.querySelector('.score-line-name')"
                "    .textContent.trim();"
                "  link.click();"
                "  return label;"
                "})()")
            page.wait_for(".library-page .library-target-page")
            page.wait_ms(300)
            heading = page.evaluate(
                "document.querySelector('.library-page .library-target-page')"
                ".textContent")

        assert clicked_label
        # the arrival page names the star the door was on
        assert clicked_label.split("+")[0].strip()[:12] in heading


def _option_click(value: str) -> str:
    return ("document.querySelector('.rank-page .scorecard-card "
            f".search-menu-option[data-value=\"{value}\"]').click()")


def test_the_picker_takes_several_goals_and_keeps_the_panel_open():
    """Round 14, his design: "what if we could select multiple options
    (e.g., I could select 10 players plus a rank standard like Toad 1)."
    Two picks through the REAL panel: it must stay open between them (ten
    picks cannot cost ten trips through the trigger), mark what is on, and
    store a `multi` goal whose sources are both."""
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .search-select-trigger")
            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".search-select-trigger').click()")
            page.wait_for(".rank-page .scorecard-card "
                          '.search-menu-option[data-value="division:Bronze:V"]')

            for value in ("division:Bronze:V", "division:Silver:III"):
                page.evaluate(_option_click(value))
                page.wait_ms(250)

            still_open = page.count(".rank-page .scorecard-card .search-menu")
            picked = page.evaluate(
                "Array.from(document.querySelectorAll('.rank-page "
                ".scorecard-card .search-menu-option.is-picked'))"
                ".map((el) => el.dataset.value)")
            label = page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".search-select-value').textContent.trim()")

        assert still_open == 1, "the panel must stay open while picking several"
        assert sorted(picked) == ["division:Bronze:V", "division:Silver:III"], picked
        assert label == "2 picked", label

        card = _get_scorecard(base)
        assert card["goal"] == {"kind": "multi", "sources": [
            {"kind": "division", "tier": "Bronze", "division": "V"},
            {"kind": "division", "tier": "Silver", "division": "III"}]}


def test_the_legend_names_every_pick_and_each_dot_wears_its_pick_colour():
    """Round 15: "it should show pills underneath the '4 picked'... each of
    the pills should get a designated color... then we should append a
    small dot using that color to the end of the star name... that clearly
    tells us that player two is the reason that the goal is that time."

    The two surfaces must agree by construction, so the test compares
    PAINTED colours: every dot's colour has to be one of the legend's, and
    a dot's own tooltip has to name the pill of that colour."""
    with serve_ui() as base:
        # Two picks whose times CROSS, so each really owns some stars. Two
        # divisions of one ladder never cross — the easier tier is
        # uniformly slower and would own everything, leaving the
        # attribution untested.
        _put_custom_goal(base, "alpha", {"star:1:0": 9000, "star:1:1": 1000})
        _put_custom_goal(base, "beta", {"star:1:0": 1000, "star:1:1": 9000})

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .search-select-trigger")
            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".search-select-trigger').click()")
            page.wait_for(".rank-page .scorecard-card "
                          '.search-menu-option[data-value="custom:alpha"]')
            page.evaluate(_option_click("custom:alpha"))   # beta is already on
            page.wait_ms(400)

            state = page.evaluate(
                "(() => {"
                "  const pills = Array.from(document.querySelectorAll("
                "    '.rank-page .scorecard-card .goal-pill'));"
                "  const legend = pills.map((pill) => ({"
                "    label: pill.textContent.trim(),"
                "    colour: getComputedStyle("
                "      pill.querySelector('.goal-pill-dot')).backgroundColor,"
                "  }));"
                "  const dots = Array.from(document.querySelectorAll("
                "    '.rank-page .scorecard-card .score-line-source')).map((dot) => ({"
                "    colour: getComputedStyle(dot).backgroundColor,"
                "    title: dot.getAttribute('title') || '',"
                "  }));"
                "  return { legend, dots };"
                "})()")

        legend = state["legend"]
        dots = state["dots"]
        assert len(legend) == 2, f"the legend must name every pick: {legend}"
        assert legend[0]["label"] != legend[1]["label"], legend
        assert legend[0]["colour"] != legend[1]["colour"], (
            f"each pill needs its own colour: {legend}")
        assert all(pill["colour"] not in ("rgba(0, 0, 0, 0)", "transparent")
                   for pill in legend), legend

        assert dots, "no attribution dots drawn for a multi goal"
        by_colour = {pill["colour"]: pill["label"] for pill in legend}
        for dot in dots:
            assert dot["colour"] in by_colour, (
                f"a dot wears a colour no pill has: {dot} vs {legend}")
            assert dot["title"] == by_colour[dot["colour"]], (
                "a dot's colour and its named source disagree: "
                f"{dot} vs {by_colour}")
        # both picks really do own tiles, or the attribution is untested
        assert len({dot["colour"] for dot in dots}) == 2, (
            f"only one source owns anything: {set(d['colour'] for d in dots)}")


def test_a_single_goal_draws_no_legend_and_no_dots():
    """One pick has nothing to tell apart, so the card stays clean."""
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(300)
            counts = page.evaluate(
                "({ pills: document.querySelectorAll('.rank-page "
                ".scorecard-card .goal-pill').length,"
                "   dots: document.querySelectorAll('.rank-page "
                ".scorecard-card .score-line-source').length })")
        assert counts == {"pills": 0, "dots": 0}, counts


def test_unpicking_the_last_goal_clears_it_and_one_pick_stays_single():
    """A list of one is not a new shape: it stores the goal in its own kind,
    so everything that reads a division goal keeps reading a division."""
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .search-select-trigger")
            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".search-select-trigger').click()")
            page.wait_for(".rank-page .scorecard-card "
                          '.search-menu-option[data-value="division:Bronze:V"]')
            page.evaluate(_option_click("division:Bronze:V"))
            page.wait_ms(300)

        assert _get_scorecard(base)["goal"] == {
            "kind": "division", "tier": "Bronze", "division": "V"}

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .search-select-trigger")
            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".search-select-trigger').click()")
            page.wait_for(".rank-page .scorecard-card "
                          ".search-menu-option.is-picked")
            page.evaluate(_option_click("division:Bronze:V"))   # toggle it off
            page.wait_ms(300)

        assert _get_scorecard(base)["goal"] is None


def test_no_star_name_leaves_a_blank_line_under_itself():
    """Round 13. The hover glyph reserves inline space, and on a name that
    fills its column that space landed the glyph ALONE on a second line:
    one line of visible text inside a two-line box, so the icon -- centred
    on the box, correctly -- drew below the text. His report: "the text
    ends up not being center aligned, and it appears to have incorrectly
    loaded above the course icon", naming six real stars.

    The property, measured rather than inspected: every name's last line of
    VISIBLE TEXT reaches the bottom of its own box. Driven at 2860px, where
    the six-column layout makes those exact names wrap (at 1500px nothing
    wraps and the defect cannot appear at all)."""
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")

        with get_driver().launch(headless=True, viewport=(2860, 1200)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(400)

            names = page.evaluate(
                "(() => {"
                "  const out = [];"
                "  for (const name of document.querySelectorAll('.rank-page "
                ".scorecard-card .score-line-name')) {"
                "    const walker = document.createTreeWalker(name, NodeFilter.SHOW_TEXT);"
                "    let last = null;"
                "    while (walker.nextNode())"
                "      if (walker.currentNode.data.trim()) last = walker.currentNode;"
                "    if (!last) continue;"
                "    const range = document.createRange();"
                "    range.setStart(name, 0);"
                "    range.setEnd(last, last.data.length);"
                "    const rects = Array.from(range.getClientRects());"
                "    if (!rects.length) continue;"
                "    const box = name.getBoundingClientRect();"
                "    out.push({ text: name.textContent.trim(),"
                "               lines: rects.length,"
                "               slack: box.bottom - Math.max(...rects.map((r) => r.bottom)) });"
                "  }"
                "  return out;"
                "})()")

        assert names, "no names measured"
        wrapped = [row for row in names if row["lines"] > 1]
        assert wrapped, (
            "no name wrapped at this width — the fixture can no longer "
            "exhibit the defect, so this guard proves nothing; widen the "
            "names or narrow the viewport")
        blank_tailed = [row for row in names if row["slack"] > 4]
        assert not blank_tailed, (
            "these names end in a blank line, so their icon centres below "
            "the text: "
            + ", ".join(f"{row['text']!r} (+{row['slack']:.1f}px)"
                        for row in blank_tailed[:6]))
