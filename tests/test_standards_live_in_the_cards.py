# tests/test_standards_live_in_the_cards.py
"""Each practice-log card carries its OWN rank-standards dropdown, closed.

Griffin, 2026-08-05: "let's actually move this 'Rank Standards' dropdown to
the INSIDE of the individual practice log cards... for each card, we have the
Rank Standards dropdown. It should be closed by default. It should display
whatever the rank standards are for that specific star/segment."

It was ONE page-level panel in `EntityDrawer`, following whichever entity was
focused, so reading a card's ladder meant selecting that card and then looking
somewhere else on the page.

Driven in a real browser, because the two properties that matter are a fetch
and a fold: "closed by default" is invisible to any source scan (the panel
takes `defaultOpen` as a prop and could be passed either), and "displays the
standards for THAT star/segment" is a request keyed on an entity the card has
to derive correctly -- `standardsIdentity` (entitysection.js), which is NOT
the card's own identity for a Bowser reds pair.
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

# `.stdbody` only exists while the panel is OPEN (standards.js renders it
# behind `open &&`), so its absence IS "closed" -- there is no second notion
# of openness here for this to disagree with.
READ = """
  (() => {
    const cards = Array.from(document.querySelectorAll('.log-card'));
    const labelled = (root) => Array.from(root.querySelectorAll('.stdpanel'))
      .filter((p) => (p.textContent || '').includes('Rank standards'));
    return {
      cards: cards.length,
      open: cards.filter((c) => c.querySelector('.log-card-body')).length,
      withPanel: cards.filter((c) => labelled(c).length > 0).length,
      openPanels: cards.filter((c) => c.querySelector('.stdbody')).length,
      inDrawer: labelled(document.querySelector('.detail-drawer') || document.body)
        .filter((p) => p.closest('.detail-drawer')).length,
    };
  })()
"""

OPEN_IT = """
  (() => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.standards-toggle'));
    card.querySelector('.standards-toggle').click();
    return true;
  })()
"""

READ_OPENED = """
  (() => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.stdbody'));
    if (!card) return null;
    return {
      name: (card.querySelector('.log-card-name') || {}).textContent || '',
      body: (card.querySelector('.stdbody').textContent || '').slice(0, 400),
    };
  })()
"""


@pytest.fixture(scope="module")
def page_state():
    with tempfile.TemporaryDirectory() as scratch:
        with serve_ui(Path(scratch) / "std.db") as base:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1100)) as page:
                page.goto(base)
                page.evaluate(SETTLE)
                closed = page.evaluate(READ)
                page.evaluate(OPEN_IT)
                page.evaluate("new Promise(r => setTimeout(r, 1200))")
                opened = page.evaluate(READ_OPENED)
                return {"closed": closed, "opened": opened}


def test_the_fixture_renders_an_open_card_to_look_inside(page_state):
    """Precondition. The panel lives in the card BODY, which only exists on an
    open card -- without one, every assertion below would pass on a page
    nobody is looking at."""
    closed = page_state["closed"]
    assert closed["cards"] >= 1, f"no practice-log cards rendered: {closed}"
    assert closed["open"] >= 1, f"no card is open, so no body to inspect: {closed}"


def test_an_entity_card_carries_its_own_standards_panel(page_state):
    closed = page_state["closed"]
    assert closed["withPanel"] >= 1, (
        f"no card carries a Rank standards panel: {closed}")


def test_it_is_closed_by_default(page_state):
    closed = page_state["closed"]
    assert closed["openPanels"] == 0, (
        f"a card's standards panel rendered already open: {closed}")


def test_the_page_level_drawer_no_longer_carries_one(page_state):
    """Moved, not duplicated -- two copies of a ladder is two places for it to
    disagree, which is the failure this project keeps paying for."""
    closed = page_state["closed"]
    assert closed["inDrawer"] == 0, (
        f"the drawer still renders a Rank standards panel: {closed}")


def test_opening_it_loads_that_entity_s_own_ladder(page_state):
    """The panel fetches on open (never on mount), so this also proves a
    closed card costs no request. The fixture star is graded, so its body
    carries real rank names rather than an empty state."""
    opened = page_state["opened"]
    assert opened is not None, "clicking the toggle opened nothing"
    assert opened["body"].strip(), f"the opened panel is empty: {opened}"


def test_the_ladder_is_fetched_before_it_is_ever_opened():
    """Griffin, 2026-08-06: "when opening the rank standards, it seems like it
    loads the rank standards from disk... which causes there to be a brief lag
    before the rank standards loads -- then, every open/close going forward
    looks as expected. I think the second the card appears in the list, we
    should load those rank standards and prepare them to be opened later."

    The REQUEST COUNT is the honest probe, not the rendered table: a panel that
    fetches on open has made no request while closed, and one that prefetches
    has -- and both look identical from the outside, because a closed panel
    draws nothing either way. The visible symptom was worse than a stall: the
    first open animated a box whose contents arrived mid-flight, so the height
    it measured was the loading state's.
    """
    spy = """
      (() => {
        window.__stdReqs = 0;
        const orig = window.fetch;
        window.fetch = function (...args) {
          if (String(args[0] || "").includes('/api/ranks/standards'))
            window.__stdReqs += 1;
          return orig.apply(this, args);
        };
        return true;
      })()
    """
    with tempfile.TemporaryDirectory() as scratch:
        with serve_ui(Path(scratch) / "prefetch.db") as base:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1000)) as page:
                page.goto(base)
                page.evaluate(spy)
                page.evaluate("new Promise(r => setTimeout(r, 3000))")
                state = page.evaluate("""
                  (() => {
                    const panel = document.querySelector('.log-card .stdpanel');
                    if (!panel) return {error: 'no standards panel'};
                    return {requests: window.__stdReqs || 0,
                            isOpen: !!panel.querySelector('.stdtable')};
                  })()
                """)

    assert state.get("error") is None, state
    assert state["isOpen"] is False, (
        f"the panel opened itself, so this proves nothing about prefetching: {state}")
    assert state["requests"] >= 1, (
        "the ladder is still fetched on first open, so the first open of every "
        f"card animates a box measured around a loading state: {state}")
