# tests/test_ui_standards_subdivisions.py
"""The rank-standards table's tier rows expand into subdivision rows.

Task 0098: (2) the tier label wears the division-I cap — numbered, fully
winged, the Library TOC's own round-2 ruling applied here; (3) a tier expands
into five subdivision threshold rows, each hyperlinked to the fastest example
within that subdivision's band.

Driven in a real browser because everything under test is a render: the cap's
wings are sprite layers, the rows mount on expand and unmount after the close
animation, and the links depend on the payload's clips banding through
librarymodel.js::bandsOf in the page itself.

The linked-cell expectation is DERIVED from the payload rather than
hand-picked: if `cutoff_videos[strat][tier]` names a link, at least one clip
banded into that tier, and the divisions partition the band — so expanding
that tier must show at least one linked subdivision cell in that strategy's
column. That derivation is what keeps this test honest on any fixture data.
"""
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import serve_ui  # noqa: E402

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402

SETTLE = "new Promise(r => setTimeout(r, 2500))"
BEAT = "new Promise(r => setTimeout(r, 900))"

OPEN_PANEL = """
  (() => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.standards-toggle'));
    if (!card) return null;
    card.querySelector('.standards-toggle').click();
    return true;
  })()
"""

# Which (strat, tier) the payload itself promises an example for — the header
# order maps a strategy name to its column index in the rendered table.
FIND_LINKED = """
  (async () => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.stdtable'));
    if (!card) return {error: 'no open standards table'};
    const heads = Array.from(card.querySelectorAll(
      '.stdtable thead tr:last-child th')).map((th) => th.textContent.trim());
    const entity = card.getAttribute('data-feed-key');
    const res = await fetch('/api/ranks/standards?entity='
      + encodeURIComponent(entity));
    const data = await res.json();
    for (const [strat, byTier] of Object.entries(data.cutoff_videos || {})) {
      for (const tier of Object.keys(byTier)) {
        const col = heads.findIndex((h) => h.startsWith(strat));
        if (col > 0) return {strat, tier, col, entity};
      }
    }
    return {error: 'no linked (strat, tier) in the payload', entity,
            heads, cutoff: data.cutoff_videos};
  })()
"""

EXPAND = """
  ((tier) => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.stdtable'));
    // `tier` is the RAW ladder key; the cell's xcams-bridge title is the one
    // place the raw key survives on screen ("Waluigi · Gold on xcams").
    const btn = Array.from(card.querySelectorAll('.std-tier-btn'))
      .find((b) => b.closest('td').title.includes('· ' + tier + ' on xcams'));
    if (!btn) return {error: 'no expand button for ' + tier};
    btn.click();
    return {clicked: true, animations: document.getAnimations().length};
  })
"""

READ_SUBROWS = """
  ((col) => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.stdtable'));
    const rows = Array.from(card.querySelectorAll('tr.std-sub'));
    return {
      rows: rows.length,
      labels: rows.map((r) => r.querySelector('.std-sub-name').textContent.trim()),
      capsWithWings: rows.filter((r) =>
        r.querySelector('.std-sub-name .hat .wing-l')).length,
      linkedInColumn: rows.filter((r) =>
        r.querySelectorAll('td')[col] && r.querySelectorAll('td')[col]
          .querySelector('a[href]')).length,
      timesShown: rows.flatMap((r) => Array.from(r.querySelectorAll('td'))
        .slice(1).map((td) => td.textContent.trim()))
        .filter((t) => t && t !== '—').length,
    };
  })
"""

TIER_ICON = """
  (() => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.stdtable'));
    const buttons = Array.from(card.querySelectorAll('.std-tier-btn'));
    return {
      buttons: buttons.length,
      withCap: buttons.filter((b) => b.querySelector('.hat')).length,
      withWings: buttons.filter((b) => b.querySelector('.hat .wing-l')).length,
      withDivisionOne: buttons.filter((b) => {
        const hat = b.querySelector('.hat');
        return hat && /\\s1$/.test(hat.title);
      }).length,
    };
  })()
"""


@pytest.fixture(scope="module")
def page_state():
    with tempfile.TemporaryDirectory() as scratch:
        with serve_ui(Path(scratch) / "sub.db") as base:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1100)) as page:
                page.goto(base)
                page.evaluate(SETTLE)
                assert page.evaluate(OPEN_PANEL), "no standards toggle on any card"
                page.evaluate(BEAT)
                target = page.evaluate(FIND_LINKED)
                assert not target.get("error"), target
                icons = page.evaluate(TIER_ICON)
                first = page.evaluate(f"({EXPAND})({target['tier']!r})")
                page.evaluate(BEAT)
                expanded = page.evaluate(f"({READ_SUBROWS})({target['col']})")
                # single-open: expanding a DIFFERENT tier closes the first
                other = "Bronze" if target["tier"] != "Bronze" else "Silver"
                page.evaluate(f"({EXPAND})({other!r})")
                page.evaluate(BEAT)
                switched = page.evaluate(f"({READ_SUBROWS})({target['col']})")
                # collapse: toggling the open tier off unmounts every sub row
                page.evaluate(f"({EXPAND})({other!r})")
                page.evaluate(BEAT)
                collapsed = page.evaluate(f"({READ_SUBROWS})({target['col']})")
                return {"target": target, "icons": icons, "first": first,
                        "expanded": expanded, "switched": switched,
                        "collapsed": collapsed}


def test_every_tier_label_wears_the_numbered_winged_division_one_cap(page_state):
    icons = page_state["icons"]
    assert icons["buttons"] == 8, icons
    assert icons["withCap"] == icons["buttons"], icons
    # Every tier here wears wings and reads as division 1 (the hat's own
    # title, "Metal 1") — Capless, the one wingless tier by law, has no row in
    # this table, and Mario's sign field wears its M emblem rather than a
    # digit, the registry's own design.
    assert icons["withWings"] == icons["buttons"], icons
    assert icons["withDivisionOne"] == icons["buttons"], icons


def test_expanding_a_tier_shows_its_five_subdivision_rows(page_state):
    expanded = page_state["expanded"]
    assert expanded["rows"] == 5, expanded
    digits = [label[-1] for label in expanded["labels"]]
    assert digits == ["1", "2", "3", "4", "5"], expanded["labels"]
    assert expanded["timesShown"] >= 5, expanded


def test_subdivision_caps_wear_their_own_division_wings(page_state):
    # I..IV wear at least one wing pair; V wears none (wingTiers' law), so
    # exactly four of the five rows show a winged cap.
    assert page_state["expanded"]["capsWithWings"] == 4, page_state["expanded"]


def test_a_subdivision_threshold_links_to_an_example_in_its_band(page_state):
    target, expanded = page_state["target"], page_state["expanded"]
    assert expanded["linkedInColumn"] >= 1, (
        f"cutoff_videos promises an example for {target}, so at least one of "
        f"its subdivisions must link: {expanded}")


def test_the_expansion_animates_rather_than_snapping(page_state):
    assert page_state["first"]["animations"] >= 1, page_state["first"]


def test_only_one_tier_is_expanded_at_a_time(page_state):
    assert page_state["switched"]["rows"] == 5, page_state["switched"]


def test_collapsing_unmounts_the_rows_after_the_close(page_state):
    assert page_state["collapsed"]["rows"] == 0, page_state["collapsed"]
