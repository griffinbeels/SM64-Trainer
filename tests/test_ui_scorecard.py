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
