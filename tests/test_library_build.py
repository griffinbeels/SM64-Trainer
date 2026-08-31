from library_fixture import GREY, build_workbook

from sm64_events.library import build
from sm64_events.library import workbook as wb

BLACK = "FF000000"


def _workbook(rows, runners=("Kally",)):
    """rows: (label, bold, rgb, best, best_runner, {runner: (time, link)})."""
    cells = {(1, 1): {"text": "Xcam IGT !"}, (1, 2): {"text": "Sheet Best"},
             (1, 3): {"text": "Player"}, (1, 4): {"text": "Ideal Run"},
             (1, 5): {"text": "Fill Rate"}}
    for i, name in enumerate(runners):
        cells[(1, 7 + i)] = {"text": name}
    for row, (label, bold, rgb, best, who, times) in enumerate(rows, start=2):
        cells[(row, 1)] = {"text": label, "bold": bold, "rgb": rgb}
        if best:
            cells[(row, 2)] = {"text": best}
            cells[(row, 3)] = {"text": who}
        for i, name in enumerate(runners):
            if name in times:
                value, link = times[name]
                cells[(row, 7 + i)] = {"text": value, "link": link}
    return build_workbook({wb.SHEET_MAIN: cells,
                           wb.SHEET_LOG: {(1, 1): {"text": "46238.84334791667"}}})


BOB = [
    ("1. Bob-omb Battlefield", False, BLACK, None, None, {}),
    ("[1] Big Bob-omb on the Summit (JP)", True, BLACK, "43.63", "Avatar",
     {"Kally": ("43.80", "https://youtu.be/z")}),
    (" [2] Big Bob-omb on the Summit (US)", False, BLACK, "45.03", "Kaylee", {}),
    ("[1|2] Warp fadeout", False, GREY, "15.90", "taihou", {}),
    ("  [3] Left side strat", False, BLACK, "42.90", "Kaylee", {}),
]


def test_payload_carries_both_versions_and_the_revision():
    out = build.build(_workbook(BOB), fetched_at="2026-08-04T21:00:00Z")
    assert out["schema_version"] == build.SCHEMA_VERSION
    assert out["sheet_revision"] == "2026-08-04T20:14:25"
    assert out["fetched_at"] == "2026-08-04T21:00:00Z"
    target = out["targets"][0]
    assert target["entity_key"] == "star:1:0"
    paired = target["approaches"][0]
    assert paired["name"] == "Big Bob-omb on the Summit"
    assert paired["times"] == {"jp": 4363, "us": 4503}
    assert paired["best_cs"] == 4363 and paired["best_runner"] == "Avatar"
    assert paired["ids"] == ["1", "2"]


def test_subsections_are_kept_apart_from_approaches():
    target = build.build(_workbook(BOB), fetched_at="x")["targets"][0]
    assert [a["name"] for a in target["approaches"]] == [
        "Big Bob-omb on the Summit", "Left side strat"]
    assert [s["name"] for s in target["subsections"]] == ["Warp fadeout"]


def test_entries_carry_the_runner_time_and_video():
    target = build.build(_workbook(BOB), fetched_at="x")["targets"][0]
    entry, = target["approaches"][0]["entries"]
    # Pin the fields this test owns; `version` is the fitter's and is pinned
    # in tests/test_library_ladders.py.
    assert entry["runner"] == "Kally" and entry["time_cs"] == 4380
    assert entry["video"] == "https://youtu.be/z"


def test_two_rows_of_the_same_version_do_not_merge():
    # Same base name, both (JP): different approaches whose labels collide.
    # Merging them would silently drop one and its entries.
    rows = [
        ("1. Bob-omb Battlefield", False, BLACK, None, None, {}),
        ("[1] Some Star (JP)", True, BLACK, "43.63", "Avatar", {}),
        (" [2] Some Star (JP)", False, BLACK, "44.00", "Kaylee", {}),
    ]
    target = build.build(_workbook(rows), fetched_at="x")["targets"][0]
    assert len(target["approaches"]) == 2
    assert [a["times"] for a in target["approaches"]] == [{"jp": 4363}, {"jp": 4400}]


