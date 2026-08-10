# tests/test_practice_log_open_slot_render.py
"""The practice log always has exactly ONE card open, and it is never empty.

`tests/test_ui_practice_log.py` drives the rule (`autoOpenKey`) under node.
This drives the whole chain in a real browser, because the rule was never the
part that broke. Both bugs this feature has had were the same shape: the slot
named a card that was NOT ON SCREEN -- once because the page resolved it
against the unfiltered view while the log renders a filtered one (a Bowser
reds star and its pipe segment tie on `last_activity`, and the exclusivity
rule that used to pick one of them -- retired 2026-08-10, reds-as-subsection
-- drew only that one), and once because the active entity took the slot
with nothing recorded. A key naming an unrendered card is indistinguishable from "nothing
qualifies", so `isCardOpen` matches nothing and EVERY card sits closed -- with
every test of the rule green.

That is what these two assertions are for, and why they are about the count
rather than the identity: **zero open cards is the failure signature**, and it
is visible in any state the fixture can reach.

COVERAGE OWED, stated rather than left as a silent gap. The state his report
was actually about -- an ACTIVE entity with nothing recorded, beside a card
that has history -- is not reachable through `tools/ui_fixture.py`: every
config that makes something active also gives it attempts (`_seed_target`
seeds the star, and targeting a segment needs the player standing in it, which
`_arm_segment` only reaches with a row already recorded). Measured, not
assumed: `arm_segment` leaves the target star active with 4 rows, and
`target_segment=`+`arm_segment=` leaves the segment active with 1. Reaching it
needs a fixture that seeds a target WITHOUT attempts, which changes what the
whole responsive matrix measures and belongs in its own change with its own
`known_defects` reckoning (the same reasoning `tests/test_ui_lone_route_render
.py` records for its own owed half). Until then the identity half of the rule
is pinned by node, mutation-proved three ways, and the COUNT half is here.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import FIXTURE_SEGMENT, serve_ui  # noqa: E402

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402

SETTLE = "new Promise(r => setTimeout(r, 2500))"

# `total` is the same signal `ui/uilog.js` records, deliberately: a closed card
# draws no attempt table at all, so "has rows" IS "is open" and there is no
# second notion of openness for this to disagree with.
READ_CARDS = """
  Array.from(document.querySelectorAll('.log-card')).map((card) => ({
    name: (card.querySelector('.log-card-name') || {}).textContent || '',
    active: card.classList.contains('log-card-active'),
    rows: card.querySelectorAll('.attempt-table tr').length,
  }))
"""


@pytest.fixture(scope="module")
def cards(tmp_path_factory):
    db = tmp_path_factory.mktemp("openslot") / "slot.db"
    with serve_ui(db, arm_segment=FIXTURE_SEGMENT) as base:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            page.evaluate(SETTLE)
            return page.evaluate(READ_CARDS)


def test_the_page_renders_several_cards_to_choose_between(cards):
    """Guard the precondition: one card, or none, would make both assertions
    below true for reasons that have nothing to do with the slot."""
    assert len(cards) >= 3, f"too few cards to be measuring anything: {cards}"


def test_exactly_one_card_is_open(cards):
    """ZERO is the signature of the bug he reported -- a slot naming a card
    the log did not draw. TWO would mean the slot is being answered in more
    than one place again, which is how it got here."""
    opened = [c for c in cards if c["rows"]]
    assert len(opened) == 1, f"expected exactly one open card, got {opened}"


def test_the_open_card_has_something_recorded(cards):
    """The eligibility rule, from the direction this fixture can reach: an
    empty card must never be the one the system opened by itself."""
    opened = [c for c in cards if c["rows"]]
    assert opened[0]["rows"] > 0, (
        f"the auto-opened card has nothing in it: {opened[0]}")
