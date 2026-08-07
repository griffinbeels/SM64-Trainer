"""The shipped kind catalogue against the one ground truth that exists: what
Griffin actually touched (round 8 item 2, 2026-08-07).

His report: *"looks like when we pick up the bob-omb, it's 'an object' — same
for bowser's tail, it's 'an object'. Is there a way to automatically detect
WHAT the object is?"* The table answering it (`tools/corpus_behaviors.py`)
derives 510 kind names from STROOP's US symbol map through ONE constant — the
behavior segment's RAM base — so the whole thing is wrong at once if that
constant or the ROM version is wrong. This file pins the eight pointers his
own 2026-08-07 session ground-truthed: a table that names all eight correctly
ships; one that misses any is wrong at the base or the version and does not.

Pinned against the BUILT SEED (`data/defaults.seed.json`), not the corpus
module, so a build step that drops the landmarks section fails here too.
The sentence-level joins run the REAL `label_event` over those seed names —
the exact string his recorder rows will show.
"""
import json
import sys
from pathlib import Path

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.storage.db import EventRow
from sm64_events.tracking.eventlabel import label_event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import corpus_behaviors  # noqa: E402


def _seed_kind_names() -> dict:
    seed = json.loads(bundled_defaults_seed().read_text(encoding="utf-8"))
    return {row["key"]: row["name"] for row in seed["landmarks"]
            if row["key"].startswith("kind:")}


# (pointer, seeded name) — each row is a thing he touched on 2026-08-07 and
# named or described himself; the symbols they resolve to are in the module
# docstring of corpus_behaviors.py.
GROUND_TRUTH = (
    (0x800EE2F4, "bob-omb"),        # the BoB bob-omb he picked up, x5
    (0x800EC9D0, "Bowser"),         # Bowser's tail in the Bowser 2 arena
    (0x800EDD38, "Whomp King"),     # the dialogue he named "Whomp Text"
    (0x800EB2C4, "pole"),           # a WF pole
    (0x800EDC24, "tree"),           # the WF "pole" the engine calls a tree
    (0x800EB180, "star door"),      # castle star doors — offset 0, the base
    (0x800EBC7C, "warp door"),      # castle warp doors (his 2026-08-05 kind)
    (0x800EBC8C, "door"),           # the kind of his "HMC Door"
)


def test_every_pointer_he_touched_resolves_to_the_thing_he_touched():
    names = _seed_kind_names()
    assert len(names) >= 500, (
        "the kind catalogue is missing from the seed — the landmarks section "
        f"holds {len(names)} kind rows")
    for pointer, expected in GROUND_TRUTH:
        key = f"kind:{pointer:08x}"
        assert names.get(key) == expected, (
            f"{key} should name {expected!r}, got {names.get(key)!r} — "
            "wrong at the base constant or the ROM version, so the whole "
            "table is suspect, not just this row")


def _moment(kind: str, level: int, pointer: int, ordinal: int = 1) -> EventRow:
    return EventRow(1, 1, 1, "moment_reached", 100, "2026-08-07T00:00:00Z",
                    {"kind": kind, "level": level, "ordinal": ordinal,
                     "landmark": {"key": f"{level}:1:{pointer:08x}:1,2,3",
                                  "kind_key": f"kind:{pointer:08x}"}})


def test_the_sentences_his_report_was_about():
    """End to end: his journal rows, the shipped names, the real labeller.
    The two rows of his report ("it's 'an object'") plus the proper-noun
    grammar and the ordinal retirement, in the exact strings the recorder
    will draw."""
    names = _seed_kind_names()
    assert label_event(_moment("pickup", 9, 0x800EE2F4, ordinal=5), names) \
        == "Pick up a bob-omb in Bob-omb Battlefield"
    assert label_event(_moment("pickup", 33, 0x800EC9D0), names) \
        == "Pick up Bowser in Bowser 2 Arena"
    assert label_event(_moment("textbox", 24, 0x800EDD38), names) \
        == "Trigger Whomp King in Whomp's Fortress"
    assert label_event(_moment("pole_grab", 24, 0x800EDC24), names) \
        == "Grab a tree in Whomp's Fortress"
