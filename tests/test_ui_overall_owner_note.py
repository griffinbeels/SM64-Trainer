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

Driven in a browser, and through the REAL editor endpoints, because the claim
has three parts and only the last is about text: the panel has to read
`overall_owners` at all, the sentence has to track the LADDERS rather than the
star it happens to be on, and it has to say nothing when several strategies
share the job. The fixture's own star is deliberately a SHARED-owner star (its
two ranks visibly differ, which is what every other gate here needs), so the
sole-owner state is reached by editing the ladders and then restored through
the panel's own Community-defaults route.
"""
import json
import sys
import tempfile
import urllib.request
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
    return card.getAttribute('data-feed-key');
  })()
"""

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


def _post(base, path, payload):
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(request, timeout=10).read())


def _put(base, path, payload):
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(), method="PUT",
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(request, timeout=10).read())


def _standards(base, entity):
    from urllib.parse import quote
    return json.loads(urllib.request.urlopen(
        f"{base}/api/ranks/standards?entity={quote(entity)}", timeout=10).read())


@pytest.fixture(scope="module")
def note_states():
    """The panel read three times: as shipped (several owners), after one
    strategy is made fastest everywhere, and after Community defaults puts it
    back."""
    from urllib.parse import quote

    out = {}
    with tempfile.TemporaryDirectory() as scratch:
        with serve_ui(Path(scratch) / "ownernote.db") as base:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1200)) as page:
                page.goto(base)
                page.evaluate(SETTLE)
                entity = page.evaluate(OPEN_PANEL)
                assert entity, "no standards toggle on any card"
                page.evaluate(BEAT)
                out["entity"] = entity
                out["shipped"] = page.evaluate(READ_NOTE)
                out["shipped_owners"] = _standards(base, entity)["overall_owners"]

                # Make ONE strategy the fastest at every defined rank, using
                # the panel's own PUT. Times come from the payload rather than
                # a constant, so this still works when the seed moves.
                data = _standards(base, entity)
                winner = sorted(data["strategies"])[0]
                for rank, seconds in data["overall"].items():
                    _put(base,
                         f"/api/ranks/standards/{quote(entity)}/"
                         f"{quote(winner)}/{quote(rank)}",
                         {"seconds": round(seconds - 0.5, 2)})
                out["winner"] = winner
                # RELOAD rather than waiting for a refetch: the panel loads its
                # payload when you open it and does not re-poll (the same
                # standing behaviour `.claude/rules/library.md` records for the
                # Library page). Asserting on a refetch that does not happen
                # would have made this test measure the wrong thing -- it is
                # what the first version did, and it failed for that reason
                # rather than for the feature's.
                page.goto(base)
                page.evaluate(SETTLE)
                assert page.evaluate(OPEN_PANEL) == entity
                page.evaluate(BEAT)
                out["sole"] = page.evaluate(READ_NOTE)
                out["sole_owners"] = _standards(base, entity)["overall_owners"]

                # Restore through the same control the user has. The
                # scratch `standards_path` above already makes that
                # unnecessary for hygiene -- it is kept because the RESTORE is
                # itself under test below (the sentence must stop being drawn
                # when the ladders go back).
                _post(base, f"/api/ranks/standards/{quote(entity)}/reset", {})
                page.goto(base)
                page.evaluate(SETTLE)
                assert page.evaluate(OPEN_PANEL) == entity
                page.evaluate(BEAT)
                out["restored"] = page.evaluate(READ_NOTE)
                out["restored_owners"] = _standards(base, entity)["overall_owners"]
    for key in ("shipped", "sole", "restored"):
        assert not out[key].get("error"), (key, out[key])
    return out


def _owner_names(owners):
    return {name for names in owners.values() for name in names}


def test_several_owners_say_nothing_at_all(note_states):
    """The control. The fixture's star is shared-owner, so the sentence would
    be false and is therefore absent -- and this asserts it is absent for THAT
    reason rather than because the note never renders."""
    assert len(_owner_names(note_states["shipped_owners"])) > 1, (
        "the fixture star stopped being shared-owner, so this test no longer "
        "controls anything: pick another entity or seed one",
        note_states["shipped_owners"])
    assert note_states["shipped"]["note"] is None, note_states["shipped"]


def test_one_owner_gets_the_sentence_and_is_named(note_states):
    """The claim: when a single strategy sets every cutoff, the panel says so
    and names it, on the SAME entity that a moment ago said nothing -- so the
    sentence tracks the ladders rather than the star."""
    assert len(_owner_names(note_states["sole_owners"])) == 1, \
        note_states["sole_owners"]
    note = note_states["sole"]
    assert note["note"], note_states
    assert note["named"] == note_states["winner"], note
    assert "fastest strategy at every rank" in note["note"], note
    assert "Overall standard" in note["note"], note


def test_the_note_goes_away_again_when_the_ladders_do(note_states):
    """Community defaults puts the shared owners back, so the sentence must
    stop being drawn -- an explanation that outlives the state it explains is
    the same bug as one that never arrives."""
    assert len(_owner_names(note_states["restored_owners"])) > 1, \
        note_states["restored_owners"]
    assert note_states["restored"]["note"] is None, note_states["restored"]


def test_a_lone_strategy_is_not_told_it_won_a_race(note_states):
    """A single-ladder entity owns every cutoff trivially, so the sentence
    would describe a race with one runner. Gated on the number of LADDERS
    rather than the number of ranks, and asserted here because the derivation
    is otherwise identical -- this is the one case where "every rank has the
    same owner" is true and saying so is not."""
    from urllib.parse import quote

    import tempfile as _tempfile

    with _tempfile.TemporaryDirectory() as scratch:
        with serve_ui(Path(scratch) / "lone.db") as base:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1200)) as page:
                page.goto(base)
                page.evaluate(SETTLE)
                entity = page.evaluate(OPEN_PANEL)
                data = _standards(base, entity)
                keep = sorted(data["strategies"])[0]
                # Clear every OTHER strategy's standards through the panel's
                # own × control, leaving exactly one ladder.
                for strat in data["strategies"]:
                    if strat == keep:
                        continue
                    request = urllib.request.Request(
                        f"{base}/api/ranks/standards/{quote(entity)}/{quote(strat)}",
                        method="DELETE")
                    urllib.request.urlopen(request, timeout=10).read()
                after = _standards(base, entity)
                laddered = [s for s, ladder in after["strategies"].items() if ladder]
                assert laddered == [keep], laddered
                assert len(_owner_names(after["overall_owners"])) == 1

                page.goto(base)
                page.evaluate(SETTLE)
                page.evaluate(OPEN_PANEL)
                page.evaluate(BEAT)
                assert page.evaluate(READ_NOTE)["note"] is None, \
                    "a lone ladder must not be told it is the fastest of many"
