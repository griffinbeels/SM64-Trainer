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