def test_an_unmapped_target_carries_its_reason():
    rows = [
        ("Castle Movements (Lobby)", False, BLACK, None, None, {}),
        ("★ BoB", False, BLACK, None, None, {}),
        ("[1] Lobby door (L) - BoB door", True, BLACK, "2.76", "Multiple", {}),
    ]
    target = build.build(_workbook(rows), fetched_at="x")["targets"][0]
    assert target["entity_key"] is None
    assert target["miss_reason"] == "castle_movement"
    assert target["group"] == "Castle Movements (Lobby)"
    assert target["section"] == "★ BoB"


def test_coverage_counts_mapped_and_unmapped():
    out = build.build(_workbook(BOB), fetched_at="x")
    cov = build.coverage(out)
    assert cov["targets"] == 1 and cov["mapped"] == 1 and cov["unmapped"] == 0
    assert cov["entities"] == 1
    assert cov["approaches"] == 2 and cov["subsections"] == 1
    assert cov["entries"] == 1 and cov["videos"] == 1 and cov["runners"] == 1


# The Princess's Secret Slide, in the sheet's own shape: ONE heading holding
# both slide stars, their approaches interleaved by id ([1] and [3] are the
# box star's, [2] and [4] the Under-21 star's) and two "Slide time" pieces
# each shared across one pair.
SLIDE = [
    ("Castle Secret Stars", False, BLACK, None, None, {}),
    ("[1] The Princess's Secret Slide", True, BLACK, "24.66", "Avatar",
     {"Kally": ("24.80", "https://youtu.be/box")}),
    ("[2] Under 21", False, BLACK, "20.60", "Suigi",
     {"Kally": ("20.90", "https://youtu.be/u21")}),
    ("[1|2] Slide time", False, GREY, "12.40", "taihou", {}),
    ("[3] Late wall bounce strat", False, BLACK, "24.56", "Ikori", {}),
    ("[4] Late wall bounce strat (U21)", False, BLACK, "20.50", "Ikori", {}),
    ("[3|4] Slide time", False, GREY, "12.30", "taihou", {}),
]


def test_the_slide_block_splits_into_two_stars():
    """2026-08-31, his report: row 550 ("Under 21") is a STAR of ours, not an
    approach of the box star. The sheet models both as variants of one
    heading and interleaves their ids, so the split is stated in
    `mapping.TARGET_SPLITS` and applied after the target is assembled."""
    out = build.build(_workbook(SLIDE), fetched_at="")
    slides = [t for t in out["targets"] if t["section"] == "Castle Secret Stars"]
    assert [t["entity_key"] for t in slides] == ["star:19:0", "star:19:1"], slides
    box, u21 = slides

    assert [a["name"] for a in box["approaches"]] == [
        "The Princess's Secret Slide", "Late wall bounce strat"]
    assert [a["name"] for a in u21["approaches"]] == [
        "Under 21", "Late wall bounce strat (U21)"]
    assert u21["label"] == "Slide Star (Under 21 Seconds)"
    assert u21["miss_reason"] is None

    # The U21 runner time follows its own star rather than the box star's.
    assert any(entry["runner"] == "Kally" and entry["time_cs"] == 2090
               for approach in u21["approaches"] for entry in approach["entries"])
    assert all(entry["time_cs"] != 2090
               for approach in box["approaches"] for entry in approach["entries"])

    # A piece timing BOTH stars stays with the heading it was written under;
    # only one whose ids lie entirely inside the carved-out approaches moves.
    # Here both "Slide time" rows are shared, so both stay.
    assert [s["ids"] for s in box["subsections"]] == [["1", "2"], ["3", "4"]]
    assert u21["subsections"] == []


def test_a_target_with_no_split_rule_is_untouched():
    """The split fires on ONE stated (section, label) pair; everything else
    goes through build() exactly as before."""
    out = build.build(_workbook(BOB), fetched_at="")
    assert len(out["targets"]) == 1
    assert out["targets"][0]["entity_key"] == "star:1:0"
