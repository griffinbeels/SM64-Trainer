"""The Rank tab's scorecard card (spec 2026-08-23-scorecard-design, task 3).

Two layers, same split every rendered-card feature in this app uses:
`ui/scorecardgoal.js` is pure and import-free (division options + gap
formatting), driven through node exactly like ui/entitysection.js
(tests/test_ui_entity_section.py); `ui/components/scorecard.js` is the
Preact card, driven through a real browser via tools/ui_fixture.py.
"""
import json
import shutil
import time
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "src" / "sm64_events" / "ui"
SCORECARDGOAL_JS = (UI / "scorecardgoal.js").as_uri()

# The card labels the Overall scope draws, in the order round 20 fixed them
# in -- read off the BUILDER rather than restated, so a course rename or a
# reordering cannot leave this file quietly asserting the old world.
from sm64_events.memory.addresses import COURSE_NAMES  # noqa: E402
from sm64_events.ranks.scorecard import BOWSER_LABEL, SECRET_LABEL  # noqa: E402

CARD_ORDER = [COURSE_NAMES[course_id] for course_id in range(1, 16)] \
    + [SECRET_LABEL, BOWSER_LABEL]

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def _viewport_for_pane(pane: int) -> int:
    """The window width that gives the scorecard a pane of `pane` px: the
    shell spends 283px beside the pane with the wide sidebar (1180px windows
    and up) and 153px with the rail below that -- both measured; the test
    asserts the pane it actually got, so a shell change shows up here."""
    wide = pane + 283
    return wide if wide >= 1180 else pane + 153


def _column_floors() -> list:
    """`CARD_COLUMN_FLOORS` as the module ships it, so a render test can
    probe each floor rather than a copy of it."""
    script = (f"import * as mod from {SCORECARDGOAL_JS!r};\n"
              "console.log(JSON.stringify(mod.CARD_COLUMN_FLOORS));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


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


def test_division_options_has_every_finite_goal_hardest_first():
    options = call("divisionOptions")
    assert len(options) == 44
    assert options[0] == {"value": "division:Mario:I", "label": "Mario 1"}
    assert options[-1] == {"value": "division:Iron:IV", "label": "Capless 4"}
    assert not any(o["value"] == "division:Iron:V" for o in options)


def test_division_options_covers_all_tiers_and_omits_only_the_unbounded_floor():
    options = call("divisionOptions")
    by_tier = {}
    for option in options:
        _, tier, division = option["value"].split(":")
        by_tier.setdefault(tier, []).append(division)
    assert set(by_tier.keys()) == {
        "Mario", "Grandmaster", "Master", "Diamond",
        "Platinum", "Gold", "Silver", "Bronze", "Iron"}
    for tier, divisions in by_tier.items():
        expected = ["I", "II", "III", "IV"] + ([] if tier == "Iron" else ["V"])
        assert sorted(divisions) == sorted(expected), tier


def test_fmt_gap_cs_prints_a_signed_two_decimal_second_value():
    assert call("fmtGapCs", -437) == "-4.37"
    assert call("fmtGapCs", 40) == "+0.40"


def test_fmt_gap_cs_signs_a_positive_gap_too():
    assert call("fmtGapCs", 100) == "+1.00"


def test_goal_groups_separates_rank_players_and_custom_sets():
    groups = call("goalGroups", ["808sAndBailey", "Suigi"], ["Practice set"])
    rank = groups["rank"][0]["options"]
    assert rank[0] == {"value": "", "label": "Automatic"}
    assert len(rank) == 45
    assert all(option["value"].startswith("division:") for option in rank[1:])
    assert groups["players"][0]["options"] == [
        {"value": "runner:808sAndBailey", "label": "808sAndBailey"},
        {"value": "runner:Suigi", "label": "Suigi"}]
    assert groups["custom"][0]["options"] == [{"value": "custom:Practice set", "label": "Practice set"}]
    empty = call("goalGroups", None, [])
    assert empty["players"][0]["options"] == empty["custom"][0]["options"] == []


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
            "sum": {"you_cs": 2100, "goal_cs": 1000, "delta_cs": -100,
                    "counted": 1, "total": 2},
        }],
        "total": {"you_cs": 2100, "goal_cs": 1000, "delta_cs": -100,
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
    payload = {"rows": [], "total": {"you_cs": None, "goal_cs": None, "delta_cs": None,
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
            "sum": {"you_cs": 5000, "goal_cs": None, "delta_cs": None, "counted": 0, "total": 1},
        }],
        "total": {"you_cs": 5000, "goal_cs": None, "delta_cs": None, "counted": 0, "total": 1},
        "goal_coverage": {"covered": 0, "tiles": 1},
    }
    result = call("applyGoalOverrides", payload, {"star:1:6": 4800})
    tile = result["rows"][0]["tiles"][0]
    assert tile["goal_cs"] == 4800 and tile["delta_cs"] == 200
    assert result["rows"][0]["sum"]["counted"] == 1
    assert result["rows"][0]["sum"]["delta_cs"] == 200
    assert result["total"]["counted"] == 1
    assert result["goal_coverage"]["covered"] == 1


def test_apply_goal_overrides_sums_the_goal_with_nothing_of_his():
    """Round 29's rule on the LIVE side: typing goals onto a card he has no
    times on must sum the goal column and leave his side and the delta
    null -- the exact state his wiped-data screenshot showed as three
    em-dashes."""
    payload = {
        "rows": [{
            "course_id": 1, "label": "X",
            "tiles": [{"key": "star:1:0", "label": "A", "you_cs": None,
                       "goal_cs": 1000, "delta_cs": None},
                      {"key": "star:1:1", "label": "B", "you_cs": None,
                       "goal_cs": None, "delta_cs": None}],
            "sum": {"you_cs": None, "goal_cs": 1000, "delta_cs": None, "counted": 0, "total": 2},
        }],
        "total": {"you_cs": None, "goal_cs": 1000, "delta_cs": None, "counted": 0, "total": 2},
        "goal_coverage": {"covered": 1, "tiles": 2},
    }
    result = call("applyGoalOverrides", payload, {"star:1:1": 1200})
    assert result["rows"][0]["sum"] == {
        "you_cs": None, "goal_cs": 2200, "delta_cs": None, "counted": 0, "total": 2}
    assert result["total"] == result["rows"][0]["sum"]


# --- the rendered card -------------------------------------------------

sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui, serve_ui_live  # noqa: E402
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


