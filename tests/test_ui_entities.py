"""The pure group builders (ui/entities.js), driven through node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ENTITIES_JS = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
               / "ui" / "entities.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_node(imports: str, body: str):
    script = f"import {{ {imports} }} from {ENTITIES_JS!r};\n{body}"
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


VOCAB = """
const vocab = {
  levels: { "6": "Castle Inside", "9": "Bob-omb Battlefield", "8": "Shifting Sand Land" },
  courses: { "1": "Bob-omb Battlefield", "8": "Shifting Sand Land" },
  stars: { "1": ["Big Bob-omb", "Footrace"], "8": ["In the Talons", "Shining Atop"] },
  level_groups: [
    { key: "6:1", label: "Lobby", levels: [6, 9] },
    { key: "6:3", label: "Basement", levels: [8] },
  ],
  course_groups: [
    { key: "6:1", label: "Lobby", courses: [1] },
    { key: "6:3", label: "Basement", courses: [8] },
  ],
};
"""


def test_level_options_carry_group_labels_and_string_ids():
    groups = run_node("levelOptions", VOCAB
                      + "console.log(JSON.stringify(levelOptions(vocab)));")
    assert [group["label"] for group in groups] == ["Lobby", "Basement"]
    assert groups[0]["options"] == [{"id": "6", "name": "Castle Inside"},
                                    {"id": "9", "name": "Bob-omb Battlefield"}]


def test_star_options_are_one_control_grouped_by_course():
    groups = run_node("starOptionsFromVocab", VOCAB
                      + "console.log(JSON.stringify(starOptionsFromVocab(vocab)));")
    # a group per COURSE, courses in region order (Lobby's BoB before SSL)
    assert [group["label"] for group in groups] == ["Bob-omb Battlefield",
                                                    "Shifting Sand Land"]
    assert groups[1]["options"] == [{"id": "8:0", "name": "In the Talons"},
                                    {"id": "8:1", "name": "Shining Atop"}]


def test_star_ids_round_trip():
    parsed = run_node("parseStarId",
                      'console.log(JSON.stringify(parseStarId("8:1")));')
    assert parsed == {"course": 8, "star": 1}


def test_catalog_and_vocab_produce_the_same_star_groups():
    catalog = """
const catalog = {
  course_groups: [
    { key: "6:1", label: "Lobby", courses: [1] },
    { key: "6:3", label: "Basement", courses: [8] },
  ],
  courses: [
    { id: 1, name: "Bob-omb Battlefield", stars: ["Big Bob-omb", "Footrace"] },
    { id: 8, name: "Shifting Sand Land", stars: ["In the Talons", "Shining Atop"] },
  ],
};
"""
    groups = run_node("starOptionsFromCatalog", catalog
                      + "console.log(JSON.stringify(starOptionsFromCatalog(catalog)));")
    assert [group["label"] for group in groups] == ["Bob-omb Battlefield",
                                                    "Shifting Sand Land"]
    assert groups[0]["options"][0] == {"id": "1:0", "name": "Big Bob-omb"}


def test_segments_with_no_taxonomy_yet_form_one_group_not_n_others():
    # Before /api/segments/vocab resolves, segmentOptions(segs, undefined) must
    # not bucket by region into N groups that all fall back to the same
    # "Other" label (review M4) — verified: two segments in different regions
    # used to render as two separate "Other" optgroups.
    body = """
const defs = [
  { id: 3, name: "LBLJ", origin: { region: "6:1" } },
  { id: 7, name: "Lakitu Skip", origin: { region: "16" } },
];
console.log(JSON.stringify(segmentOptions(defs, undefined)));
"""
    groups = run_node("segmentOptions", body)
    assert len(groups) == 1
    assert [option["id"] for option in groups[0]["options"]] == ["3", "7"]


def test_segments_group_by_origin_region_in_taxonomy_order():
    body = """
const taxonomy = [
  { key: "16", label: "Castle Grounds", children: [] },
  { key: "6:1", label: "Lobby", children: [] },
  { key: null, label: "Anywhere", children: [] },
];
const defs = [
  { id: 3, name: "LBLJ", origin: { region: "6:1", region_label: "Lobby" } },
  { id: 7, name: "Lakitu Skip", origin: { region: "16", region_label: "Castle Grounds" } },
  { id: 9, name: "Reset split", origin: { region: null, region_label: "Anywhere" } },
];
console.log(JSON.stringify(segmentOptions(defs, taxonomy)));
"""
    groups = run_node("segmentOptions", body)
    assert [group["label"] for group in groups] == ["Castle Grounds", "Lobby",
                                                    "Anywhere"]
    assert groups[0]["options"] == [{"id": "7", "name": "Lakitu Skip"}]
    assert groups[2]["options"] == [{"id": "9", "name": "Reset split"}]
