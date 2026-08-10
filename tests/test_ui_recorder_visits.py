"""One card per PLACE YOU WERE IN — the recorder's grouping rule.

`ui/visits.js` is import-free so node drives the REAL module, the same
arrangement `tests/test_ui_subsections.py` uses. A Python reimplementation
would be a second copy of the one thing that decides where a card breaks.

The other half of this feature — WHERE each row happened — is derived on the
server and gated in `tests/test_api.py`
(`test_timeline_rows_carry_where_they_happened` and the two beside it); this
file assumes those fields and only groups them.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import sm64_events

VISITS_JS = Path(sm64_events.__file__).parent / "ui" / "visits.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def row(row_id, place, label=None, level=None):
    return {"id": row_id, "place": place,
            "place_label": label, "place_level": level}


def cards(rows):
    script = (f"import {{ visitCards }} from {VISITS_JS.as_uri()!r};\n"
              f"console.log(JSON.stringify(visitCards({json.dumps(rows)})));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# Rows arrive NEWEST FIRST, which is the order the recorder draws them.

def test_one_card_per_place_with_its_own_rows():
    got = cards([row(9, "22", "Lethal Lava Land", 22),
                 row(8, "22", "Lethal Lava Land", 22),
                 row(7, "7", "Hazy Maze Cave", 7)])
    assert [(card["label"], len(card["rows"])) for card in got] \
        == [("Lethal Lava Land", 2), ("Hazy Maze Cave", 1)]


def test_going_back_to_a_place_makes_a_SECOND_card_for_it():
    """HMC -> LLL -> HMC is THREE cards, not two.

    This is a timeline, so a card is a VISIT. Folding the two HMC visits
    together would put events minutes apart under one heading with nothing
    saying they were separate trips.
    """
    got = cards([row(9, "7", "Hazy Maze Cave", 7),
                 row(8, "22", "Lethal Lava Land", 22),
                 row(7, "7", "Hazy Maze Cave", 7)])
    assert [card["place"] for card in got] == ["7", "22", "7"]
    assert len({card["key"] for card in got}) == 3, "two visits shared a key"


def test_a_card_is_keyed_on_its_OLDEST_row_so_a_new_arrival_cannot_re_key_it():
    """The load-bearing property, and the reason it is not the obvious one.

    A live row lands at the NEWEST end. Keying a card on its newest row would
    re-key it every time it grew — and the collapsed set is keyed by this, so a
    re-keyed card is one that springs open (or shut) underneath the user while
    they are still playing.
    """
    before = cards([row(8, "22", "Lethal Lava Land", 22),
                    row(7, "22", "Lethal Lava Land", 22)])
    after = cards([row(9, "22", "Lethal Lava Land", 22),
                   row(8, "22", "Lethal Lava Land", 22),
                   row(7, "22", "Lethal Lava Land", 22)])
    assert before[0]["key"] == after[0]["key"] == "7"
    # And a row arriving in a NEW place leaves the old card's key alone too.
    moved = cards([row(9, "7", "Hazy Maze Cave", 7),
                   row(8, "22", "Lethal Lava Land", 22),
                   row(7, "22", "Lethal Lava Land", 22)])
    assert moved[1]["key"] == "7"


def test_rows_with_no_place_group_together_rather_than_vanishing():
    """`null` is a value here, not a hole to skip. The first frames of a fresh
    journal have no established area, and dropping those rows would make them
    unpickable — the caller names the card instead."""
    got = cards([row(3, "7", "Hazy Maze Cave", 7),
                 row(2, None), row(1, None)])
    assert [card["place"] for card in got] == ["7", None]
    assert len(got[1]["rows"]) == 2


def test_an_empty_list_is_no_cards_rather_than_one_empty_card():
    assert cards([]) == []
