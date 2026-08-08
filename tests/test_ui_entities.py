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


def test_special_stages_use_their_own_art_not_a_plain_star():
    # Courses 16-24 sit past the end of COURSE_ICON_PREFIXES, so before this
    # map they fell through to the generic gold star — caught by a live render
    # check (2026-07-25), not by a unit test, which is why this one exists.
    for course, expected in ((16, "bitdw"), (18, "bits"), (20, "metal"),
                             (22, "vanish"), (24, "aqua")):
        src = run_node("optionIcon", CONTEXT
                       + f'console.log(JSON.stringify(optionIcon("course", "{course}", context)));')
        assert src == f"/ui/assets/star_icons/{expected}.png", course


def test_a_special_stage_with_a_real_portrait_prefers_it():
    # PSS is the one special stage the game DOES give a painting.
    src = run_node("optionIcon", "const context = { courseIcons: "
                   '{ princess_secret: "princess_secret.webp" } };\n'
                   'console.log(JSON.stringify(optionIcon("course", "19", context)));')
    assert src == "/ui/assets/course_icons/princess_secret.webp"


def test_stars_inside_a_special_stage_wear_the_stage_art():
    # A special stage has ONE icon, not seven, so `${prefix}${slot+1}` would
    # request a vanish1.png that does not exist. Every star in it uses the
    # stage's own art.
    src = run_node("optionIcon", CONTEXT
                   + 'console.log(JSON.stringify(optionIcon("star", "22:1", context)));')
    assert src == "/ui/assets/star_icons/vanish.png"


CATALOG_UNION = """
const catalog = { courses: [
  { id: 1, name: "Bob-omb Battlefield", stars: ["Big Bob-omb", "Footrace"] },
  { id: 6, name: "Hazy Maze Cave", stars: ["Swimming Beast"] },
] };
const segments = [
  { id: 3, name: "HMC to LLL", origin: { key: "7" } },        // level 7 = HMC
  { id: 9, name: "LBLJ", origin: { key: "6:1" } },           // castle, no course
];
const courseByLevel = { "7": 6, "9": 1 };
"""


def test_layer_two_unions_a_courses_stars_and_its_segments():
    groups = run_node("courseUnionGroups", CATALOG_UNION
                      + "console.log(JSON.stringify(courseUnionGroups("
                      + "catalog, segments, courseByLevel)));")
    hmc = next(group for group in groups if group["label"] == "Hazy Maze Cave")
    assert [option["name"] for option in hmc["options"]] == [
        "Swimming Beast", "HMC to LLL"]
    assert hmc["options"][1]["id"] == "segment:3"


def test_a_castle_segment_is_filed_under_castle_not_a_course():
    # LBLJ starts in the lobby, which is not a course. It must not be filed
    # under one — but it must still be REACHABLE, which is why it lands in a
    # trailing "Castle" group (review I2 corrected the original behaviour of
    # dropping it entirely).
    groups = run_node("courseUnionGroups", CATALOG_UNION
                      + "console.log(JSON.stringify(courseUnionGroups("
                      + "catalog, segments, courseByLevel)));")
    course_groups = [group for group in groups if group["label"] != "Castle"]
    course_names = [option["name"] for group in course_groups
                    for option in group["options"]]
    assert "LBLJ" not in course_names
    castle = next(group for group in groups if group["label"] == "Castle")
    assert [option["name"] for option in castle["options"]] == ["LBLJ"]


def test_grid_order_is_main_courses_then_specials_then_castle_secret():
    # The catalog's own order leads with course 0, which put "Castle Secret"
    # where Bob-omb Battlefield belongs (live check 2026-07-25).
    groups = run_node("courseUnionGroups", """
const catalog = { courses: [
  { id: 0, name: "Castle Secret", stars: ["Toad"] },
  { id: 16, name: "BitDW", stars: ["Key"] },
  { id: 19, name: "PSS", stars: ["Slide"] },
  { id: 1, name: "Bob-omb Battlefield", stars: ["Big Bob-omb"] },
] };
console.log(JSON.stringify(courseUnionGroups(catalog, [], {})));
""")
    assert [group["label"] for group in groups] == [
        "Bob-omb Battlefield", "BitDW", "PSS", "Castle Secret"]


