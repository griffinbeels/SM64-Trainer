"""A 100-coin star's strategies REACH the screen grouped by exit star.

Spec 2026-08-03-hundred-coin-exit-variants. This is a render test because the
project's own rule says so — unit tests plus `node --check` once shipped an
invisible feature — and because the two things it checks are structural facts
about the DOM the browser actually builds: `<optgroup>`s inside the strategy
dropdown, and a `colspan`'d band row over the standards table's columns.

It also closes the gap the whole feature turns on. The dropdown for CCM/WDW/
THI/RR shows a bare "Standard" TWICE — once per exit-star variant — and the
only thing that tells them apart on screen is the heading above each. A test
that asserted "both names are present" would pass on a flat list where the two
are indistinguishable, which is the bug, not the fix.
"""
import contextlib
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

# CCM's 100-coin star: two exit-star variants, and BOTH define "Standard" and
# "Open". The one course where a flat list is provably ambiguous.
CCM = 4
CCM_LEVEL = 5
HUNDRED_COIN = 6


@contextlib.contextmanager
def _page(url: str):
    with driver.get_driver().launch(headless=True) as page:
        page.goto(url)
        yield page


@pytest.fixture(scope="module")
def hundred_coin_server():
    # The stage must BE Cool, Cool Mountain: you may only practice what you
    # are standing in front of (tracking/practicable.py), so a fixture left on
    # the default WF stage has its POST /api/target refused with a 409.
    with serve_ui(stage=(CCM, CCM_LEVEL), target=(CCM, HUNDRED_COIN)) as base:
        yield base


@pytest.fixture
def hundred_coin_page(hundred_coin_server):
    """A FRESH page per test. Sharing one across the module made whichever
    test ran first decide what the others measured — the modal test leaves a
    dialog open, the table test scrolls and expands a panel — and pytest
    randomises order inside a module, so it failed about one run in three.
    Booting the server is the expensive half and stays module-scoped."""
    with _page(f"{hundred_coin_server}/ui/index.html") as page:
        page.wait_for(".objective-card", timeout_ms=20000)
        yield page


def test_the_strategy_dropdown_groups_by_exit_star(hundred_coin_page):
    groups = hundred_coin_page.evaluate("""
      Array.from(document.querySelectorAll(".objective-strategy optgroup"))
        .map((g) => [g.label,
                     Array.from(g.querySelectorAll("option")).map((o) => o.textContent.trim())])
    """)
    assert groups, "the 100-coin card's strategy dropdown has no optgroups"
    labels = [label for label, _ in groups]
    assert labels == sorted(set(labels), key=labels.index), "duplicate headings"
    assert len(labels) >= 2, f"CCM has two exit-star variants; got {labels}"
    # The leaf, not the stored name — the heading already carries the variant.
    for label, leaves in groups:
        assert leaves, f"{label} has no strategies under it"
        for leaf in leaves:
            assert label not in leaf, (
                f"{leaf!r} repeats its own heading {label!r}; the dropdown "
                "should show the leaf")
    # The ambiguity this whole design exists for: the SAME leaf under two
    # different headings, which a flat list could not tell apart.
    shared = set(groups[0][1]) & set(groups[1][1])
    assert shared, ("expected a strategy name shared by two variants (CCM's "
                    "'Standard'/'Open'); without one this test proves nothing")


def test_the_selected_value_is_the_full_qualified_name(hundred_coin_page):
    """The heading is presentation; the VALUE stays the stored identity, or
    the write would post a name no ladder is filed under."""
    values = hundred_coin_page.evaluate("""
      Array.from(document.querySelectorAll(".objective-strategy optgroup option"))
        .map((o) => o.value)
    """)
    assert values and all(" · " in value for value in values), values


def test_the_standards_table_bands_its_columns_by_variant(hundred_coin_page):
    hundred_coin_page.evaluate("""
      (() => { const b = document.querySelector(".standards-toggle");
               if (b && b.getAttribute("aria-expanded") !== "true") b.click(); })()
    """)
    hundred_coin_page.wait_for(".stdtable", timeout_ms=10000)
    bands = hundred_coin_page.evaluate("""
      Array.from(document.querySelectorAll(".stdtable .std-variant"))
        .map((th) => [th.textContent.trim(), Number(th.getAttribute("colspan"))])
    """)
    assert len(bands) >= 2, f"expected a band per exit-star variant; got {bands}"
    columns = hundred_coin_page.evaluate(
        'document.querySelectorAll(".stdtable thead tr:last-child th").length')
    # The band row spans the strategy columns exactly — one short and every
    # column after it sits under the wrong heading, silently.
    assert sum(span for _, span in bands) == columns - 1, (bands, columns)


def test_the_new_strategy_modal_asks_which_star_the_run_ends_on(hundred_coin_page):
    """Live question, 2026-08-03: "How do people add a new exit star that
    doesn't already have a strat?" — asked while running this very build, so
    the control existed and was not FINDABLE. This renders the modal and reads
    its fields back in DOM ORDER, because the answer he gave is an order:
    "add strategy -> choose exit star --> add strategy name / rank standards".
    """
    hundred_coin_page.evaluate("""
      (() => {
        const select = document.querySelector(".objective-strategy select");
        select.value = "__new";
        select.dispatchEvent(new Event("change", {bubbles: true}));
      })()
    """)
    hundred_coin_page.wait_for(".modal-body", timeout_ms=10000)
    fields = hundred_coin_page.evaluate("""
      Array.from(document.querySelectorAll(".modal-body .field-label"))
        .map((el) => el.textContent.trim())
    """)
    assert fields, "the New strategy modal rendered no labelled fields"
    assert "Ends on" in fields, (
        f"no exit-star control on a 100-coin star; fields were {fields}")
    assert fields.index("Ends on") < fields.index("Strategy name"), (
        f"the exit star must be asked FIRST — you pick the route, then name "
        f"the approach to it. Order was {fields}")
    options = hundred_coin_page.evaluate("""
      Array.from(document.querySelectorAll(".modal-body .exit-star-field option"))
        .map((o) => o.textContent.trim())
    """)
    # All six endings, not just the rated ones — defining a variant for a star
    # the community does not time is the whole point of the control.
    assert len([o for o in options if o]) >= 6, options
    assert any("no community times" in o for o in options), options


def test_the_standards_cells_print_the_usamune_notation(hundred_coin_page):
    """Rendered, not just formatted: the cells read `1'21"32` / `23"00`, never
    raw seconds (user, 2026-08-03). Lives here because this fixture is the one
    that reaches a populated standards table; the rule is not 100-coin-specific
    and `tests/test_ui_time_format.py` owns the notation itself."""
    import re

    hundred_coin_page.evaluate("""
      (() => { const b = document.querySelector(".standards-toggle");
               if (b && b.getAttribute("aria-expanded") !== "true") b.click(); })()
    """)
    hundred_coin_page.wait_for(".stdtable", timeout_ms=10000)
    cells = hundred_coin_page.evaluate("""
      Array.from(document.querySelectorAll(".stdtable tbody td"))
        .map((td) => td.textContent.trim()).filter((t) => t && t !== "\u2014")
    """)
    times = [c for c in cells if re.fullmatch(r"[\d'\"]+", c)]
    assert times, f"no time cells found; got {cells[:8]}"
    for text in times:
        assert re.fullmatch(r"(\d+')?\d{2}\"\d{2}", text), (
            f"{text!r} is not the Usamune notation")
