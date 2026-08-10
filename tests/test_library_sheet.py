import pytest
from library_fixture import GREY, build_workbook

from sm64_events.library import sheet
from sm64_events.library import workbook as wb

BLACK = "FF000000"


def _rows(rows, runners=("Kally", "Avatar")):
    """rows: list of (label, bold, rgb, best, {runner: (time, link)})."""
    cells = {(1, 1): {"text": "Xcam IGT !"}, (1, 2): {"text": "Sheet Best"},
             (1, 3): {"text": "Player"}, (1, 4): {"text": "Ideal Run"},
             (1, 5): {"text": "Fill Rate"}, (1, 6): {"text": "---"}}
    for i, name in enumerate(runners):
        cells[(1, 7 + i)] = {"text": name}
    for row, (label, bold, rgb, best, times) in enumerate(rows, start=2):
        cells[(row, 1)] = {"text": label, "bold": bold, "rgb": rgb}
        if best is not None:
            cells[(row, 2)] = {"text": best}
        for i, name in enumerate(runners):
            if name in times:
                value, link = times[name]
                cells[(row, 7 + i)] = {"text": value, "link": link}
    return build_workbook({wb.SHEET_MAIN: cells,
                           wb.SHEET_LOG: {(1, 1): {"text": "46238.5"}}})


def test_parse_time_formats():
    assert sheet.parse_time("9.96") == 996
    assert sheet.parse_time("59.96") == 5996
    assert sheet.parse_time("1:20.63") == 8063
    assert sheet.parse_time("") is None
    assert sheet.parse_time("n/a") is None


def test_parse_time_refuses_the_sheets_own_placeholder():
    # 9:59.96 is verbatim the "over 1 minute" example on the rules tab; one
    # live row carries it against a 66.9s target. A typo is not data.
    assert sheet.parse_time("9:59.96") is None


def test_new_target_opens_on_bold_and_subsection_on_grey():
    data = _rows([
        ("1. Bob-omb Battlefield", False, BLACK, None, {}),
        ("[1] Big Bob-omb on the Summit (JP)", True, BLACK, "43.63",
         {"Kally": ("43.80", "https://youtu.be/a")}),
        (" [2] Big Bob-omb on the Summit (US)", False, BLACK, "45.03", {}),
        ("[1|2] Warp fadeout", False, GREY, "15.90", {}),
    ])
    rows = sheet.read_rows(data)
    assert [r.kind for r in rows] == ["approach", "approach", "subsection"]
    assert [r.opens_target for r in rows] == [True, False, False]
    assert rows[0].section == "1. Bob-omb Battlefield"
    assert rows[0].best_cs == 4363
    assert rows[0].entries["Kally"] == (4380, "https://youtu.be/a")


def test_single_id_reuse_is_a_subsection():
    # `[3] Bob-omb grab` follows `[3] Slide + Backflip clip`: a subsection
    # whose bracket carries no pipe at all.
    data = _rows([
        ("[1] Behind Chain Chomp's Gate", True, BLACK, "12.63", {}),
        ("  [3] Slide + Backflip clip", False, BLACK, "11.76", {}),
        ("  [3] Bob-omb grab", False, GREY, "7.83", {}),
    ])
    rows = sheet.read_rows(data)
    assert [r.kind for r in rows] == ["approach", "approach", "subsection"]


def test_already_seen_ids_win_even_against_black_font():
    # The one-way structural rule is definitive: ids already introduced mean
    # subsection whatever the styling says.
    data = _rows([
        ("[1] Big Bob-omb on the Summit", True, BLACK, "43.63", {}),
        (" [2] Left side strat", False, BLACK, "42.90", {}),
        ("[2] Warp fadeout", False, BLACK, "15.90", {}),
    ])
    assert [r.kind for r in sheet.read_rows(data)] == [
        "approach", "approach", "subsection"]


