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


CONTEXT = """
const context = {
  courseIcons: { bob: "bob.webp", rr: "rr.png" },   // hmc/ssl/ddd/sl absent
  starIconsMode: "course",
  iconOverrides: { "segment:7": "bitfs" },
  courseByLevel: { "9": 1, "22": 7, "17": 16 },
  segmentLevels: { "7": [19], "9": [6] },
};
"""


def test_course_icon_prefers_the_portrait():
    src = run_node("optionIcon", CONTEXT
                   + 'console.log(JSON.stringify(optionIcon("course", "1", context)));')
    assert src == "/ui/assets/course_icons/bob.webp"


def test_the_four_painting_less_courses_use_their_hand_picked_icons():
    # HMC(6), SSL(8), DDD(9), SL(10) are not entered through a painting, so the
    # game has no portrait. These substitutes are the user's picks (2026-07-25)
    # — the art that reads as that course — not a positional star-1 default.
    for course, expected in ((6, "hmc6"), (8, "ssl2"), (9, "ddd1"), (10, "sl6")):
        src = run_node("optionIcon", CONTEXT
                       + f'console.log(JSON.stringify(optionIcon("course", "{course}", context)));')
        assert src == f"/ui/assets/star_icons/{expected}.png", course


def test_star_one_is_still_the_fallback_behind_the_substitutes():
    # A course with neither a portrait nor a curated substitute (none today —
    # this guards the chain's last rung, not a live case).
    src = run_node("optionIcon", CONTEXT
                   + 'console.log(JSON.stringify(optionIcon("course", "7", context)));')
    assert src == "/ui/assets/star_icons/lll1.png"


def test_star_icon_follows_the_user_preference():
    per_star = run_node("optionIcon", CONTEXT
                        + 'console.log(JSON.stringify(optionIcon("star", "1:2", context)));')
    assert per_star == "/ui/assets/star_icons/bob3.png"
    classic = run_node("optionIcon", CONTEXT + """
const classicContext = { ...context, starIconsMode: "classic" };
console.log(JSON.stringify(optionIcon("star", "1:2", classicContext)));
""")
    assert classic.startswith("/ui/assets/star_"), classic
    assert "star_icons" not in classic


def test_level_icon_routes_through_its_course():
    src = run_node("optionIcon", CONTEXT
                   + 'console.log(JSON.stringify(optionIcon("level", "9", context)));')
    assert src == "/ui/assets/course_icons/bob.webp"


def test_bowser_levels_use_their_own_art():
    src = run_node("optionIcon", CONTEXT
                   + 'console.log(JSON.stringify(optionIcon("level", "17", context)));')
    assert src == "/ui/assets/star_icons/bitdw.png"


def test_segment_icon_uses_the_override_the_banner_uses():
    src = run_node("optionIcon", CONTEXT
                   + 'console.log(JSON.stringify(optionIcon("segment", "7", context)));')
    assert src == "/ui/assets/star_icons/bitfs.png"


def test_every_kind_returns_art_never_null():
    # A picker row with no icon would collapse its layout; the chain always
    # ends at the generic star.
    for call in ('optionIcon("course", "99", context)',
                 'optionIcon("level", "26", context)',
                 'optionIcon("segment", "9", context)',
                 'optionIcon("nonsense", "x", context)'):
        src = run_node("optionIcon", CONTEXT
                       + f'console.log(JSON.stringify({call}));')
        assert src.startswith("/ui/assets/"), (call, src)

# --- visibleGroups -------------------------------------------------------
# Moved here from tests/test_ui_picker.py when components/picker.js was
# deleted (the icon modal replaced it). The function never lived in that
# component — it is in entities.js precisely because this module imports
# nothing and can therefore be executed by node — so the tests follow the
# code rather than the control that used to render it.
GROUPS = """
const groups = [
  { key: "a", label: "Lobby", options: [{ id: "9", name: "BoB" }, { id: "24", name: "WF" }] },
  { key: "b", label: "Basement", options: [{ id: "8", name: "SSL" }] },
];
"""


def test_without_a_filter_every_group_survives():
    tree = run_node("visibleGroups", GROUPS + 'console.log(JSON.stringify(visibleGroups(groups, null, null)));')
    assert [group["label"] for group in tree] == ["Lobby", "Basement"]
    assert [option["id"] for option in tree[0]["options"]] == ["9", "24"]


def test_a_group_emptied_by_the_filter_is_dropped():
    tree = run_node("visibleGroups", GROUPS + 'console.log(JSON.stringify('
                    'visibleGroups(groups, (id) => id === "9", null)));')
    assert [group["label"] for group in tree] == ["Lobby"]
    assert [option["id"] for option in tree[0]["options"]] == ["9"]


def test_the_current_value_survives_a_filter_that_rejects_it():
    # A stored/legacy value fed to a filtered dropdown must never vanish — it
    # renders BLANK and reads as unset. Fixed twice before; pinned here.
    tree = run_node("visibleGroups", GROUPS + 'console.log(JSON.stringify('
                    'visibleGroups(groups, (id) => id === "9", "8")));')
    assert [group["label"] for group in tree] == ["Lobby", "Basement"]
    assert [option["id"] for option in tree[1]["options"]] == ["8"]


def test_filtering_does_not_mutate_the_caller_s_groups():
    tree = run_node("visibleGroups", GROUPS
                    + 'visibleGroups(groups, () => false, null);\n'
                    + 'console.log(JSON.stringify(groups.map((g) => g.options.length)));')
    assert tree == [2, 1]
