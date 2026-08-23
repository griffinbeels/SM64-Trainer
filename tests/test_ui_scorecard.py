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