def test_a_target_opens_where_the_id_lineage_RESTARTS_not_where_bold_is():
    # The sheet unbolted all nine of CCM's target rows between two revisions
    # on 2026-08-05 while changing no row, no id and no grey font. Bold as the
    # authority silently lost nine targets; the restart rule keeps them.
    data = _rows([
        ("[1] CCM RTA (RTA strat, Fadeout)", False, BLACK, "4:44.66", {}),
        (" [2] CCM RTA (Any strat, Star-Grab)", False, BLACK, "4:36.37", {}),
        ("[1] Slip Slidin' Away", False, BLACK, "24.50", {}),
        (" [2] No ice bridge clip", False, BLACK, "24.93", {}),
    ])
    rows = sheet.read_rows(data)
    assert [r.opens_target for r in rows] == [True, False, True, False]
    assert [r.bold for r in rows] == [False] * 4      # the drift is visible


def test_a_grey_row_reusing_id_1_does_not_open_a_target():
    # WF's "[1] Whomp text Xcam" is a subsection that reuses lineage 1. The
    # grey veto is what stops the restart rule opening a target on it.
    data = _rows([
        ("[1] Chip off Whomp's Block (JP)", True, BLACK, "29.76", {}),
        ("[1] Whomp text Xcam (JP)", False, GREY, "9.83", {}),
        (" [2] Chip off Whomp's Block (US)", False, BLACK, "28.80", {}),
    ])
    rows = sheet.read_rows(data)
    assert [r.opens_target for r in rows] == [True, False, False]
    assert [r.kind for r in rows] == ["approach", "subsection", "approach"]


def test_font_and_time_disagreement_raises_naming_the_row():
    # A restyled subsection: black font says approach, but 15.90 beats the
    # target's own 43.63, so the temporal rule dissents. Refuse, do not guess.
    data = _rows([
        ("[1] Big Bob-omb on the Summit", True, BLACK, "43.63", {}),
        ("[2] Warp fadeout", False, BLACK, "15.90", {}),
    ])
    with pytest.raises(sheet.ClassificationConflict) as err:
        sheet.read_rows(data)
    assert "Warp fadeout" in str(err.value)


def test_a_grey_row_that_is_slower_than_its_target_also_raises():
    # The mirror of the case above, and the reason the temporal rule is a
    # SIGNAL rather than a sanity check: grey says subsection, the time says
    # it cannot be one.
    data = _rows([
        ("[1] Big Bob-omb on the Summit", True, BLACK, "43.63", {}),
        ("[2] Some slower thing", False, GREY, "50.00", {}),
    ])
    with pytest.raises(sheet.ClassificationConflict):
        sheet.read_rows(data)


def test_a_star_xcam_row_is_an_approach_despite_reusing_ids():
    # "100 coin star Xcam" and "Red coin star Xcam" carry the UNION of several
    # approaches' ids, so the one-way id rule called all 25 of them
    # subsections. They time a STAR GRAB, which is a whole-target time for the
    # star the target maps to (user corrected every one by hand, 2026-08-05).
    data = _rows([
        ("[1] Red Coins on the Floating Isle + 100c (JP)", True, BLACK, "57.00", {}),
        (" [2] Red Coins on the Floating Isle + 100c (US)", False, BLACK, "56.70", {}),
        ("[1|2] 100 coin star Xcam", False, GREY, "51.80", {}),
    ])
    assert [r.kind for r in sheet.read_rows(data)] == [
        "approach", "approach", "approach"]


def test_an_ordinary_xcam_row_is_still_a_subsection():
    # Every OTHER Xcam row on the sheet times a door, a text box or an entry,
    # and those genuinely are parts. The rule is "star Xcam", not "Xcam".
    data = _rows([
        ("[1] Chip off Whomp's Block (JP)", True, BLACK, "29.76", {}),
        ("[1] Whomp text Xcam (JP)", False, GREY, "9.83", {}),
    ])
    assert [r.kind for r in sheet.read_rows(data)] == ["approach", "subsection"]


