"""ui/mareloturnstate.js — whose turn it is to celebrate.

    "Strategy THEN star THEN marelo... While it's in the header waiting it
     shouldn't change its rank / animate ANY progress until everything is
     done." (user, 2026-07-29)

Two things wait on this, not one: the full-screen overlay, and the header's own
route rank card, which reads the live payload and would otherwise start its own
climb the instant one lands. Both bugs this file guards were reported by the
user AFTER a fix had shipped for them, and both survived a full suite of
source-scan tests, because both are single state TRANSITIONS:

  * the overlay renders a card that climbs, so gating it on "is anything
    climbing" un-readied it the moment it became ready -- the card flashed in
    and out forever (2026-07-29);
  * the held before-state was captured on the render the celebration ARRIVED.
    That payload already carries the rank being celebrated, so the hold was a
    no-op and the card climbed alongside the banners -- "I THOUGHT i fixed a
    problem where the MARELO display at the top of the Practice page
    prematurely fires... it seems like this bug is still present"
    (2026-08-01).

The decision is arithmetic in an import-free module precisely so a transition
can be driven directly here instead of only through a mounted hook.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

TURN_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
           / "mareloturnstate.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")

# Two ratings and the celebration that sits between them. `score` stands for
# everything the card DRAWS -- if the held payload carries the new one, the
# card has already climbed.
BEFORE = {"scope_id": "route:4", "label": "16 Star", "marelo": 41.0,
          "tier": "Toad", "division": "II", "celebration": None}
AFTER = {"scope_id": "route:4", "label": "16 Star", "marelo": 52.0,
         "tier": "Waluigi", "division": "V",
         "celebration": {"key": "k1", "from": {"tier": "Toad", "division": "II"},
                         "to": {"tier": "Waluigi", "division": "V"}}}


def walk(renders: list[dict]) -> list[dict]:
    """Replay a sequence of renders, returning what each one would DISPLAY.

    Each render is `{marelo, running, graced}` -- exactly the three inputs the
    hook has.
    """
    script = (
        f"import {{ NO_TURN, advanceTurn, displayed }} from {TURN_JS.as_uri()!r};\n"
        f"const renders = {json.dumps(renders)};\n"
        "let state = NO_TURN;\n"
        "const out = [];\n"
        "for (const render of renders) {\n"
        "  state = advanceTurn(state, render);\n"
        "  const shown = displayed(state, render.marelo);\n"
        "  out.push({ ready: state.ready, shown: shown && shown.marelo,\n"
        "             unprompted: state.unprompted,\n"
        "             tier: shown && shown.tier });\n"
        "}\n"
        "console.log(JSON.stringify(out));\n")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def render(marelo, running=False, graced=False):
    return {"marelo": marelo, "running": running, "graced": graced}


def test_ordinary_play_passes_straight_through():
    """With no celebration pending nothing about the header changes."""
    seen = walk([render(BEFORE), render(BEFORE), render(AFTER | {"celebration": None})])
    assert [row["ready"] for row in seen] == [True, True, True]
    assert [row["shown"] for row in seen] == [41.0, 41.0, 52.0]


def test_the_card_keeps_the_rank_it_had_until_the_banners_are_done():
    """THE bug, twice reported: the payload that DELIVERS the celebration is
    the one carrying the new rank, so it can never be the before-state."""
    seen = walk([
        render(BEFORE),                              # ordinary play
        render(AFTER),                               # the rank-up lands
        render(AFTER, running=True),                 # banners start climbing
        render(AFTER, running=True),
        render(AFTER, running=False),                # the last banner settles
    ])
    assert [row["shown"] for row in seen] == [41.0, 41.0, 41.0, 41.0, 52.0], (
        "the header card must keep drawing the rank it had until its turn")
    assert [row["tier"] for row in seen[1:4]] == ["Toad"] * 3
    assert [row["ready"] for row in seen] == [True, False, False, False, True]


def test_the_turn_survives_the_celebrations_own_climb():
    """The latch. The overlay renders a card, that card climbs, and that climb
    is the very signal being waited on -- so readiness must never be revoked
    once given, or the overlay mounts and unmounts forever."""
    seen = walk([
        render(BEFORE),
        render(AFTER),
        render(AFTER, running=True),
        render(AFTER, running=False),                # our turn
        render(AFTER, running=True),                 # the overlay's own climb
        render(AFTER, running=True),
    ])
    assert [row["ready"] for row in seen[3:]] == [True, True, True]
    assert [row["shown"] for row in seen[3:]] == [52.0, 52.0, 52.0]


def test_a_celebration_with_no_banners_still_gets_its_turn():
    """Nothing is mounted to climb on the Rank or Replay tabs. "No climb is
    running" the instant a celebration lands means NOT YET (the banners and
    the payload arrive from one refresh, unordered) -- so the turn waits for a
    grace window, and then it must actually ARRIVE."""
    seen = walk([
        render(BEFORE),
        render(AFTER),                               # nothing running, ever
        render(AFTER),
        render(AFTER, graced=True),                  # the window elapsed
    ])
    assert [row["ready"] for row in seen] == [True, False, False, True]


def test_it_does_not_fire_on_the_frame_it_wins_the_race():
    """Without the grace window the overlay fired immediately whenever the
    payload beat the banners to the page, which is the original report."""
    seen = walk([render(BEFORE), render(AFTER), render(AFTER)])
    assert [row["ready"] for row in seen] == [True, False, False]


def test_a_celebration_already_pending_at_load_is_never_shown():
    """The server holds a scope rank-up until it is acked, so one earned before
    the app was closed greets the next page load unprompted.

        "when I opened the page for the first time in my session, the MARELO
         display / animation triggered. This should NEVER be triggered outside
         of updating a PB... it feels like a bug (because I didn't trigger it)"
        (user, 2026-08-01)

    `marelo` is null until the first fetch answers, so the realistic sequence
    starts with nothing at all -- and that must NOT read as "we saw a quiet
    payload"."""
    seen = walk([render(None), render(None), render(AFTER), render(AFTER)])
    assert [row["unprompted"] for row in seen] == [False, False, True, True], (
        "a celebration that was already pending when this client arrived was "
        "not triggered by anything the user just did")
    # And the header simply shows the live payload: there is no before-state to
    # hold, and nothing to hold it back from.
    assert seen[2]["shown"] == 52.0


def test_a_rank_up_earned_while_watching_is_not_unprompted():
    """The inverse, and the one that matters: having seen one quiet payload is
    the whole difference."""
    seen = walk([render(BEFORE), render(AFTER), render(AFTER, running=True)])
    assert [row["unprompted"] for row in seen] == [False, False, False]


def test_a_second_rank_up_waits_all_over_again():
    """A fresh key is a fresh turn: it may not inherit the previous one's."""
    second = {**AFTER, "marelo": 60.0,
              "celebration": {**AFTER["celebration"], "key": "k2"}}
    seen = walk([
        render(BEFORE), render(AFTER), render(AFTER, graced=True),   # ready
        render(second), render(second, running=True),
        render(second, running=False),
    ])
    assert [row["ready"] for row in seen[3:]] == [False, False, True]
    # The before-state for the SECOND celebration is still the last payload
    # seen with nothing pending -- there was never a quiet frame in between.
    assert seen[3]["shown"] == 41.0
