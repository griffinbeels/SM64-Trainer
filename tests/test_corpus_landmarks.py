"""The shipped landmark INSTANCE names, and the tool that promotes them.

Round 9 item 1: *"are the doors to each specific course room annotated? Did we
miss anything? …this door is the door to Whomp's Fortress, marked with a '1'
star. It was recognized as a generic door in the lobby."* The answer was no —
the catalogue shipped 510 KIND names and five instance names — so the doors he
had already labelled were re-answering a question he had answered, on every
fresh install.

The names below are HIS, verbatim, promoted with `tools/corpus_from_db.py
--landmarks`. Pinned against the BUILT seed rather than the corpus module, so
a build step that drops them fails here too.
"""
import json
import sys
from pathlib import Path

from sm64_events.core.paths import bundled_defaults_seed

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from corpus_from_db import landmark_row_source  # noqa: E402

# key -> the name he typed. A castle door's identity is its spawn point, so
# these keys are also the check that the coordinate space never shifts.
HIS_DOORS = {
    "6:1:800ebc8c:256,0,-1074": "WF Door",
    "6:1:800ebc8c:-1775,0,-824": "Left Basement Door",
    "6:1:800ebc8c:-271,0,-824": "Right Basement Door",
    "6:1:800ebc7c:-1023,-101,-5170": "Courtyard Door",
    "6:3:800ebc7c:7885,-1586,-511": "Moat to Castle Grounds Door",
    "16:1:800ebc7c:3292,-511,-2931": "Castle Grounds to Moat Door",
    "7:1:800ebc8c:3817,205,870": "Maze Door",
    # The three from his 2026-08-05 session, still shipping.
    "6:3:800ebc8c:1126,-1074,-2661": "HMC Door",
    "6:3:800ebc8c:717,-1177,-869": "Moat Door",
    "6:3:800ebc8c:-3097,-1279,1434": "DDD Door",
}


def _seeded_names() -> dict:
    seed = json.loads(bundled_defaults_seed().read_text(encoding="utf-8"))
    return {row["key"]: row["name"] for row in seed["landmarks"]}


def test_every_door_he_named_ships():
    seeded = _seeded_names()
    for key, name in HIS_DOORS.items():
        assert seeded.get(key) == name, (
            f"{key} should ship as {name!r}, got {seeded.get(key)!r} — a name "
            "he typed once must not need typing again on a fresh install")


def test_a_seeded_instance_name_beats_its_kind_name():
    """Both levels ship for the same object and the specific one has to win,
    or naming a door would be invisible under "a door"."""
    seeded = _seeded_names()
    assert seeded["kind:800ebc8c"] == "door"
    assert seeded["6:1:800ebc8c:256,0,-1074"] == "WF Door"


def test_the_promotion_tool_inverts_a_key_back_into_a_corpus_row():
    """`corpus_landmarks.py`'s docstring named this tool as the promotion path
    for over a day while the tool only handled segment definitions — so every
    name he typed stayed on his machine. This is the inversion, both shapes."""
    assert landmark_row_source("6:1:800ebc8c:256,0,-1074", "WF Door") == (
        "    at(6, 1, 0x800EBC8C, (256, 0, -1074), 'WF Door'),")
    assert landmark_row_source("kind:800edc24", "tree") == (
        "    kind(0x800EDC24, 'tree'),")