def _put_multi_goal(base: str, sources: list) -> None:
    body = json.dumps({"kind": "multi", "sources": sources}).encode()
    request = urllib.request.Request(
        f"{base}/api/scorecard/goal", data=body, method="PUT",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200


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
        assert (you_text, goal_text) == _expected_foot_sums(row_sum)


def _expected_foot_sums(row_sum: dict) -> tuple[str, str]:
    """What a card's foot prints for You and Goal, from the payload's own
    sum: each side on its own presence (round 29), an em-dash for a null."""
    return (_fmt_seconds_like_js(row_sum["you_cs"] / 100)
            if row_sum["you_cs"] is not None else "—",
            _fmt_seconds_like_js(row_sum["goal_cs"] / 100)
            if row_sum["goal_cs"] is not None else "—")


def _read_feet(page) -> list[dict]:
    """Every rendered card's foot: label, the three printed cells, and the
    coverage chip's text ("" when the chip is not drawn)."""
    return page.evaluate(
        "Array.from(document.querySelectorAll('.rank-page .scorecard-card "
        ".score-card')).map((card) => {"
        "  const foot = card.querySelector('.score-card-foot');"
        "  const chip = foot.querySelector('.score-sum-coverage');"
        "  return {name: card.querySelector('.score-card-name').textContent.trim(),"
        "          you: foot.querySelector('.score-line-you').textContent.trim(),"
        "          goal: foot.querySelector('.score-line-goal').textContent.trim(),"
        "          gap: foot.querySelector('.score-gap').textContent.trim(),"
        "          chip: chip ? chip.textContent.trim() : ''};"
        "})")


def _wipe_all(base: str) -> None:
    body = json.dumps({"kind": "all", "scope": "lifetime"}).encode()
    request = urllib.request.Request(
        f"{base}/api/wipe", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200


def test_the_goal_stage_sum_prints_with_nothing_of_his_on_the_card():
    """Round 29 item 1, his exact case: Settings -> wipe all practice data,
    then the Rank tab's scorecard. Every foot used to read "--" in all
    three columns under "0/6"; now the GOAL column still sums, his column
    and the gap are em-dashes, and the chip says 0/n."""
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")
        _wipe_all(base)
        payload = _get_scorecard(base)
        assert all(tile["you_cs"] is None
                   for row in payload["rows"] for tile in row["tiles"]), (
            "the wipe left a time of his on the card -- this test would then "
            "prove nothing about the empty-YOU case")
        summed_rows = [row for row in payload["rows"] if row["sum"]["goal_cs"] is not None]
        assert summed_rows, "the division goal covers no card at all"

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card-foot")
            page.wait_ms(200)
            feet = {foot["name"]: foot for foot in _read_feet(page)}

        for row in payload["rows"]:
            foot = feet[row["label"]]
            expected_you, expected_goal = _expected_foot_sums(row["sum"])
            assert foot["you"] == "—" == expected_you, (row["label"], foot)
            assert foot["goal"] == expected_goal, (row["label"], foot)
            assert foot["gap"] == "—", (row["label"], foot)
            assert foot["chip"] == f"0/{row['sum']['total']}", (row["label"], foot)
        assert any(feet[row["label"]]["goal"] != "—" for row in summed_rows)


def test_a_partly_shared_card_prints_each_sides_own_sum_and_the_shared_gap():
    """The 3/6-shaped case: a runner goal is partial by nature, and the
    seeded fixture's PBs cover other lines, so at least one card has times
    on BOTH sides over DIFFERENT lines. Its foot must print his sum over his
    lines, the goal's over its lines, and a gap over only the shared ones
    -- which is why the printed gap is NOT you-minus-goal there -- with the
    chip saying how many lines the gap compares."""
    with serve_ui() as base:
        _put_runner_goal(base, _any_sheet_runner())
        payload = _get_scorecard(base)
        split_rows = [row for row in payload["rows"]
                      if row["sum"]["you_cs"] is not None
                      and row["sum"]["goal_cs"] is not None
                      and 0 < row["sum"]["counted"] < row["sum"]["total"]
                      and row["sum"]["delta_cs"]
                      != row["sum"]["you_cs"] - row["sum"]["goal_cs"]]
        assert split_rows, (
            "no card has both sides over different lines -- pick another "
            "runner, or this test proves nothing about the split-sets case")

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card-foot")
            page.wait_ms(200)
            feet = {foot["name"]: foot for foot in _read_feet(page)}

        for row in split_rows:
            foot = feet[row["label"]]
            row_sum = row["sum"]
            assert (foot["you"], foot["goal"]) == _expected_foot_sums(row_sum), (row["label"], foot)
            sign = "-" if row_sum["delta_cs"] < 0 else "+"
            assert foot["gap"] == f"{sign}{abs(row_sum['delta_cs']) / 100:.2f}", (row["label"], foot)
            assert foot["chip"] == f"{row_sum['counted']}/{row_sum['total']}", (row["label"], foot)


def test_the_card_reaches_a_real_goal_covers_line_when_partial():
    with serve_ui() as base:
        # Include an untimed castle star so the note measures a genuinely
        # missing standard; Automatic now covers ordinary runner gaps.
        urllib.request.urlopen(urllib.request.Request(
            f"{base}/api/marelo/exclude", data=b'{"entity":"star:0:0","excluded":false}',
            method="POST", headers={"Content-Type": "application/json"}), timeout=10).read()
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
            page.wait_for(".rank-page .scorecard-card [data-goal-control=\"Player Goal\"] .search-select-trigger")

            # Before the open: no Runners options have landed. (Divisions
            # are present immediately -- they cost no fetch.)
            pre_open = page.evaluate(
                "document.querySelectorAll('.rank-page .scorecard-card "
                ".search-menu-option').length")
            assert pre_open == 0, "the menu must not be open yet"

            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                "[data-goal-control=\"Player Goal\"] .search-select-trigger').click()")
            # Lazy fetch: wait for THIS runner's option to actually land
            # rather than assuming a fixed delay covers the round trip.
            page.wait_for(
                ".rank-page .scorecard-card "
                f'.search-menu-option[data-value="runner:{runner}"]')

            group_heads = page.evaluate(
                "Array.from(document.querySelectorAll('.rank-page "
                ".scorecard-card .search-menu-group-head'))"
                ".map((el) => el.textContent)")
            assert group_heads == []

            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                f'.search-menu-option[data-value="runner:{runner}"]\').click()')
            page.wait_for(
                ".rank-page .scorecard-card .score-gap.good, "
                ".rank-page .scorecard-card .score-gap.bad")

        card = _get_scorecard(base)
        assert card["goal"]["sources"][1] == {"kind": "runner", "runner": runner}
        coverage = card["goal_coverage"]
        assert coverage["covered"] == coverage["tiles"]


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

            # Automatic mode supplies a time; clear the draft explicitly.
            _open_goal_editor(page, row_label, tile_label)
            _type_into_goal_editor(page, "")
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
        assert unsaved_goal["kind"] == "automatic", "an unsaved edit must not save a manual goal"


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
                "[data-goal-control=\"Custom Goals\"] .search-select-value').textContent")

            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                "[data-goal-control=\"Custom Goals\"] .search-select-trigger').click()")
            page.wait_for(".rank-page .scorecard-card .search-menu-option")
            group_heads = page.evaluate(
                "Array.from(document.querySelectorAll('.rank-page .scorecard-card "
                ".search-menu-group-head')).map((el) => el.textContent)")

        assert trigger_label == "My Sub 5 Attempt"
        # Custom sits right after "No goal" -- ahead of Divisions/Runners.
        assert group_heads == []

        card = _get_scorecard(base)
        assert card["goal"]["sources"][1] == {"kind": "custom", "name": "My Sub 5 Attempt"}
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
        # The fixture's own seeded star (ui_fixture.FIXTURE_COURSE/STAR: WF
        # star 4), so ONE line of the column carries a real time -- without
        # it every line is empty and "the column reached the page" is proved
        # by comparing blank strings.
        (5, 1): {"text": "2. Whomp's Fortress"},
        (6, 1): {"text": "[5] Fall onto the Caged Island", "bold": True},
        (6, 2): {"text": "13.50"},
        # A runner column with Raisn's legend (round 29 item 2): rows 2/3
        # name the platforms in two fills, and an N64 time on the fixture's
        # own star, faster than the seeded PB so an import of this column
        # lands it -- the end-to-end paint test imports Griff and copies.
        (1, 7): {"text": "Griff"},
        (2, 7): {"text": "Emu", "fill": "FFA5A9F1"},
        (3, 7): {"text": "N64", "fill": "theme:8"},
        (6, 7): {"text": "11.00", "fill": "theme:8"},
    }
    return build_workbook({wb.SHEET_MAIN: cells,
                           wb.SHEET_LOG: {(1, 1): {"text": "46238.5"}}})