def test_a_legitimately_faster_approach_is_not_vetoed():
    # Every real alternate strategy is faster than the one before it; the
    # slowest non-RTA approach row on the live sheet sits at 0.770 of its
    # basis, so the veto floor must leave 0.9-ish ratios alone.
    data = _rows([
        ("[1] Blast Away the Wall", True, BLACK, "12.26", {}),
        ("[2] Texture setup", False, BLACK, "10.50", {}),
        ("  [3] Salt setup", False, BLACK, "8.63", {}),
    ])
    assert [r.kind for r in sheet.read_rows(data)] == ["approach"] * 3


def test_stage_rta_routes_are_exempt_from_the_veto():
    # A 70-star route is legitimately a third the length of the full-stage
    # route beside it (THI: 96.40 against 314.74). Different ROUTE, not a
    # subsection -- and these six rows are the whole reason a naive ratio
    # test fails.
    data = _rows([
        ("[1] THI RTA (RTA strat, Fadeout)", True, BLACK, "5:14.74", {}),
        (" [2] THI RTA (70 star, Fadeout)", False, BLACK, "1:36.40", {}),
    ])
    assert [r.kind for r in sheet.read_rows(data)] == ["approach", "approach"]


def test_rom_version_is_read_from_the_label():
    data = _rows([
        ("[1] Big Bob-omb on the Summit (JP)", True, BLACK, "43.63", {}),
        ("[2] Big Bob-omb on the Summit (US)", False, BLACK, "45.03", {}),
        ("[3] Left side strat", False, BLACK, "42.90", {}),
    ])
    assert [r.version for r in sheet.read_rows(data)] == ["jp", "us", None]


def test_a_version_beside_other_words_is_still_read():
    # 58 rows write the ROM version as one comma-separated token inside a
    # COMPOUND parenthetical rather than alone. Anchoring on the bare "(JP)"
    # form read every one of them as version-less, so their halves never
    # paired and 14 targets went into the browse grid under a JP-only name
    # (live report 2026-08-09, "HMC door - Enter DDD (<15 MIPS Clip, US)").
    data = _rows([
        ("[1] HMC door - Enter DDD (\u260615 MIPS Clip, JP)", True, BLACK, "22.33", {}),
        ("[2] HMC door - Enter DDD (\u260615 MIPS Clip, US)", False, BLACK, "24.16", {}),
        ("[3] Post slide (JP, 98/99c -)", False, GREY, "9.83", {}),
        ("[4] Bowser in the Sky Battle (120 star file, US)", False, BLACK, "38.23", {}),
    ])
    assert [r.version for r in sheet.read_rows(data)] == ["jp", "us", "jp", "us"]


def test_a_version_token_is_a_whole_word_not_a_substring():
    # "US" inside a word, and a parenthetical that is not the trailing one,
    # must not be read as a ROM version -- otherwise the pairing would merge
    # rows that are not two versions of anything.
    data = _rows([
        ("[1] Bust the Bonus (JP) then finish", True, BLACK, "12.26", {}),
        ("[2] Chuckya toss", False, BLACK, "13.50", {}),
    ])
    assert [r.version for r in sheet.read_rows(data)] == [None, None]


def test_only_the_version_token_is_dropped_when_pairing():
    # The pairing name keeps every OTHER word in the parenthetical, which is
    # what stops "(MIPS, JP)" merging with "(<50 MIPS, US)": those name two
    # different setups, not two versions of one.
    assert sheet.base_name("HMC door - Enter HMC (Toad, JP)") == "HMC door - Enter HMC (Toad)"
    assert sheet.base_name("HMC door - Enter HMC (Toad, US)") == "HMC door - Enter HMC (Toad)"
    assert (sheet.base_name("Tunnel door - HMC door (MIPS, JP)")
            != sheet.base_name("Tunnel door - HMC door (\u260650 MIPS, US)"))
    assert sheet.base_name("Big Bob-omb on the Summit (JP)") == "Big Bob-omb on the Summit"
    assert sheet.base_name("Left side strat") == "Left side strat"