def test_courses_keep_game_order():
    groups = run_node("courseUnionGroups", CATALOG_UNION
                      + "console.log(JSON.stringify(courseUnionGroups("
                      + "catalog, segments, courseByLevel)));")
    # Castle trails the courses — it is the home for segments that belong to
    # none of them, not a course itself.
    assert [group["label"] for group in groups] == [
        "Bob-omb Battlefield", "Hazy Maze Cave", "Castle"]


def test_course_union_groups_attach_a_rank_from_the_star_entity_key():
    # The rank map is keyed "star:1:0", but the star OPTION's id is the bare
    # composite "1:0" -- the likeliest silent bug in this feature is getting
    # that translation backwards, which would leave every star unranked while
    # segments (whose ids already match the map's "segment:N" keys) keep
    # working. Star side first, on purpose.
    body = CATALOG_UNION + """
const ranksByKey = { "star:1:0": { rank: "Gold", division: "III" } };
console.log(JSON.stringify(courseUnionGroups(
  catalog, segments, courseByLevel, ranksByKey)));
"""
    groups = run_node("courseUnionGroups", body)
    bob = next(group for group in groups if group["label"] == "Bob-omb Battlefield")
    ranked = next(option for option in bob["options"] if option["id"] == "1:0")
    # `rank` rides NESTED {rank, division} (mario-cap-rank-icons integration,
    # 2026-07-26) -- PracticeCell hands it straight to RankIcon, which now
    # wants the same shape rank_by_star/segment_targets already carry.
    assert ranked["rank"] == {"rank": "Gold", "division": "III"}
    unranked = next(option for option in bob["options"] if option["id"] == "1:1")
    assert unranked.get("rank") is None


def test_course_union_groups_attach_the_strategy_the_rank_was_earned_with():
    # I2 (final review, 2026-07-26): `strat` must ride alongside `rank` --
    # practicecell.js needs it to name which strategy a picker badge grades,
    # since the SAME cell later shows a DIFFERENT (active) strategy's medal
    # on the practice banner. Dropped at this exact hop before the fix.
    body = CATALOG_UNION + """
const ranksByKey = { "star:1:0": { rank: "Gold", division: "III", strat: "BLJ" } };
console.log(JSON.stringify(courseUnionGroups(
  catalog, segments, courseByLevel, ranksByKey)));
"""
    groups = run_node("courseUnionGroups", body)
    bob = next(group for group in groups if group["label"] == "Bob-omb Battlefield")
    ranked = next(option for option in bob["options"] if option["id"] == "1:0")
    assert ranked["strat"] == "BLJ"


def test_course_union_groups_attach_a_rank_from_the_segment_entity_key():
    # A segment option's id ("segment:3") already matches the rank map's key
    # shape -- no translation needed, unlike the star side above.
    body = CATALOG_UNION + """
const ranksByKey = { "segment:3": { rank: "Bronze" } };
console.log(JSON.stringify(courseUnionGroups(
  catalog, segments, courseByLevel, ranksByKey)));
"""
    groups = run_node("courseUnionGroups", body)
    hmc = next(group for group in groups if group["label"] == "Hazy Maze Cave")
    ranked = next(option for option in hmc["options"] if option["id"] == "segment:3")
    # No `division` in the source map here -- withRank still nests it as
    # {rank, division}, and JSON.stringify drops the undefined-valued key.
    assert ranked["rank"] == {"rank": "Bronze"}


def test_course_union_groups_still_defaults_ranksbykey_for_the_old_call_shape():
    # header.js calls courseUnionGroups with only three arguments until a
    # later task wires the ranks fetch -- the fourth parameter must default,
    # and an option's shape must come back byte-for-byte the same as before
    # this parameter existed (JSON.stringify drops an `undefined` property).
    groups = run_node("courseUnionGroups", CATALOG_UNION
                      + "console.log(JSON.stringify(courseUnionGroups("
                      + "catalog, segments, courseByLevel)));")
    hmc = next(group for group in groups if group["label"] == "Hazy Maze Cave")
    assert hmc["options"][0] == {"id": "6:0", "name": "Swimming Beast"}


def test_segment_ids_are_distinguishable_from_star_ids():
    parsed = run_node("parseSegmentId",
                      'console.log(JSON.stringify(['
                      'parseSegmentId("segment:12"), parseSegmentId("8:1")]));')
    assert parsed == [12, None]