# Replaces ONLY `writeText`, per the brief -- `navigator.clipboard` itself is
# real (127.0.0.1 is a secure context in Chromium), so this leaves every
# other clipboard method alone and proves the shim actually installed by
# returning `true` rather than letting a missing `navigator.clipboard`
# fail silently.
def _wait_until(page, expression, timeout_ms=15000, step_ms=100):
    """Poll a JS predicate. uilab's own `wait_for` takes a SELECTOR, and what
    round 26's polled copy needs to wait on is a value on `window` -- a fixed
    `wait_ms` here is the coin-flip this file has already paid for once."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if page.evaluate(expression):
            return
        page.wait_ms(step_ms)
    raise AssertionError(f"timed out waiting for {expression!r}")


_INSTALL_CLIPBOARD_SHIM = """(() => {
  window.__scorecardCopied = [];
  window.__scorecardHtml = [];
  // Round 29: the auto-copy runs only when the document has focus, which a
  // headless page cannot be relied on to report either way -- so every test
  // says which case it is in. TRUE here; the away-from-the-page tests flip it.
  document.hasFocus = () => true;
  window.__columnPosts = 0;
  const realFetch = window.fetch.bind(window);
  window.fetch = (url, init) => {
    if (String(url).endsWith("/api/scorecard/column") && init && init.method === "POST") {
      window.__columnPosts += 1;
    }
    return realFetch(url, init);
  };
  navigator.clipboard.writeText = (text) => {
    window.__scorecardCopied.push(text);
    return Promise.resolve();
  };
  // Round 26 writes BOTH flavours through `clipboard.write`, so the shim has
  // to capture both or the plain-text assertion below silently stops seeing
  // anything -- which is exactly how it failed the moment the flavour was
  // added. Blob.text() is async, hence the awaited collection.
  navigator.clipboard.write = async (items) => {
    for (const item of items) {
      if (item.types.includes("text/plain")) {
        window.__scorecardCopied.push(await (await item.getType("text/plain")).text());
      }
      if (item.types.includes("text/html")) {
        window.__scorecardHtml.push(await (await item.getType("text/html")).text());
      }
    }
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
            # Round 26 put a POLLED job behind the button, so the copy lands a
            # poll interval or two after the click rather than on the first
            # fetch's resolution.
            _wait_until(page, "window.__scorecardCopied.length > 0")
            # A SEPARATE evaluate call, after the wait -- reading the
            # captured text in the same call as the click would race the
            # button's own async fetch-then-copy chain.
            copied = page.evaluate("window.__scorecardCopied")
            html_copied = page.evaluate("window.__scorecardHtml")
            # Round 23, his report: the button read "Copied ✓" AND the error
            # slot read `COPIED_FLASH_MS is not defined` -- the copy had
            # succeeded and the flash timer threw. This test read the
            # clipboard and never the button, so the throw was invisible.
            # Now: the label flashes, nothing lands in the error slot, and
            # the label RETURNS so the control reads as usable again.
            label_now = page.evaluate(
                "document.querySelector('.scorecard-copy-column').textContent.trim()")
            errors = page.count(".rank-page .scorecard-exports .inline-state.error")
            page.wait_ms(1500)
            label_later = page.evaluate(
                "document.querySelector('.scorecard-copy-column').textContent.trim()")

        assert len(copied) == 1
        assert len(copied[0].split("\n")) == column["total_rows"]
        # ROUND 26 item 2: the clipboard also carries one explicit `<tr>` per
        # worksheet row, empty ones included -- "I would expect it to end at
        # 804, even if we don't have entries. It should paste empty entries
        # then." A plain-text block whose tail is a run of newlines is the
        # ambiguous case; a table's rows are structure. This asserts the row
        # COUNT rather than the markup, because the count is the claim.
        assert len(html_copied) == 1, "no HTML flavour reached the clipboard"
        assert html_copied[0].count("<tr>") == column["total_rows"], (
            f"the HTML flavour carries {html_copied[0].count('<tr>')} rows, "
            f"the column has {column['total_rows']}")
        assert label_now == "Copied ✓", label_now
        assert errors == 0, "a successful copy must leave the error slot empty"
        # Round 29: the column is HELD once built, so the label returns to the
        # held state's own word rather than to the build's -- a second click
        # copies what is on the page instead of downloading the sheet again.
        assert label_later == "Ready to copy", (
            f"the confirmation must be transient, still reads {label_later!r}")


def _read_copy_label(page) -> str:
    return page.evaluate(
        "document.querySelector('.scorecard-copy-column').textContent.trim()")


def test_a_column_built_while_he_is_away_waits_on_the_page(monkeypatch):
    """ROUND 29 item 3, his report: "I have to be tabbed in in order for it
    to grab my clipboard. This throws an error if I'm not there... store the
    result on the page so that if the player tabs out, they can come back
    and grab it whenever they're ready... The button should say 'Ready to
    Copy' when it's ready."

    The document has NO focus while the job runs and lands. Nothing may
    reach the clipboard, nothing may land in the error slot, and the column
    must be on the page: the button reads "Ready to copy", Open shows every
    line, and a click once he is back copies the HELD text without a second
    download (the column POST count does not move)."""
    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", _stub_workbook)
    with serve_ui() as base:
        column = json.loads(urllib.request.urlopen(
            f"{base}/api/scorecard/column", timeout=10).read())
        expected_text = "\n".join(column["lines"])

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-copy-column")
            assert page.evaluate(_INSTALL_CLIPBOARD_SHIM) is True
            page.evaluate("document.hasFocus = () => false")

            page.evaluate("document.querySelector('.scorecard-copy-column').click()")
            _wait_until(page, "document.querySelector('.scorecard-exports')"
                              ".dataset.held === 'true'")
            page.wait_ms(200)
            copied_while_away = page.evaluate("window.__scorecardCopied.length")
            errors_while_away = page.count(".rank-page .scorecard-exports .inline-state.error")
            label_while_away = _read_copy_label(page)
            open_controls = page.count(".rank-page .scorecard-column-open")
            posts_after_build = page.evaluate("window.__columnPosts")

            # He comes back and inspects it first.
            page.evaluate("document.querySelector('.scorecard-column-open').click()")
            page.wait_for(".modal .scorecard-column-view")
            shown_text = page.evaluate(
                "document.querySelector('.modal .scorecard-column-view').value")
            page.evaluate("document.querySelector('.modal-close').click()")
            page.wait_ms(100)
            modal_left = page.count(".modal") == 0

            # Then grabs it: the held text, no second download.
            page.evaluate("document.hasFocus = () => true")
            page.evaluate("document.querySelector('.scorecard-copy-column').click()")
            _wait_until(page, "window.__scorecardCopied.length > 0")
            label_after_copy = _read_copy_label(page)
            copied = page.evaluate("window.__scorecardCopied")
            posts_after_copy = page.evaluate("window.__columnPosts")
            page.wait_ms(1600)
            label_settled = _read_copy_label(page)

    assert copied_while_away == 0, "the clipboard was written while the page had no focus"
    assert errors_while_away == 0, "an unfocused finish must not read as a failure"
    assert label_while_away == "Ready to copy", label_while_away
    assert open_controls == 1, "no Open control beside the held column"
    assert shown_text == expected_text, "Open does not show the column as built"
    assert modal_left, "the inspect box did not close"
    assert posts_after_build == 1
    assert copied == [expected_text]
    assert posts_after_copy == 1, "copying the held column downloaded the sheet again"
    assert label_after_copy == "Copied ✓", label_after_copy
    assert label_settled == "Ready to copy", (
        f"the column must stay held after a copy, label reads {label_settled!r}")


def test_a_clipboard_refusal_leaves_the_column_on_the_page(monkeypatch):
    """The failure he saw, kept from costing the column: the browser refuses
    the write (its own NotAllowedError when focus was lost between the check
    and the write, a denied permission, a shell with no clipboard). The
    error slot names the clipboard, and the column is still held -- the
    button reads "Ready to copy" and Open shows every line."""
    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", _stub_workbook)
    with serve_ui() as base:
        column = json.loads(urllib.request.urlopen(
            f"{base}/api/scorecard/column", timeout=10).read())

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-copy-column")
            assert page.evaluate(_INSTALL_CLIPBOARD_SHIM) is True
            page.evaluate("""
              navigator.clipboard.write = () => Promise.reject(
                new DOMException("Document is not focused.", "NotAllowedError"));
              navigator.clipboard.writeText = navigator.clipboard.write;
            """)

            page.evaluate("document.querySelector('.scorecard-copy-column').click()")
            page.wait_for(".rank-page .scorecard-exports .inline-state.error")
            page.wait_ms(200)
            error = page.evaluate(
                "document.querySelector('.rank-page .scorecard-exports "
                ".inline-state.error').textContent")
            label = _read_copy_label(page)
            held = page.evaluate(
                "document.querySelector('.scorecard-exports').dataset.held")
            page.evaluate("document.querySelector('.scorecard-column-open').click()")
            page.wait_for(".modal .scorecard-column-view")
            shown_lines = page.evaluate(
                "document.querySelector('.modal .scorecard-column-view')"
                ".value.split('\\n').length")

    assert "clipboard" in error and "not focused" in error, error
    assert "still here" in error, f"the message must say the column survived: {error!r}"
    assert held == "true" and label == "Ready to copy", (held, label)
    assert shown_lines == column["total_rows"]


def test_his_times_changing_drops_the_held_column(monkeypatch):
    """The held column is a snapshot of his times. A time of his landing
    bumps the Rank tab's staleness key, and a snapshot from before it would
    paste stale into his sheet -- so the hold ends and the button offers the
    build again. Driven through the store's own path: a `pb_saved` event on
    the live WebSocket is what bumps `t.mareloRev`."""
    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", _stub_workbook)
    with serve_ui_live() as (base, service):
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-copy-column")
            assert page.evaluate(_INSTALL_CLIPBOARD_SHIM) is True
            # Built while away, so the label is the held state's own word and
            # not the auto-copy's transient "Copied" flash.
            page.evaluate("document.hasFocus = () => false")

            page.evaluate("document.querySelector('.scorecard-copy-column').click()")
            _wait_until(page, "document.querySelector('.scorecard-exports')"
                              ".dataset.held === 'true'")
            label_held = _read_copy_label(page)

            _publish_pb_saved(service)
            _wait_until(page, "document.querySelector('.scorecard-exports')"
                              ".dataset.held === 'false'")
            label_after = _read_copy_label(page)

    assert label_held == "Ready to copy", label_held
    assert label_after == "Copy sheet column", label_after


def _publish_pb_saved(service) -> None:
    """One `pb_saved` broadcast, the way the server's own PB save announces
    itself -- through the live service's broadcaster, and NOT through
    `service.publish`, which would also journal an empty PB event. In a
    worker thread because the browser driver's sync API owns this thread's
    loop (test_ui_recorder_latency.py's own pattern)."""
    import asyncio
    import datetime as dt
    import threading

    from sm64_events.core.events import Event

    async def go():
        await service.broadcaster.publish(Event(
            type="pb_saved", frame=0,
            timestamp_utc=dt.datetime.now(dt.timezone.utc), payload={}))

    published = threading.Thread(target=lambda: asyncio.run(go()))
    published.start()
    published.join(timeout=10)
    assert not published.is_alive(), "the pb_saved broadcast did not return"


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


def test_the_copy_narrates_its_real_steps_and_says_what_it_copied(monkeypatch):
    """ROUND 26 item 1, his words: "Right now it feels like lag, but I know
    that's just how long it takes to confirm things. But that's because I
    developed the tool. We should show a status line that updates at every
    step of the process."

    Two claims, and both are about the line being REAL rather than decorative.
    It must show MORE THAN ONE distinct sentence during one copy -- a single
    "Working..." would satisfy a weaker assertion while telling him nothing
    about which step he is on -- and its closing sentence must name what
    actually landed: the row count, the worksheet range it covers, and how
    many of those rows carry a time. That last part is also what lets him
    confirm the paste reaches row 804 without counting cells."""
    # A SLOW sheet read, deliberately: the fixture workbook is three rows and
    # the whole job finishes inside one poll, so against it the line would
    # show its closing sentence and nothing else -- and "it updated at every
    # step" would be untestable exactly where he asked for it. Half a second
    # is the real shape of his own copy (a ~5.6 MB fetch), compressed.
    def slow_stub(*args, **kwargs):
        time.sleep(0.5)
        return _stub_workbook(*args, **kwargs)

    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", slow_stub)
    with serve_ui() as base:
        column = json.loads(urllib.request.urlopen(
            f"{base}/api/scorecard/column", timeout=20).read())

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-copy-column")
            assert page.evaluate(_INSTALL_CLIPBOARD_SHIM) is True

            # Collect every distinct sentence the line shows, sampled as the
            # copy runs -- the steps are the point, so reading only the last
            # one would pass through a line that never updated.
            page.evaluate("""
              (() => {
                window.__steps = [];
                const read = () => {
                  const el = document.querySelector('.scorecard-status');
                  if (!el) return;
                  const text = el.textContent.trim();
                  if (text && window.__steps[window.__steps.length - 1] !== text) {
                    window.__steps.push(text);
                  }
                };
                window.__stepTimer = setInterval(read, 30);
              })()
            """)
            page.evaluate(
                "document.querySelector('.scorecard-copy-column').click()")
            _wait_until(page, "window.__scorecardCopied.length > 0")
            page.wait_ms(300)
            page.evaluate("clearInterval(window.__stepTimer)")
            steps = page.evaluate("window.__steps")
            fill = page.evaluate(
                "document.querySelector('.scorecard-status .job-status-fill').style.width")

    assert len(steps) >= 2, (
        f"the status line showed one sentence for the whole copy: {steps}")
    closing = steps[-1]
    assert str(column["total_rows"]) in closing, (closing, column["total_rows"])
    assert str(column["total_rows"] + 1) in closing, (
        f"the closing line must name the last worksheet row it covers: {closing!r}")
    assert str(column["mapped"]) in closing, (closing, column["mapped"])
    assert fill == "100%", f"the progress track did not finish: {fill!r}"


def test_the_export_sits_under_the_cards_not_in_the_head():
    """Round 25, his placement: "The button should go at the bottom,
    underneath all the cards." Read as DOM order and as geometry -- a rule
    about where something sits has to be checked where it is drawn, not
    where it is declared."""
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-copy-column")
            page.wait_ms(300)
            state = page.evaluate("""
              (() => {
                const card = document.querySelector('.rank-page .scorecard-card');
                const kids = Array.from(card.children);
                const exports = card.querySelector('.scorecard-exports');
                const cards = card.querySelector('.score-cards');
                return {
                  inHead: !!card.querySelector('.scorecard-head .scorecard-exports'),
                  lastChild: kids[kids.length - 1] === exports,
                  belowCards: exports.getBoundingClientRect().top
                              >= cards.getBoundingClientRect().bottom,
                };
              })()
            """)
    assert state["inHead"] is False, "the export is still inside the card's head"
    assert state["lastChild"] is True, "the export is not the card's last child"
    assert state["belowCards"] is True, "the export does not sit below the cards"


def test_clicking_copy_again_clears_the_previous_error(monkeypatch):
    """Round 25, his words: "If there's an error, and I click 'copy sheet
    column' again, the error should disappear. If there's a new error, the
    new error should show." A message that outlives the gesture it explains
    reads as the retry having failed the same way.

    Both halves, because clearing alone would be a message that never comes
    back: the first click fails one way, the second fails DIFFERENTLY, and
    the sentence on screen has to be the second one."""
    calls = {"n": 0}

    def boom(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            # The second attempt is SLOW on purpose: the cleared window is
            # what this test is about, and a failure that resolves in a few
            # milliseconds leaves nothing to sample. The column route runs
            # its fetch in a threadpool, so sleeping here blocks that one
            # request and nothing else.
            time.sleep(0.8)
            raise OSError("the sheet is a teapot")
        raise OSError("no route to host")

    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", boom)
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-copy-column")

            read = ("(document.querySelector('.rank-page .scorecard-exports "
                    ".inline-state.error') || {}).textContent")
            page.evaluate("document.querySelector('.scorecard-copy-column').click()")
            page.wait_for(".rank-page .scorecard-exports .inline-state.error")
            first = page.evaluate(read)

            page.evaluate("document.querySelector('.scorecard-copy-column').click()")
            page.wait_ms(250)
            during = page.evaluate(read)
            page.wait_for(".rank-page .scorecard-exports .inline-state.error")
            page.wait_ms(300)
            second = page.evaluate(read)

    assert "no route to host" in first, first
    assert not during, f"the old error survived the retry: {during!r}"
    assert "teapot" in second, second


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
            # Changing scope clears the old cards while its payload loads.
            # This gesture is ready when the new route's two cards arrive.
            deadline = time.monotonic() + 8
            while page.count(".rank-page .scorecard-card .score-card") != 2 and time.monotonic() < deadline:
                page.wait_ms(25)
            assert page.count(".rank-page .scorecard-card .score-card") == 2

            removed_label = page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                ".score-card .score-card-name').textContent.trim()")
            page.evaluate(
                "document.querySelectorAll('.rank-page .scorecard-card "
                ".score-row-remove')[0].click()")
            # Cards live inside responsive column wrappers. Wait on their
            # total count, independent of how those columns are arranged.
            deadline = time.monotonic() + 8
            while page.count(".rank-page .scorecard-card .score-card") != 1 and time.monotonic() < deadline:
                page.wait_ms(25)

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


def test_there_is_no_caps_toggle_and_no_cap_on_any_line():
    """Round 23 (his call): "Let's remove the 'Show rank caps' button. Not
    going to use it ever. Should just get rid of it." Gone with it: the
    per-line cap draw and the server grading behind it -- a control with no
    door is dead code here, not a hidden feature."""
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(200)
            counts = page.evaluate(
                "({ toggles: document.querySelectorAll('.rank-page "
                ".scorecard-caps-toggle').length,"
                "   caps: document.querySelectorAll('.rank-page "
                ".scorecard-card .score-line-cap').length })")
        assert counts == {"toggles": 0, "caps": 0}, counts


def test_the_cards_sit_in_four_aligned_columns_reading_down_in_course_order():
    """Round 20's placement, his words as geometry: "We should fill columns
    top to bottom, then left to right... [BOB] [BBH] [DDD] [THI] / [WF] [HMC]
    [SL] [TTC] / [JRB] [LLL] [WDW] [RR] / [CCM] [SSL] [TTM] [Secrets + Bowser
    combined into a single card]", with round 10's alignment claim intact:
    "The tops and bottoms of each card should end in the same place."

    RENDERED, not chunked: the node tests above already prove `cardColumns`
    chunks column-major, and this is the other half — that the browser draws
    the stacks it chunked, in that order, with their rows in register.
    Driven at 2100px, where the 4-up track count applies (round 32 raised
    the 4-up floor to a 1700px pane so no name wraps; 1920 sits just under
    it)."""
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")

        with get_driver().launch(headless=True, viewport=(2100, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(200)

            columns = page.evaluate(
                "Array.from(document.querySelectorAll('.rank-page "
                ".scorecard-card .score-cards > .score-col')).map((col) => ("
                "  Array.from(col.querySelectorAll('.score-card'))"
                "    .map((card) => ({"
                "      label: card.querySelector('.score-card-name')"
                "        .textContent.trim(),"
                "      lines: card.querySelectorAll('.score-line').length,"
                "      top: card.getBoundingClientRect().top,"
                "      bottom: card.getBoundingClientRect().bottom,"
                "    }))"
                "))")

        assert len(columns) == 4, (
            f"his grid is four columns wide, drew {len(columns)}")
        drawn = [card["label"] for column in columns for card in column]
        assert drawn == sorted(drawn, key=CARD_ORDER.index), (
            "reading down each column in turn must walk the course list in "
            f"order; drew {drawn}")
        assert drawn[-1] == SECRET_LABEL, (
            "Secret closes the last column; Bowser is centred beneath (round 32)")

        # Round 21: 15 course cards chunk [4, 4, 4, 3]; Secret appends to
        # the last column, so every column holds four (round 32 moved
        # Bowser to its own centred row). Rows register across columns (the
        # column stacks are subgrids on the outer grid's row tracks), but no
        # COLUMN stretches to the tallest one any more -- round 20's merged
        # card made every card its column's average, 272px for six lines.
        counts = [len(column) for column in columns]
        assert counts == [4, 4, 4, 4], counts
        for card_index in range(4):
            tops = [column[card_index]["top"] for column in columns]
            bottoms = [column[card_index]["bottom"] for column in columns]
            assert max(tops) - min(tops) <= 1.5, (
                f"card {card_index}'s tops drift across columns: {tops}")
            assert max(bottoms) - min(bottoms) <= 1.5, (
                f"card {card_index}'s bottoms drift across columns: {bottoms}")
        six_line = [card["bottom"] - card["top"]
                    for column in columns for card in column
                    if card["lines"] == 6]
        assert len(six_line) >= 15, "every course card has six lines"
        assert max(six_line) - min(six_line) <= 20, (
            "a six-line card may be one line taller than another (its row"
            " holds a wrapped name or the seven-line Secret card), never a"
            f" column's average: {six_line}")


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


def test_a_full_monitor_keeps_four_columns_and_centres_bowser():
    """Round 32, rendered at a 2860px viewport: the five-track shape is
    retired ("we actually should just keep the same layout as the medium
    width... that looks much nicer") -- four tracks, four cards each, and
    the Bowser card on a centred row beneath, its centre on the grid's
    midline and its width a column's. The chunked stacks and the drawn
    tracks must be the same number."""
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
                "    tracks: getComputedStyle(cards).gridTemplateColumns"
                "      .split(' ').length,"
                "    counts: cols.map((col) =>"
                " col.querySelectorAll('.score-card').length),"
                "    last: cols[cols.length - 1].querySelector("
                "'.score-card:last-child .score-card-name').textContent.trim(),"
                "    centred: (() => {"
                "      const row = document.querySelector('.rank-page .score-centred');"
                "      if (!row) return null;"
                "      const card = row.querySelector('.score-card');"
                "      const grid = cards.getBoundingClientRect();"
                "      const box = card.getBoundingClientRect();"
                "      const column = cols[0].querySelector('.score-card').getBoundingClientRect();"
                "      const stack = [...cols[0].querySelectorAll('.score-card')]"
                "        .map((c) => c.getBoundingClientRect());"
                "      return {label: card.querySelector('.score-card-name').textContent.trim(),"
                "              offCentre: Math.abs((box.left + box.right) / 2 - (grid.left + grid.right) / 2),"
                "              widthGap: Math.abs(box.width - column.width),"
                "              below: box.top >= grid.bottom,"
                "              gapBelowGrid: box.top - grid.bottom,"
                "              gapInStack: stack[1].top - stack[0].bottom};"
                "    })(),"
                "  };"
                "})()")

        assert state["width"] >= 1900, (
            f"the pane itself is only {state['width']}px wide — the "
            "workspace cap is back")
        assert state["dataCols"] == "4", state
        assert state["tracks"] == 4, (
            f"the grid drew {state['tracks']} tracks for 4 chunked stacks "
            "— the CSS and the component disagree")
        assert state["counts"] == [4, 4, 4, 4], state
        assert state["last"] == SECRET_LABEL, "Secret closes the last column"
        assert state["centred"] and state["centred"]["label"] == BOWSER_LABEL, state
        assert state["centred"]["offCentre"] <= 1.5, state["centred"]
        assert state["centred"]["widthGap"] <= 1.5, state["centred"]
        assert state["centred"]["below"], state["centred"]
        # The row sits one grid gap below the stacks -- the SAME gap the
        # cards in a stack keep between themselves (his report: the Bowser
        # gap read as twice the others).
        assert abs(state["centred"]["gapBelowGrid"] - state["centred"]["gapInStack"]) <= 1, state["centred"]


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


_HOLD_GOAL_RESPONSES = """(() => {
  const original = window.fetch.bind(window);
  window.goalWrites = [];
  window.goalReplies = 0;
  window.fetch = async (input, init) => {
    const path = new URL(input, location.href).pathname;
    if (path === '/api/scorecard/goal') {
      window.goalWrites.push(JSON.parse(init.body));
      if (window.goalWrites.length === 1) {
        const response = FAIL_FIRST
          ? new Response(JSON.stringify({detail: 'Goal save failed'}), {status: 503})
          : await original(input, init);
        await new Promise(resolve => { window.releaseGoal = resolve; });
        window.goalReplies++;
        return response;
      }
    }
    const response = await original(input, init);
    if (path === '/api/scorecard/goal') window.goalReplies++;
    if (path === '/api/scorecard' && !window.releaseOldCard) {
      await new Promise(resolve => { window.releaseOldCard = resolve; });
      window.oldCardReleased = true;
    }
    return response;
  };
})()"""

_PICKED_GOALS = ("[...document.querySelectorAll('.rank-page .scorecard-card "
                 ".search-menu-option.is-picked')].map(el => el.dataset.value)")


@pytest.mark.parametrize("fail_first", [False, True])
@pytest.mark.parametrize("width", [1500, 850])
def test_the_picker_keeps_players_and_replaces_the_division(fail_first, width, tmp_path):
    """Cross-control edits stay ordered while a write and stale read are held."""
    from test_ui_scorecard_auto_goal import _check_browser_errors, _open_menu, _pick_rank, _label_is, CARD
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(width, 1000)) as page:
            page.goto(base)
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(CARD + " .search-select-trigger")
            page.evaluate(_HOLD_GOAL_RESPONSES.replace("FAIL_FIRST", json.dumps(fail_first)))
            urllib.request.urlopen(urllib.request.Request(
                f"{base}/api/ranks/mode", data=b'{"mode":"pb"}', method="PUT",
                headers={"Content-Type": "application/json"}), timeout=10).read()
            _wait_until(page, "!!window.releaseOldCard")
            _pick_rank(page, "division:Bronze:V")
            _open_menu(page, "Player Goal")
            page.wait_for('.search-menu-option[data-value="runner:Raisn"]')
            for runner in ("RONC3NA", "Raisn"):
                page.evaluate(_option_click("runner:" + runner))
                page.wait_ms(100)
            assert sorted(page.evaluate(_PICKED_GOALS)) == ["runner:RONC3NA", "runner:Raisn"]
            page.click(CARD + ' [data-goal-control="Player Goal"] .search-select-trigger')
            _pick_rank(page, "division:Silver:III")
            _pick_rank(page, "division:Iron:IV")
            _label_is(page, "Capless 4")
            assert page.count(CARD + " .goal-pill") == 3
            assert page.count(CARD + " .goal-pill:first-child button") == 0
            assert len(page.evaluate("window.goalWrites")) == 1
            _wait_until(page, "!!window.releaseGoal")
            page.evaluate("window.releaseGoal()")
            _wait_until(page, "window.goalReplies === 5")
            page.evaluate("window.releaseOldCard()")
            _wait_until(page, "window.oldCardReleased")
            _label_is(page, "Capless 4")
            assert page.count(CARD + " .goal-pill") == 3
            assert page.count(CARD + " .inline-state.error") == 0
            _check_browser_errors(page, base)
            page.evaluate("document.querySelector('.scorecard-card').scrollIntoView()")
            (tmp_path / f"one-division-{width}.png").write_bytes(page.screenshot())
        assert _get_scorecard(base)["goal"] == {"kind": "multi", "sources": [
            {"kind": "division", "tier": "Iron", "division": "IV"},
            {"kind": "runner", "runner": "RONC3NA"},
            {"kind": "runner", "runner": "Raisn"}]}


def test_a_failed_goal_pick_reconciles_and_leaves_the_picker_ready_to_retry():
    from test_ui_scorecard_auto_goal import _check_browser_errors, _label_is, _pick_rank

    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .search-select-trigger")
            page.evaluate("document.querySelector('.rank-page .scorecard-card "
                          ".search-select-trigger').click()")
            page.wait_for('.search-menu-option[data-value="division:Bronze:V"]')
            page.evaluate(_HOLD_GOAL_RESPONSES.replace("FAIL_FIRST", "true"))
            # This test holds only the rejected write, not its recovery read.
            page.evaluate("window.releaseOldCard = () => {}")
            page.evaluate(_option_click("division:Bronze:V"))
            _label_is(page, "Toad 5")
            _wait_until(page, "!!window.releaseGoal")
            page.evaluate("window.releaseGoal()")
            page.wait_for(".rank-page .scorecard-card .inline-state.error")
            _wait_until(page, "document.querySelector('.scorecard-card .search-select-value').textContent.startsWith('Automatic')")
            assert _get_scorecard(base)["goal"]["kind"] == "automatic"
            _pick_rank(page, "division:Silver:III")
            _wait_until(page, "window.goalReplies === 2")
            _wait_until(page, "!document.querySelector('.rank-page .scorecard-card .inline-state.error')")
            _check_browser_errors(page, base)
        assert _get_scorecard(base)["goal"] == {
            "kind": "division", "tier": "Silver", "division": "III"}


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
            page.wait_for(".rank-page .scorecard-card [data-goal-control=\"Custom Goals\"] .search-select-trigger")
            page.evaluate(
                "document.querySelector('.rank-page .scorecard-card "
                "[data-goal-control=\"Custom Goals\"] .search-select-trigger').click()")
            page.wait_for(".rank-page .scorecard-card "
                          '.search-menu-option[data-value="custom:alpha"]')
            page.evaluate(_option_click("custom:alpha"))   # beta is already on
            # The picker updates locally; the legend and attribution arrive
            # with the computed card after the goal write has completed.
            page.wait_for(".rank-page .scorecard-card .goal-pill:nth-child(3)")
            page.wait_for(".rank-page .scorecard-card .score-line-source")

            state = page.evaluate(
                "(() => {"
                "  const pills = Array.from(document.querySelectorAll("
                "    '.rank-page .scorecard-card .goal-pill'));"
                "  const legend = pills.map((pill) => ({"
                "    label: pill.querySelector('.goal-pill-label').textContent.trim(),"
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
        assert len(legend) == 3, f"the legend must name every pick: {legend}"
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
        assert {"alpha", "beta"} <= {dot["title"] for dot in dots}, (
            f"only one source owns anything: {set(d['colour'] for d in dots)}")


def test_a_pills_cross_removes_that_pick_and_regrades_the_card():
    """Round 23: "when i hover over each name / pill in the scorecard, there
    should be an X on the right side that appears. I should be able to
    click this to remove that specific player / rank standard from my
    scorecard immediately. Everything should update accordingly." Two
    divisions picked; the × is visible at rest (his follow-up: "Pills look
    weird if the X is hidden by default. Let's just show it at all times.
    Red X.") and still visible under the pointer;
    clicking the SECOND pill's × (Gold I -- the faster offer, which a multi
    goal takes per tile) leaves Bronze V as a SINGLE goal (no legend), and
    every goal time on the card moves to Bronze V's slower cutoff."""
    with serve_ui() as base:
        _put_custom_goal(base, "Fast set", {"star:1:0": 1000})
        body = json.dumps({"kind": "multi", "sources": [
            {"kind": "division", "tier": "Bronze", "division": "V"},
            {"kind": "custom", "name": "Fast set"}]}).encode()
        urllib.request.urlopen(urllib.request.Request(
            f"{base}/api/scorecard/goal", data=body, method="PUT",
            headers={"Content-Type": "application/json"})).read()
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .goal-pill")
            page.wait_ms(200)
            first_goal = "document.querySelector('.rank-page .scorecard-card "
            first_goal += ".score-line .score-line-goal').textContent.trim()"
            before = page.evaluate(first_goal)
            at_rest = page.evaluate(
                "getComputedStyle(document.querySelector('.rank-page "
                ".scorecard-card .goal-pill:nth-child(2) .goal-pill-remove')).visibility")
            page.hover(".rank-page .scorecard-card .goal-legend .goal-pill:nth-child(2)")
            under_pointer = page.evaluate(
                "getComputedStyle(document.querySelector('.rank-page "
                ".scorecard-card .goal-pill:nth-child(2) .goal-pill-remove')).visibility")
            page.click(".rank-page .scorecard-card .goal-legend "
                       ".goal-pill:nth-child(2) .goal-pill-remove")
            page.wait_ms(600)
            pills_after = page.count(".rank-page .scorecard-card .goal-pill")
            after = page.evaluate(first_goal)
        goal = json.loads(urllib.request.urlopen(
            f"{base}/api/scorecard", timeout=10).read())["goal"]
    assert at_rest == "visible", at_rest
    assert under_pointer == "visible", under_pointer
    assert goal == {"kind": "division", "tier": "Bronze", "division": "V"}, goal
    assert pills_after == 0, "one pick left is a single goal, and a single goal draws no legend"
    assert before != after, (
        f"the card must re-grade against the remaining pick: {before!r} -> {after!r}")


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


def test_unpicking_the_last_goal_restores_automatic_and_one_pick_stays_single():
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
                          '.search-menu-option[data-value=""]')
            page.evaluate(_option_click(""))   # select Automatic
            page.wait_ms(300)

        assert _get_scorecard(base)["goal"]["kind"] == "automatic"


def test_every_name_sits_on_one_line_at_every_column_count():
    """Round 32, his rule: "Every single name should be on a SINGLE row,
    rather than occupying two rows." The column count steps down before a
    name would wrap (`CARD_COLUMN_FLOORS`, measured on this template), so
    the real card grid -- every one of the 120 stars and the Bowser rows --
    is driven at a pane just above each floor and at the widest window, and
    every name must occupy one line. This replaces round 13's blank-line
    guard, whose premise (a wrapped name) no longer exists at any width the
    grid offers.

    The widths are DERIVED from `CARD_COLUMN_FLOORS` at test time -- each
    floor plus one pane pixel, plus the ~283px of sidebar and padding a
    viewport adds -- so lowering a floor moves the probe with it and the
    guard goes red where a name would wrap (a hard-coded list would keep
    passing at the old widths). Plus a single-column width and the widest
    window."""
    floors = _column_floors()                       # [[1700, 4], [850, 2]]
    probes = [(2860, "4")] + [(_viewport_for_pane(floor + 1), str(count))
                              for floor, count in floors]
    probes.append((_viewport_for_pane(min(floor for floor, _count in floors) - 50), "1"))
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")
        with get_driver().launch(headless=True, viewport=(2860, 1200)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card .score-line")
            page.wait_ms(300)
            findings = {}
            for width, expected_cols in probes:
                page.set_viewport(width, 1200)
                page.wait_ms(250)
                findings[width] = page.evaluate("""
                  (() => {
                    const cards = document.querySelector('.rank-page .score-cards');
                    const names = [...document.querySelectorAll('.rank-page .score-line-name')];
                    const lineHeight = parseFloat(getComputedStyle(names[0]).lineHeight);
                    const wrapped = names.filter((el) =>
                      el.getBoundingClientRect().height > lineHeight * 1.5)
                      .map((el) => el.textContent.trim());
                    return {cols: cards.dataset.cols, pane: cards.getBoundingClientRect().width,
                            names: names.length, wrapped};
                  })()
                """)
                assert findings[width]["cols"] == expected_cols, (width, findings[width])
                if width != 2860:
                    wanted = next(pane for pane in (floor + 1 for floor, _c in floors)
                                  if _viewport_for_pane(pane) == width) if expected_cols != "1" else None
                    if wanted is not None:
                        assert abs(findings[width]["pane"] - wanted) <= 4, (
                            f"a {width}px window gave a {findings[width]['pane']}px pane, "
                            f"expected {wanted} -- the shell's offset moved")
    for width, found in findings.items():
        # 15 courses x 6 lines (a 100-coin star shares its companion's line)
        # + Secret's 7 + Bowser's 3.
        assert found["names"] >= 100, (width, found)
        assert found["wrapped"] == [], (
            f"at a {width}px window ({found['pane']:.0f}px pane, {found['cols']} columns) "
            f"these names wrap: {found['wrapped']}")


COURSE_ORDER = ["BOB", "WF", "JRB", "CCM", "BBH", "HMC", "LLL", "SSL",
                "DDD", "SL", "WDW", "TTM", "THI", "TTC", "RR", "Specials"]


def test_the_grid_reads_down_each_column_in_course_order():
    """His grid, verbatim: "We should fill columns top to bottom, then left
    to right... [BOB] [BBH] [DDD] [THI] / [WF] [HMC] [SL] [TTC] / [JRB] [LLL]
    [WDW] [RR] / [CCM] [SSL] [TTM] [Secrets + Bowser combined into a single
    card]". Sixteen cards, four columns, COLUMN-MAJOR."""
    rows = [{"label": name, "course_id": index + 1}
            for index, name in enumerate(COURSE_ORDER[:15])]
    rows += [{"label": "Secret", "course_id": None},
             {"label": "Bowser", "course_id": None}]
    layout = call("cardLayout", rows, 4)
    drawn = [[card["label"] for card in column["rows"]] for column in layout["columns"]]
    # Round 21: two specials cards again. Round 32: Secret closes the last
    # column and Bowser -- the odd card -- takes a centred row of its own
    # ("centered between all 4 columns (centered under SSL + TTM)").
    assert drawn == [["BOB", "WF", "JRB", "CCM"],
                     ["BBH", "HMC", "LLL", "SSL"],
                     ["DDD", "SL", "WDW", "TTM"],
                     ["THI", "TTC", "RR", "Secret"]]
    assert [card["label"] for card in layout["centred"]] == ["Bowser"]


def test_bowser_rides_a_centred_row_at_two_columns_and_the_stack_at_one():
    """Round 32, his narrow case: "What if the Bowser card is centered
    between the two columns here when the page is narrower?" At two columns
    the courses chunk 8 / 7, Secret closes the second, Bowser is centred. A
    single column is one stack and keeps everything in it; a scope with one
    special has no odd card and keeps it in the stack."""
    rows = [{"label": name, "course_id": index + 1}
            for index, name in enumerate(COURSE_ORDER[:15])]
    rows += [{"label": "Secret", "course_id": None},
             {"label": "Bowser", "course_id": None}]
    two = call("cardLayout", rows, 2)
    assert [len(column["rows"]) for column in two["columns"]] == [8, 8]
    assert two["columns"][1]["rows"][-1]["label"] == "Secret"
    assert [card["label"] for card in two["centred"]] == ["Bowser"]
    one = call("cardLayout", rows, 1)
    assert [len(column["rows"]) for column in one["columns"]] == [17]
    assert one["columns"][0]["rows"][-1]["label"] == "Bowser" and one["centred"] == []
    lone = call("cardLayout", rows[:16], 4)
    assert lone["columns"][-1]["rows"][-1]["label"] == "Secret" and lone["centred"] == []


def test_a_scope_with_fewer_cards_still_reads_down_in_order():
    """A route scope holds fewer cards; the last column simply holds fewer,
    and reading down still walks the list in order."""
    rows = [{"label": name, "course_id": index + 1}
            for index, name in enumerate(["BOB", "WF", "JRB", "CCM", "BBH"])]
    columns = call("cardLayout", rows, 4)["columns"]
    assert [card["label"] for column in columns for card in column["rows"]] == [
        "BOB", "WF", "JRB", "CCM", "BBH"]
    assert [len(column["rows"]) for column in columns] == [2, 2, 1]


def test_no_cards_draws_no_columns():
    assert call("cardLayout", [], 4) == {"columns": [], "centred": []}


def test_the_track_count_follows_the_measured_pane():
    """The COMPONENT picks the track count, not a container query: the
    chunker and the drawn grid have to be the same number or reading down a
    column stops being course order. Floors are round 11's."""
    # Round 32's floors: the count steps down before a name would wrap
    # (a card needs 417px; measured on the real template), and four is the
    # ceiling -- the five-track shape is retired.
    assert call("columnCountFor", 2860) == 4
    assert call("columnCountFor", 1701) == 4
    assert call("columnCountFor", 1700) == 2
    assert call("columnCountFor", 851) == 2
    assert call("columnCountFor", 850) == 1
    assert call("columnCountFor", 0) == 1


def test_the_css_declares_a_track_rule_for_every_count_the_component_picks():
    """A count the component picks with no matching `data-cols` rule draws
    the default four tracks over two chunked stacks -- silently, and only at
    that one width."""
    css = (UI / "index.html").read_text(encoding="utf-8")
    counts = {call("columnCountFor", width)
              for width in (0, 850, 851, 1700, 1701, 2860)}
    for count in counts:
        if count == 4:
            continue                      # the bare `.score-cards` default
        assert f'.score-cards[data-cols="{count}"]' in css, (
            f"columnCountFor can pick {count} columns and the CSS has no "
            f"rule for it")


def test_a_completed_attempt_does_not_blank_the_card_he_is_reading():
    """Round 20, his report: "the page... is randomly refreshing? It keeps
    refreshing without me doing anything, and I fear we have made a mistake
    somewhere that accidentally prompts this type of autorefreshing."

    Measured on his own live server: while he played, an `attempt_completed`
    landed on the event socket every minute or so, and that event is in
    store.js's REFRESH_ON set. Every Rank-tab fetch answered the resulting
    `mareloRev` bump by CLEARING its state first, so the card dropped to
    "Loading your scorecard..." with no gesture of his anywhere on the page.

    Driven through the REAL service, because the whole claim is about what a
    server-PUSHED event does to a mounted page -- a fresh load can never
    reach this state. The card must keep its rows on screen ACROSS the
    refetch: sampled EVERY FRAME while the event travels, the count never
    drops to zero. Per-frame matters — the blank this catches is two frames
    long on localhost, and a 60ms timer stepped straight over it.
    """
    import asyncio
    import threading
    from datetime import datetime, timezone

    from sm64_events.core.events import Event
    from ui_fixture import serve_ui_live

    with serve_ui_live() as (base, service):
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(300)

            # Sample the card's own row count on a timer, so a blank that
            # lasts one fetch is caught rather than missed between two reads.
            page.evaluate(
                "(() => {"
                "  window.__samples = [];"
                "  window.__fetches = 0;"
                "  const real = window.fetch;"
                "  window.fetch = function (...args) {"
                "    if (String(args[0]).includes('/api/scorecard'))"
                "      window.__fetches += 1;"
                "    return real.apply(this, args);"
                "  };"
                "  const tick = () => {"
                "    window.__samples.push(document.querySelectorAll("
                "      '.rank-page .scorecard-card .score-card').length);"
                "    window.__raf = requestAnimationFrame(tick);"
                "  };"
                "  tick();"
                "})()")

            def publish():
                async def go():
                    await service.publish(Event(
                        type="attempt_completed", frame=99000,
                        timestamp_utc=datetime(2026, 9, 1, 5, 19,
                                               tzinfo=timezone.utc),
                        payload={"course_id": 1, "star_id": 0,
                                 "outcome": "success"}))
                worker = threading.Thread(target=lambda: asyncio.run(go()))
                worker.start()
                worker.join(timeout=10)

            publish()
            page.wait_ms(1500)
            measured = page.evaluate(
                "(() => { cancelAnimationFrame(window.__raf);"
                " return {samples: window.__samples,"
                "         fetches: window.__fetches}; })()")
            samples = measured["samples"]
            refetches = measured["fetches"]

    assert samples, "the sampler never ran"
    assert samples[0] > 0, "the card was not drawn before the event"
    # Without this the guard is vacuous: a card that never refetched cannot
    # blank, so a page that ignored the event entirely would pass it.
    assert refetches >= 1, (
        "the published attempt never reached the card -- it did not refetch, "
        "so this run proves nothing about blanking")
    blanks = [index for index, count in enumerate(samples) if count == 0]
    assert not blanks, (
        "the card blanked while a completed attempt refetched it -- that is "
        f"the flicker he reported; row counts were {samples}")


def test_the_scorecard_sits_between_the_scope_rank_card_and_progress():
    """Round 24: "move the scorecard to be directly below the scope rank
    card, above the Progress card." Read off the Rank page's own children,
    in DOM order: the scope rank card, then the scorecard, then the Progress
    card -- and nothing between the first two."""
    with serve_ui() as base:
        _put_division_goal(base, "Bronze", "V")
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .score-card")
            page.wait_ms(300)
            order = page.evaluate(
                "Array.from(document.querySelector('.rank-page').children)"
                ".map((el) => el.classList.contains('rank-card') ? 'scope'"
                "  : el.classList.contains('scorecard-card') ? 'scorecard'"
                "  : (el.querySelector(':scope > h3') || {}).textContent === 'Progress' ? 'progress'"
                "  : 'other')")
    assert "scope" in order and "scorecard" in order and "progress" in order, order
    assert order.index("scorecard") == order.index("scope") + 1, (
        f"the scorecard must sit directly below the scope rank card: {order}")
    assert order.index("progress") > order.index("scorecard"), (
        f"Progress must come after the scorecard: {order}")


def test_the_pasted_html_paints_each_timed_cell_by_the_machine_that_set_it(monkeypatch):
    """ROUND 29 item 2, his loop with colour: "people like to color the cells
    either an EMU color or an N64 color... exporting exports the fastest
    time across both your emu / console times, and colors the cell
    accordingly." Griff's column in the stub workbook carries Raisn's legend
    and an N64 11.00 on the fixture's star; importing it through the real
    sheet door stamps that time n64, and the copied HTML's cell for that
    row wears the N64 fill, the chosen text colour and font, while every
    empty cell carries no style at all -- as Raisn's empty cells do. The
    style is the one he picked in Settings, read at copy time."""
    from sm64_events.library.ladders import fit_payload
    from sm64_events.library.store import LibraryStore, build_and_stamp

    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", _stub_workbook)

    # The sheet door's refresh, replaced in the SERVER (the fixture runs
    # in-process): the store takes the stub workbook's payload without the
    # newer-than-what-we-have check (the stub's Log is older than the bundled
    # snapshot) and without writing a snapshot anywhere.
    def stub_refresh(self, fetch_fn, overrides=None, step=None):
        self._payload = fit_payload(build_and_stamp(_stub_workbook(), overrides))
        return {"applied": True}

    monkeypatch.setattr(LibraryStore, "refresh", stub_refresh)
    with serve_ui() as base:
        style = {"emu_fill": "#4F7BE0", "n64_fill": "#AB3F14",
                 "font_color": "#FFFFFF", "font_family": "Roboto Mono"}
        request = urllib.request.Request(
            f"{base}/api/scorecard/sheet_style", data=json.dumps(style).encode(),
            method="PUT", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
        request = urllib.request.Request(
            f"{base}/api/import/sheet", method="POST",
            data=json.dumps({"runner": "Griff", "refresh": True}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            landed = json.loads(response.read())
        assert landed["imported"] == 1, landed
        column = json.loads(urllib.request.urlopen(
            f"{base}/api/scorecard/column", timeout=10).read())
        assert [cell["platform"] for cell in column["cells"]
                if cell["text"] and not cell.get("legend")] == ["n64"], column

        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-copy-column")
            assert page.evaluate(_INSTALL_CLIPBOARD_SHIM) is True
            page.evaluate("document.querySelector('.scorecard-copy-column').click()")
            _wait_until(page, "window.__scorecardHtml.length > 0")
            html_copied = page.evaluate("window.__scorecardHtml")[0]

    import re
    cells = re.findall(r"<td([^>]*)>([^<]*)</td>", html_copied)
    assert len(cells) == column["total_rows"]
    painted = [(re.search(r' style="[^"]*"', attrs).group(), text)
               for attrs, text in cells if text]
    # Round 30 item 7: the pasted column opens with the legend in its own
    # fills -- here only the EMU cell, because this stub's row 3 holds a
    # data row the N64 cell must never print over -- then the one timed
    # cell, in the N64 fill.
    assert painted == [
        (' style="background-color:#4F7BE0;color:#FFFFFF;font-family:Roboto Mono"', "EMU"),
        (' style="background-color:#AB3F14;color:#FFFFFF;font-family:Roboto Mono"', "11.00"),
    ], painted
    assert all(attrs == "" for attrs, text in cells if not text), cells


def test_the_region_switch_shares_the_pills_row_left_of_them():
    """Round 31, his words: "the JP/US buttons (and the accompanying text)
    should go underneath the scorecard on the same row as the player pills,
    aligned to the left edge, but in the same row... the top right feels
    overwhelming right now, but if it was split up, it would feel balanced."
    With a multi goal the pills exist: the switch sits left of them on one
    row under the head, and the head holds the picker alone."""
    with serve_ui() as base:
        _put_multi_goal(base, [{"kind": "division", "tier": "Bronze", "division": "V"},
                               {"kind": "runner", "runner": _any_sheet_runner()}])
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-card .goal-legend .goal-pill")
            page.wait_ms(200)
            layout = page.evaluate("""
              (() => {
                const card = document.querySelector('.rank-page .scorecard-card');
                const head = card.querySelector('.scorecard-head').getBoundingClientRect();
                const row = card.querySelector('.scorecard-subhead');
                const rowBox = row.getBoundingClientRect();
                const sw = row.querySelector('.version-switch').getBoundingClientRect();
                const pills = row.querySelector('.goal-legend').getBoundingClientRect();
                return {
                  switchInHead: !!card.querySelector('.scorecard-head .version-switch'),
                  belowHead: sw.top >= head.bottom,
                  leftEdge: Math.abs(sw.left - rowBox.left) < 2,
                  sameRow: Math.abs((sw.top + sw.bottom) / 2 - (pills.top + pills.bottom) / 2) < 12,
                  leftOfPills: sw.right <= pills.left,
                  noteText: (row.querySelector('.version-switch-note') || {}).textContent || '',
                };
              })()
            """)
    assert layout["switchInHead"] is False, "the region switch is still in the head"
    assert layout["belowHead"] and layout["leftEdge"], layout
    assert layout["sameRow"] and layout["leftOfPills"], layout
    assert "regions" in layout["noteText"].lower() or "only" in layout["noteText"].lower(), layout


def test_the_progress_track_starts_on_the_copy_buttons_left_edge(monkeypatch):
    """Round 31: "the left side of this progress bar should be aligned with
    the left side of the button above it." Measured as boxes -- the track's
    left equals the button's left -- and drawn as a hard edge (no radius on
    the track's left end) so the start reads where the box says it is."""
    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", _stub_workbook)
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            page.evaluate(_OPEN_RANK_TAB)
            page.wait_for(".rank-page .scorecard-copy-column")
            assert page.evaluate(_INSTALL_CLIPBOARD_SHIM) is True
            page.evaluate("document.hasFocus = () => false")
            page.evaluate("document.querySelector('.scorecard-copy-column').click()")
            _wait_until(page, "document.querySelector('.scorecard-exports')"
                              ".dataset.held === 'true'")
            page.wait_ms(200)
            edges = page.evaluate("""
              (() => {
                const button = document.querySelector('.scorecard-copy-column').getBoundingClientRect();
                const track = document.querySelector('.scorecard-status .job-status-track');
                const css = getComputedStyle(track);
                return {button: button.left, track: track.getBoundingClientRect().left,
                        leftRadius: [css.borderTopLeftRadius, css.borderBottomLeftRadius]};
              })()
            """)
    assert abs(edges["button"] - edges["track"]) < 0.5, edges
    assert edges["leftRadius"] == ["0px", "0px"], edges
