"""Every seeded segment must land in a real castle region — otherwise it hides
in "Anywhere" where nobody browsing the library will find it.

No seeded segment is unplaced today (EXPECTED_UNPLACED is empty) — every
course-0 (castle secret) star start the corpus carries resolves via the MIPS
catches table. A future course-0 Toad-star start, which has no table row,
would land here first rather than in the UI.
A NEW corpus row that resolves nowhere fails here rather than in the UI.
"""
import json
from pathlib import Path

from sm64_events.tracking.segments import (origin_taxonomy, origin_view,
                                            start_origin)

SEED = json.loads((Path(__file__).resolve().parent.parent / "src"
                   / "sm64_events" / "data" / "defaults.seed.json")
                  .read_text(encoding="utf-8"))

EXPECTED_UNPLACED: set[str] = set()


def test_every_seeded_segment_resolves_to_a_region():
    unplaced = {segment["name"] for segment in SEED["segments"]
                if origin_view(start_origin(segment["start_triggers"]))["region"]
                is None}
    assert unplaced == EXPECTED_UNPLACED


def test_the_mips_movements_land_in_the_basement():
    mips = [segment for segment in SEED["segments"]
            if segment["name"].startswith("MIPS (")]
    assert mips, "corpus lost its MIPS movements"
    for segment in mips:
        assert origin_view(start_origin(segment["start_triggers"]))["region"] \
            == "6:3", segment["name"]


def test_a_movement_files_under_the_stage_it_leaves():
    ssl_to_lll = next(segment for segment in SEED["segments"]
                      if segment["name"] == "SSL → LLL")
    origin = origin_view(start_origin(ssl_to_lll["start_triggers"]))
    assert origin["label"] == "Shifting Sand Land"
    assert origin["region_label"] == "Basement"


def test_every_seeded_origin_has_a_place_in_the_taxonomy():
    # A node can have a REGION (region_for_node) without having a PLACE in
    # origin_taxonomy — that's exactly what let node "6" render as a group
    # header literally labelled "6" (review I1). This is the regression test.
    known = {place["key"] for group in origin_taxonomy() if group["key"] is not None
             for place in group["children"]}
    for segment in SEED["segments"]:
        node = start_origin(segment["start_triggers"])
        assert node is None or node in known, segment["name"]