def test_castle_segments_get_their_own_group_rather_than_vanishing():
    # A segment starting in the castle belongs to no course. Dropping it made
    # the union read as complete while there was no way to target it at all
    # (whole-branch review I2, 2026-07-25).
    groups = run_node("courseUnionGroups", """
const catalog = { courses: [{ id: 1, name: "BoB", stars: ["Big Bob-omb"] }] };
const segments = [{ id: 9, name: "LBLJ", origin: { key: "6:1" } }];
console.log(JSON.stringify(courseUnionGroups(catalog, segments, { "9": 1 })));
""")
    assert [group["label"] for group in groups] == ["BoB", "Castle"]
    assert groups[1]["options"] == [
        {"id": "segment:9", "name": "LBLJ", "sub": "segment"}]


def test_castle_segments_split_into_region_tiles_when_origins_are_in_hand():
    """Round 12 item 2: "for the 'what is this a piece of' display, we should
    show all of the castle areas (lobby, grounds, courtyard, basement,
    upstairs)" — against ONE "Castle" tile holding every movement. Grouping
    reads each segment's server-stamped origin.region and orders by the
    vocab's own taxonomy, never a second hand-written region table. A
    region-less segment keeps the trailing Castle tile."""
    groups = run_node("courseUnionGroups", """
const catalog = { courses: [{ id: 1, name: "BoB", stars: ["Big Bob-omb"] }] };
const segments = [
  { id: 9, name: "LBLJ", origin: { key: "6:1", region: "6:1" } },
  { id: 10, name: "Basement -> DDD", origin: { key: "6:3", region: "6:3" } },
  { id: 11, name: "Lobby -> Upstairs", origin: { key: "6:1", region: "6:1" } },
  { id: 12, name: "Anywhere Trick", origin: { key: null, region: null } },
];
const origins = [
  { key: "16", label: "Castle Grounds", children: [] },
  { key: "6:1", label: "Lobby", children: [] },
  { key: "26", label: "Courtyard", children: [] },
  { key: "6:3", label: "Basement", children: [] },
  { key: "6:2", label: "Upstairs", children: [] },
];
console.log(JSON.stringify(courseUnionGroups(
  catalog, segments, {}, {}, origins)));
""")
    assert [group["label"] for group in groups] == [
        "BoB", "Lobby", "Basement", "Castle"]
    lobby = next(group for group in groups if group["label"] == "Lobby")
    assert [option["name"] for option in lobby["options"]] == [
        "LBLJ", "Lobby -> Upstairs"]
    castle = next(group for group in groups if group["label"] == "Castle")
    assert [option["name"] for option in castle["options"]] == [
        "Anywhere Trick"]


def test_without_origins_the_castle_stays_one_group():
    # The old call shape must come back byte-identical -- a caller that has
    # no vocab in hand yet must not lose the castle tile.
    groups = run_node("courseUnionGroups", """
const catalog = { courses: [] };
const segments = [{ id: 9, name: "LBLJ", origin: { key: "6:1", region: "6:1" } }];
console.log(JSON.stringify(courseUnionGroups(catalog, segments, {})));
""")
    assert [group["label"] for group in groups] == ["Castle"]


def test_segment_levels_come_from_the_origin_in_one_place():
    # Two call sites need this derivation; a second copy is where the header's
    # missing segment art came from.
    levels = run_node("segmentLevelsOf", """
const segments = [{ id: 3, origin: { key: "30" } },
                  { id: 4, origin: { key: "6:1" } },
                  { id: 5, origin: { key: null } }];
console.log(JSON.stringify(segmentLevelsOf(segments)));
""")
    assert levels == {"3": [30], "4": [6], "5": []}


# --- entityIcon: THE one chain --------------------------------------------
# `optionIcon(kind, id, ctx)` is a wrapper over `entityIcon(key, ctx)`, so the
# cases above cover the chain's rungs. What follows pins what the KEY form
# adds — the cases three hand-rolled resolvers used to answer differently
# (live report 2026-07-26: BitFS Pipe Entry drew Bowser on the practice banner
# and a plain gold star on the Rank tab's coverage strip).


def test_a_segment_key_resolves_the_same_art_the_banner_draws():
    # The Rank tab has only the entity KEY; the banner had start_levels. Both
    # go through this call now, and the start level rides the shared context.
    src = run_node("entityIcon", CONTEXT
                   + 'console.log(JSON.stringify(entityIcon("segment:7", context)));')
    assert src == "/ui/assets/star_icons/bitfs.png"


