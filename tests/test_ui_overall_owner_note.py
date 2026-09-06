# tests/test_ui_overall_owner_note.py
"""The standards panel says WHY one strategy can be the Overall standard.

His report, 2026-08-15: *"it also still seems a bit weird that the 'Standard'
strategy is the exact progression for the 'Overall' ranking -- I feel like it
should be more gradual."* It is not weird and it is not a bug: the entity's own
ladder is a pointwise minimum across strategies, so a strategy that is fastest
at every rank IS that minimum, cutoff for cutoff. Nothing on screen said so,
which is the "correct but unexplained reads as a bug" shape this project has a
standing rule about.

Not an edge case worth a footnote, either -- measured against the bundled seed,
**76 entities with two or more strategies have a single strategy owning every
cutoff of their best-possible ladder**, his own `star:7:2` among them.

WHEN the sentence is true is the server's call (`ranks/scoring.py::
sole_overall_owner`, unit-tested in tests/test_ranks_scoring.py: ties, a
lone ladder, a split ladder). What is driven here is the other half: the
panel draws from that field, names the owner, tracks the LADDERS rather than
the star it happens to be on, and draws nothing when the field is null --
through the REAL editor endpoints, on the fixture's own star, which is
deliberately a SHARED-owner star. The WORDING is not pinned: it is his to
change, and a wording pass must not be a red build in a test that needs a
server to run.
"""
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from ui_fixture import serve_ui  # noqa: E402

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402
from standards_panel import BEAT, OPEN_PANEL, SETTLE, api, standards_payload  # noqa: E402

READ_NOTE = """
  (() => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.stdtable'));
    if (!card) return {error: 'no open standards table'};
    const note = card.querySelector('.std-overall-note');
    return {
      note: note ? note.textContent.replace(/\\s+/g, ' ').trim() : null,
      named: note ? (note.querySelector('b') || {}).textContent : null,
    };
  })()
"""


@pytest.fixture(scope="module")
def note_states():
    """The panel read four times on ONE server and ONE browser: as shipped
    (several owners), after one strategy is made fastest everywhere, after
    Community defaults puts it back, and after every other strategy is
    deleted so a single ladder is left. Each read RELOADS rather than waiting
    for a refetch: the panel loads its payload when you open it and does not
    re-poll (the standing behaviour `.claude/rules/library.md` records for
    the Library page)."""
    out = {}

    def read(page, base, entity, key):
        page.goto(base)
        page.evaluate(SETTLE)
        assert page.evaluate(OPEN_PANEL) == entity, key
        page.evaluate(BEAT)
        out[key] = page.evaluate(READ_NOTE)
        assert not out[key].get("error"), (key, out[key])
        out[key]["served"] = standards_payload(base, entity)["sole_overall_owner"]

    with tempfile.TemporaryDirectory() as scratch:
        # No bundled library (round 33): the sheet-fitted ladders it would
        # add to every star cannot be cleared through the panel's ×, and this
        # test reasons about exactly the ladders it writes.
        with serve_ui(Path(scratch) / "ownernote.db", bundled_library=False) as base:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1200)) as page:
                page.goto(base)
                page.evaluate(SETTLE)
                entity = page.evaluate(OPEN_PANEL)
                assert entity, "no standards toggle on any card"
                read(page, base, entity, "shipped")

                # Make ONE strategy the fastest at every defined rank, using
                # the panel's own PUT. Times come from the payload rather than
                # a constant, so this still works when the seed moves.
                data = standards_payload(base, entity)
                winner = sorted(data["strategies"])[0]
                for rank, seconds in data["overall"].items():
                    api(base, f"/api/ranks/standards/{quote(entity)}/"
                              f"{quote(winner)}/{quote(rank)}",
                        {"seconds": round(seconds - 0.5, 2)}, method="PUT")
                out["winner"] = winner
                read(page, base, entity, "sole")

                # Restore through the same control the user has -- the
                # RESTORE is itself under test (the sentence must stop being
                # drawn when the ladders go back).
                api(base, f"/api/ranks/standards/{quote(entity)}/reset", {})
                read(page, base, entity, "restored")

                # Clear every OTHER strategy's standards through the panel's
                # own × control, leaving exactly one ladder.
                keep = sorted(data["strategies"])[0]
                for strat in data["strategies"]:
                    if strat != keep:
                        api(base, f"/api/ranks/standards/{quote(entity)}/"
                                  f"{quote(strat)}", method="DELETE")
                after = standards_payload(base, entity)
                laddered = [s for s, ladder in after["strategies"].items() if ladder]
                assert laddered == [keep], laddered
                read(page, base, entity, "lone")
    return out


def test_several_owners_say_nothing_at_all(note_states):
    """The control. The fixture's star is shared-owner, so the sentence would
    be false and is therefore absent -- and this asserts it is absent for THAT
    reason rather than because the note never renders."""
    assert note_states["shipped"]["served"] is None, (
        "the fixture star stopped being shared-owner, so this test no longer "
        "controls anything: pick another entity or seed one")
    assert note_states["shipped"]["note"] is None, note_states["shipped"]


def test_one_owner_gets_the_sentence_and_is_named(note_states):
    """The claim: when the server names a sole owner, the panel draws the
    sentence and names the same strategy, on the SAME entity that a moment
    ago said nothing -- so the sentence tracks the ladders, not the star."""
    assert note_states["sole"]["served"] == note_states["winner"]
    note = note_states["sole"]
    assert note["note"], note_states
    assert note["named"] == note_states["winner"], note


def test_the_note_goes_away_again_when_the_ladders_do(note_states):
    """Community defaults puts the shared owners back, so the sentence must
    stop being drawn -- an explanation that outlives the state it explains is
    the same bug as one that never arrives."""
    assert note_states["restored"]["served"] is None
    assert note_states["restored"]["note"] is None, note_states["restored"]


def test_a_lone_strategy_is_not_told_it_won_a_race(note_states):
    """A single-ladder entity owns every cutoff trivially, so the sentence
    would describe a race with one runner. The server gates that (and the
    scoring tests prove it); this is the screen agreeing with it."""
    assert note_states["lone"]["served"] is None
    assert note_states["lone"]["note"] is None, \
        "a lone ladder must not be told it is the fastest of many"