def test_option_and_key_forms_are_the_same_chain():
    both = run_node("entityIcon, optionIcon", CONTEXT + """
console.log(JSON.stringify([
  [entityIcon("star:1:2", context), optionIcon("star", "1:2", context)],
  [entityIcon("segment:7", context), optionIcon("segment", "7", context)],
  [entityIcon("course:1", context), optionIcon("course", "1", context)],
  [entityIcon("level:17", context), optionIcon("level", "17", context)],
]));
""")
    for key_form, option_form in both:
        assert key_form == option_form, (key_form, option_form)


def test_bowser_and_cap_stage_stars_wear_their_stage_art():
    # Courses 16-24 are past the end of COURSE_ICON_PREFIXES. Deriving a stem
    # from that table ALONE is why `star:16:0` (BitDW's 8-coin star) drew a
    # generic gold star on the Rank tab and on the Bowser banner row, while
    # the target picker showed the real thing.
    for key, expected in (("star:16:0", "bitdw"), ("star:17:0", "bitfs"),
                          ("star:18:0", "bits"), ("star:21:0", "wing")):
        src = run_node("entityIcon", CONTEXT
                       + f'console.log(JSON.stringify(entityIcon("{key}", context)));')
        assert src == f"/ui/assets/star_icons/{expected}.png", key


def test_an_override_wins_for_a_star_too_and_in_either_mode():
    # optionIcon consulted iconOverrides for SEGMENTS only, so a star's
    # override showed on the banner and was ignored in every picker.
    srcs = run_node("entityIcon", CONTEXT + """
const overridden = { ...context,
  iconOverrides: { ...context.iconOverrides, "star:1:2": "rr7" } };
console.log(JSON.stringify([
  entityIcon("star:1:2", overridden),
  entityIcon("star:1:2", { ...overridden, starIconsMode: "classic" }),
]));
""")
    assert srcs == ["/ui/assets/star_icons/rr7.png"] * 2


def test_an_uploaded_override_resolves_to_its_upload_url():
    # An override stem may be `user:<file>`. Resolving one as a bundled stem
    # asks for /ui/assets/star_icons/user:foo.png — a 404 that degrades to a
    # plain star, which is what every picker did to uploaded icons.
    src = run_node("entityIcon", CONTEXT + """
const uploaded = { ...context, iconOverrides: { "segment:7": "user:my icon.png" } };
console.log(JSON.stringify(entityIcon("segment:7", uploaded)));
""")
    assert src == "/api/icons/file/my%20icon.png"


def test_classic_mode_flattens_stars_only():
    # The setting's control is labelled "Star icons", and the generic gold
    # star is a STAR's own fallback (user decision 2026-07-26). A segment and
    # a Bowser stage keep their real art in classic mode.
    srcs = run_node("entityIcon", CONTEXT + """
const classic = { ...context, starIconsMode: "classic", iconOverrides: {} };
console.log(JSON.stringify([
  entityIcon("star:1:2", classic),
  entityIcon("segment:7", classic),
  entityIcon("star:16:0", classic),
]));
""")
    star, segment, bowser_star = srcs
    assert star == "/ui/assets/star_3.png"
    assert segment == "/ui/assets/star_icons/bitfs.png"
    assert bowser_star == "/ui/assets/star_1.png"


def test_the_fallback_slot_follows_the_star_and_is_zero_otherwise():
    # Every caller's img onerror uses this, so a load failure has to land on
    # the same art the chain itself would have chosen.
    slots = run_node("fallbackSlotForEntityKey", """
console.log(JSON.stringify(["star:1:2", "star:8:0", "segment:7", "course:1",
                            "nonsense"].map(fallbackSlotForEntityKey)));
""")
    assert slots == [2, 0, 0, 0, 0]


# --- default art for a SEEDED segment ---------------------------------------
# Two registries, both keyed on what the corpus itself already says about a
# definition (user, 2026-07-26: "make these default for all users… update ALL
# castle movement segments to use the castle_movement picture"). The context
# fields come off /api/segments rows, so nothing new is derived in the UI.

SEG_CONTEXT = """
const context = {
  courseIcons: {}, starIconsMode: "course", iconOverrides: {},
  courseByLevel: {},
  segmentLevels: { "1": [6], "3": [16], "5": [17], "20": [6], "99": [6],
                   "98": [6], "97": [17] },
  segmentMeta: {
    "1":  { seedKey: "seg:lblj",        category: "Tricks" },
    "3":  { seedKey: "seg:lakitu-skip", category: "Tricks" },
    "5":  { seedKey: "seg:bitdw-pipe",  category: "Castle Movement" },
    "20": { seedKey: "seg:bob->wf",     category: "Castle Movement" },
    // Hand-built rows, the three shapes that reach the fallback: one that
    // starts nowhere (no place in the world graph), one that starts in the
    // basement, and one that starts in a Bowser stage.
    "99": { seedKey: null, category: null, originRegion: null },
    "98": { seedKey: null, category: null, originRegion: "6:3" },
    "97": { seedKey: null, category: null, originRegion: "6:3" },
  },
};
"""


def test_a_seeded_trick_wears_the_art_its_seed_key_names():
    # seed_key, not name or id: a rename keeps the art and every install agrees
    # without a migration.
    srcs = run_node("entityIcon", SEG_CONTEXT + """
console.log(JSON.stringify([entityIcon("segment:1", context),
                            entityIcon("segment:3", context)]));
""")
    assert srcs == ["/ui/assets/star_icons/blj.png",
                    "/ui/assets/star_icons/lakitu.png"]


def test_every_castle_movement_wears_the_castle_movement_art():
    src = run_node("entityIcon", SEG_CONTEXT
                   + 'console.log(JSON.stringify(entityIcon("segment:20", context)));')
    assert src == "/ui/assets/star_icons/castle_movement.png"


def test_a_bowser_pipe_keeps_its_stage_art_despite_its_category():
    # The pipe entries are categorised Castle Movement AND start in a Bowser
    # stage. The stage is the more useful thing to show, which is why
    # LEVEL_ICONS outranks the category table — this is the ordering, pinned.
    src = run_node("entityIcon", SEG_CONTEXT
                   + 'console.log(JSON.stringify(entityIcon("segment:5", context)));')
    assert src == "/ui/assets/star_icons/bitdw.png"


def test_a_segment_that_starts_nowhere_keeps_the_generic_star():
    # No seed_key, no category, and no PLACE either — a reset-anchored rule, an
    # unscoped key grab, a Toad star. Nothing knows what that is, so the ✎
    # override is the answer for it rather than a guess.
    src = run_node("entityIcon", SEG_CONTEXT
                   + 'console.log(JSON.stringify(entityIcon("segment:99", context)));')
    assert src == "/ui/assets/star_1.png"


def test_a_hand_built_segment_that_starts_somewhere_is_a_castle_movement():
    """The v1.6.0 live report: five hand-made movements predating the corpus
    drew plain gold stars beside 47 seeded rows wearing castle_movement, which
    reads as the feature never having shipped. They carry no category, so the
    fallback reads their origin REGION instead — present for every start with a
    place in the world graph."""
    src = run_node("entityIcon", SEG_CONTEXT
                   + 'console.log(JSON.stringify(entityIcon("segment:98", context)));')
    assert src == "/ui/assets/star_icons/castle_movement.png"


def test_the_start_stage_still_outranks_the_inferred_category():
    # Same ordering the seeded pipe entries get (LEVEL_ICONS before the
    # category table) — inferring a category must not move that line.
    src = run_node("entityIcon", SEG_CONTEXT
                   + 'console.log(JSON.stringify(entityIcon("segment:97", context)));')
    assert src == "/ui/assets/star_icons/bitdw.png"


def test_the_inferred_category_is_a_key_of_the_icon_table():
    """One door to the picture. The fallback resolves to a CATEGORY and looks
    the stem up in SEGMENT_CATEGORY_ICONS like every other row; a constant that
    named `castle_movement` itself would be a second table pointing at the same
    art, which is the divergence this module exists to prevent."""
    answer = run_node(
        "SEGMENT_CATEGORY_ICONS, UNCATEGORIZED_SEGMENT_CATEGORY, segmentCategory",
        """
console.log(JSON.stringify({
  known: UNCATEGORIZED_SEGMENT_CATEGORY in SEGMENT_CATEGORY_ICONS,
  corpusWins: segmentCategory("Tricks", "6:3"),
  inferred: segmentCategory(null, "6:3"),
  nowhere: segmentCategory(null, null),
}));
""")
    assert answer == {"known": True, "corpusWins": "Tricks",
                      "inferred": "Castle Movement", "nowhere": None}


def test_an_override_still_beats_every_seeded_default():
    src = run_node("entityIcon", SEG_CONTEXT + """
const picked = { ...context, iconOverrides: { "segment:20": "toad1" } };
console.log(JSON.stringify(entityIcon("segment:20", picked)));
""")
    assert src == "/ui/assets/star_icons/toad1.png"
