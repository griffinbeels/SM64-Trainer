"""`library/export_column.py` -- your PBs laid out as the Ultimate Sheet's
own column, one line per live worksheet row.

Pure unit tests over hand-built `SheetRow`s and a matching mini-payload; the
HTTP door (`GET /api/scorecard/column`) is `tests/test_scorecard_api.py`'s
job."""
from sm64_events.library.export_column import column_lines, sheet_time
from sm64_events.library.sheet import SheetRow

BLACK = "FF000000"


def _row(row, label, kind, opens_target, ids=("1",), version=None):
    return SheetRow(row=row, group="G", section="S", label=label,
                    ids=frozenset(ids), kind=kind, opens_target=opens_target,
                    version=version, best_cs=None, best_runner="",
                    ideal_cs=None, fill_rate=None)


def test_sheet_time_formats():
    assert sheet_time(4370) == "43.70"
    assert sheet_time(8100) == "1:21.00"


def test_sheet_time_pads_centiseconds():
    assert sheet_time(500) == "5.00"


def _fixture():
    """A header gap at row 5, a target at rows 2-4 (one matched-strategy
    approach, one placer-only approach, one unlinked subsection), and an
    unmatched movement at row 7 (also opens its own target, whose payload
    entry names something the row does not)."""
    rows = [
        _row(2, "Approach A", "approach", True, ids=("1",)),
        _row(3, "Approach B", "approach", False, ids=("2",)),
        _row(4, "Subsection C", "subsection", False, ids=("1",)),
        _row(7, "Unmatched Movement", "approach", True, ids=("1",)),
    ]
    payload = {"targets": [
        {"entity_key": "star:1:0", "label": "Some Star",
         "approaches": [
             {"name": "Approach A", "ids": ["1"], "matched_strategy": "Vetted Strat"},
             {"name": "Approach B", "ids": ["2"]},
         ],
         "subsections": [
             {"name": "Subsection C", "ids": ["1"]},
         ]},
        {"entity_key": None, "label": "A Castle Movement",
         "approaches": [{"name": "Real Name", "ids": ["1"]}],
         "subsections": []},
    ]}
    return rows, payload


def test_line_count_covers_every_worksheet_row_and_blanks_the_gaps():
    rows, payload = _fixture()
    calls = []

    def resolve(entity_key, strat_tag, timer_mode, version, **_):
        calls.append((entity_key, strat_tag, timer_mode, version))
        if entity_key == "star:1:0" and strat_tag == "Vetted Strat":
            return 4370
        if entity_key == "segment:9" and strat_tag == "Some Strategy":
            return 8100
        return None

    def place(target, item, kind):
        if item.get("name") == "Approach B":
            return ("segment:9", "rta", "Some Strategy")
        return None

    lines = column_lines(rows, payload, resolve, place=place)

    last_row = 7
    assert len(lines) == last_row - 1                  # rows 2..7
    # row 2 -- matched_strategy on a star target.
    assert lines[0] == "43.70"
    # row 3 -- no matched_strategy, but the placer lands it.
    assert lines[1] == "1:21.00"
    # row 4 -- a subsection the placer does not place.
    assert lines[2] == ""
    # row 5 -- the header gap: no SheetRow at all.
    assert lines[3] == ""
    # row 6 -- also a gap (no SheetRow).
    assert lines[4] == ""
    # row 7 -- a real row whose label matches nothing in its target.
    assert lines[5] == ""
    # resolve is only ever asked about the two rows that actually mapped.
    assert calls == [("star:1:0", "Vetted Strat", "igt", None),
                     ("segment:9", "Some Strategy", "rta", None)]


def test_a_version_mismatch_is_the_caller_resolves_responsibility():
    """`resolve` owns the game_version comparison (it has the PB row);
    `column_lines` only has to pass the row's own explicit version through."""
    rows = [_row(2, "Approach A", "approach", True, ids=("1",), version="jp")]
    payload = {"targets": [
        {"entity_key": "star:1:0", "label": "Some Star",
         "approaches": [{"name": "Approach A", "ids": ["1"],
                         "matched_strategy": "Vetted Strat"}],
         "subsections": []}]}
    seen = []

    def resolve(entity_key, strat_tag, timer_mode, version, **_):
        seen.append(version)
        return None                                    # the caller says "no match"

    lines = column_lines(rows, payload, resolve)
    assert lines == [""]
    assert seen == ["jp"]


def test_a_star_approach_with_no_vetted_strategy_exports_under_the_sheets_name():
    """The round trip's closing half (round 19): `import_runner.py` files a
    star row under `matched_strategy or the sheet's own name`, so this door
    has to ASK for the same name or a freshly imported column exports blank.
    A name this database never heard of resolves to None, which is why the
    fallback cannot print a wrong time."""
    rows, payload = _fixture()
    asked = []

    def resolve(entity_key, strat_tag, timer_mode, version, **_):
        asked.append((entity_key, strat_tag))
        return 4370 if strat_tag == "Approach B" else None

    lines = column_lines(rows, payload, resolve)
    assert lines[1] == "43.70"                          # row 3, no placer at all
    assert ("star:1:0", "Approach B") in asked


def test_the_placer_outranks_the_star_fallback():
    """`import_runner.py::candidates_for` asks `place` FIRST and only falls
    back to the star; a row he has explicitly linked to a piece he built is
    about that piece, on that piece's own clock."""
    rows, payload = _fixture()

    def place(target, item, kind):
        if item.get("name") == "Approach A":
            return ("segment:9", "rta", "Linked Strat")
        return None

    seen = []

    def resolve(entity_key, strat_tag, timer_mode, version, **_):
        seen.append((entity_key, strat_tag, timer_mode))
        return 8100

    lines = column_lines(rows, payload, resolve, place=place)
    assert lines[0] == "1:21.00"
    assert seen[0] == ("segment:9", "Linked Strat", "rta")


def _split_fixture():
    """One sheet heading that `build.py::_apply_splits` turned into TWO
    targets: the parent, and a carved-out one marked `split_from` with no
    opening row of its own. A second heading follows, to catch the shift."""
    rows = [
        _row(2, "Box Star", "approach", True, ids=("1",)),
        _row(3, "Under 21", "approach", False, ids=("2",)),
        _row(4, "Next Star", "approach", True, ids=("1",)),
    ]
    payload = {"targets": [
        {"entity_key": "star:19:0", "label": "The Slide",
         "approaches": [{"name": "Box Star", "ids": ["1"]}], "subsections": []},
        {"entity_key": "star:19:1", "label": "Slide Star (Under 21 Seconds)",
         "split_from": "The Slide",
         "approaches": [{"name": "Under 21", "ids": ["2"]}], "subsections": []},
        {"entity_key": "star:1:0", "label": "Next Star",
         "approaches": [{"name": "Next Star", "ids": ["1"]}], "subsections": []},
    ]}
    return rows, payload


def test_a_carved_out_target_shares_its_parents_opening_row():
    """The round-18 regression: a split adds a target the sheet has no row
    for, so counting targets one-per-opening-row read every LATER row
    against its neighbour's target -- 214 of 803 rows, the whole tail of the
    sheet, silently blank. One opening row opens one BLOCK."""
    rows, payload = _split_fixture()
    # "Next Star" IS its target's own label, so round 25 asks that row with
    # no strategy at all (`names_the_thing`); the other two name strategies
    # under a differently-labelled target and still ask by name.
    lines = column_lines(rows, payload,
                         lambda entity_key, strat, mode, version, **_: {
                             ("star:19:0", "Box Star"): 4370,
                             ("star:19:1", "Under 21"): 2060,
                             ("star:1:0", None): 8100,
                         }.get((entity_key, strat)))
    assert lines == ["43.70", "20.60", "1:21.00"]


def test_same_name_rows_in_one_block_are_told_apart_by_their_ids():
    """49 live rows share a name with a sibling in their own block -- "Warp
    fadeout" once per route, "100 coin star Xcam" once per 100-coin route.
    Name alone hands both rows the FIRST item, which sends a subsection's
    adoption link (keyed on its ids) to the wrong piece."""
    rows = [
        _row(2, "Some Star", "approach", True, ids=("1",)),
        _row(3, "Warp fadeout", "subsection", False, ids=("1", "2")),
        _row(4, "Warp fadeout", "subsection", False, ids=("3", "4")),
    ]
    payload = {"targets": [
        {"entity_key": "star:1:0", "label": "Some Star",
         "approaches": [{"name": "Some Star", "ids": ["1"]}],
         "subsections": [{"name": "Warp fadeout", "ids": ["1", "2"]},
                         {"name": "Warp fadeout", "ids": ["3", "4"]}]}]}
    placed = []

    def place(target, item, kind):
        placed.append(sorted(item["ids"]))
        return None

    column_lines(rows, payload, lambda *a, **_: None, place=place)
    assert placed == [["1"], ["1", "2"], ["3", "4"]]


def test_a_lone_candidate_wins_on_its_name_alone():
    """A merged (JP)/(US) approach carries the UNION of its two rows' ids,
    so an ids-equality test would reject both rows. Ids only ever DECIDE
    between same-name candidates."""
    rows = [_row(2, "Approach A (JP)", "approach", True, ids=("1",), version="jp")]
    payload = {"targets": [
        {"entity_key": "star:1:0", "label": "Some Star",
         "approaches": [{"name": "Approach A", "ids": ["1", "2"]}],
         "subsections": []}]}
    assert column_lines(rows, payload, lambda *a, **_: 4370) == ["43.70"]


def test_the_column_runs_to_the_sheets_last_data_row_blanks_and_all():
    """Round 20, his report: "it seems like it didn't paste over ALL rows in
    the spreadsheet... it should paste all the way to 803."

    The column always spans row 2 through the LAST row `read_rows` returns,
    trailing blanks included -- on the live sheet that is 803 lines ending at
    worksheet row 804, and its last 187 lines are blank because they are
    castle movements he holds no time for. Trimming them would shorten the
    column whenever the tail happens to be empty, which is a length that
    depends on his data rather than on the sheet."""
    rows = [
        _row(2, "Approach A", "approach", True, ids=("1",)),
        _row(9, "Nothing Here", "approach", True, ids=("1",)),
    ]
    payload = {"targets": [
        {"entity_key": "star:1:0", "label": "Some Star",
         "approaches": [{"name": "Approach A", "ids": ["1"]}],
         "subsections": []},
        {"entity_key": None, "label": "A Castle Movement",
         "approaches": [{"name": "Real Name", "ids": ["1"]}],
         "subsections": []},
    ]}
    lines = column_lines(rows, payload, lambda *a, **_: 4370)
    assert len(lines) == 8                      # rows 2..9, the last data row
    assert lines[0] == "43.70"
    assert lines[1:] == [""] * 7                # the tail survives being empty


def test_no_rows_is_no_lines():
    assert column_lines([], {"targets": []}, lambda *a, **_: None) == []


def _primary_fixture():
    """One star target whose rows are its OWN name and two named strategies,
    plus a subsection -- the shape 117 of the live sheet's 118 star targets
    have (measured 2026-09-02)."""
    rows = [
        _row(2, "Chip off Whomp's Block", "approach", True, ids=("1",)),
        _row(3, "Triple jump strat", "approach", False, ids=("2",)),
        _row(4, "Warp fadeout", "subsection", False, ids=("3",)),
    ]
    payload = {"targets": [
        {"entity_key": "star:2:0", "label": "Chip off Whomp's Block",
         "section": "2. Whomp's Fortress",
         "approaches": [
             {"name": "Chip off Whomp's Block", "ids": ["1"]},
             {"name": "Triple jump strat", "ids": ["2"]},
         ],
         "subsections": [{"name": "Warp fadeout", "ids": ["3"]}]},
    ]}
    return rows, payload


def test_a_star_own_row_asks_for_his_best_however_he_got_it():
    """ROUND 25, and the whole reason the column came back nearly empty. The
    export asked every row for the SHEET's strategy name, and his PBs are
    filed under the names HE picked -- measured through the real endpoint on
    his live database: 399 asks, 3 answers, the export wanting "Chip off
    Whomp's Block" where he had "Standard". A row that names the STAR is
    about the star, so it asks with no strategy at all; a row that names a
    strategy still means only times set that way."""
    rows, payload = _primary_fixture()
    asked = []

    def resolve(entity_key, strat_tag, timer_mode, version, **_):
        asked.append(strat_tag)
        return 4370 if strat_tag is None else None

    lines = column_lines(rows, payload, resolve)
    assert lines[0] == "43.70", "the star's own row must print his best"
    assert lines[1] == "", "a strategy row must not print a time set another way"
    # Round 27 put the row's OWN slot first and the blind ask second, so the
    # star's row asks TWICE when nothing is filed under its name -- which is
    # what lets an imported column round-trip without costing a played one.
    # A row named after the star IS that star's Standard strategy (his
    # rule, 2026-09-02), so its own slot is "Standard", never the star's
    # name; `adoptions.sheet_strategy` answers that for both doors.
    assert asked[:3] == ["Standard", None, "Triple jump strat"], asked


def test_a_strategy_row_still_only_prints_its_own_strategys_time():
    """The other half, and what stops round 25 becoming "print your best
    everywhere": a named strategy row asks for that name and nothing else,
    so a time set a different way can never appear under it."""
    rows, payload = _primary_fixture()

    def resolve(entity_key, strat_tag, timer_mode, version, **_):
        return 2060 if strat_tag == "Triple jump strat" else None

    lines = column_lines(rows, payload, resolve)
    assert lines[0] == ""
    assert lines[1] == "20.60"


def test_a_subsection_row_names_the_piece_so_it_asks_blind_too():
    """`adoptions.strategy_name` files a subsection under the default
    strategy for the IMPORT -- "the row names the piece being practised, not
    a way to perform that piece" -- and this door reads the same predicate
    rather than keeping a second copy of it, so both directions agree about
    which rows are about a thing and which are about a way of doing it."""
    rows, payload = _primary_fixture()
    asked = []

    def place(target, item, kind):
        return ("segment:9", "rta", "Standard") if kind == "subsection" else None

    def resolve(entity_key, strat_tag, timer_mode, version, **_):
        asked.append((entity_key, strat_tag))
        return 8100 if strat_tag is None else None

    lines = column_lines(rows, payload, resolve, place=place)
    assert lines[2] == "1:21.00"
    assert ("segment:9", None) in asked


def test_an_imported_row_wins_over_your_faster_time_on_the_star():
    """ROUND 27, and it is round 25's rule meeting his round-trip goal --
    "we need to be able to IMPORT a column, and be able to EXPORT that exact
    same column back into the sheet."

    Round 25 made a star's own row ask with NO strategy, so it printed your
    best on that star however you set it. For an IMPORTED column that broke
    the round trip: the import files a star row's time under that row's OWN
    name, and a blind ask answered with the runner's faster time from some
    other row instead -- measured through the real endpoints over the live
    sheet, 104 of one runner's 601 filled rows came back with the wrong
    number.

    Asking by NAME first settles both, and this is the case that tells them
    apart: a PB exists under the row's own slot -- "Standard", which is
    where the import files a star's own row -- AND a faster one exists
    under another. The row must print its own."""
    rows, payload = _primary_fixture()

    def resolve(entity_key, strat_tag, timer_mode, version, **_):
        if strat_tag == "Standard":
            return 4370                      # what the import landed here
        if strat_tag is None:
            return 2060                      # faster, set some other way
        return None

    lines = column_lines(rows, payload, resolve)
    assert lines[0] == "43.70", (
        "an imported star row must export the time it was imported with, not "
        "a faster time from another row")


def test_the_blind_fallback_still_carries_a_time_he_played():
    """The other half, unchanged from round 25: with NOTHING filed under the
    row's own name -- which is the ordinary case for times he PLAYED, since
    they are filed under the names HE picked -- the star's own row still
    prints his best on that star."""
    rows, payload = _primary_fixture()

    def resolve(entity_key, strat_tag, timer_mode, version, **_):
        return 2060 if strat_tag is None else None

    assert column_lines(rows, payload, resolve)[0] == "20.60"


def test_the_blind_ask_carries_only_what_no_other_row_claims():
    """Round 28. The star's own row used to ask strategy-blind and got the
    LATEST save on the star -- for an imported column, some sibling row's
    time, printed twice (THI's Tip Top: "No mountain clip" 20.90 on a row
    the runner left blank; the ten-runner sweep's whole `extra` column was
    this). Now the blind ask names what every OTHER row of the block claims,
    and the caller answers with the fastest of the rest -- which is exactly
    where a time filed under a name the sheet has no row for (his own picks)
    still lands."""
    rows, payload = _primary_fixture()
    asked = []

    def resolve(entity_key, strat_tag, timer_mode, version, excluding=()):
        asked.append((strat_tag, frozenset(excluding)))
        return None

    column_lines(rows, payload, resolve)
    assert (None, frozenset({"Triple jump strat"})) in asked, asked
    # A strategy row never asks blind, so it never carries the exclusion.
    assert all(excl == frozenset() for tag, excl in asked if tag is not None), asked


def test_a_held_cell_prints_where_nothing_else_answers_and_never_over_a_pb():
    """Round 28: a row the import could not place is HELD, and the export
    prints the hold back as written -- so a subsection nobody has linked, a
    castle movement with no entity, a stage RTA all round-trip. Only where
    nothing else answers: a row with a real personal best prints that."""
    rows, payload = _primary_fixture()
    from sm64_events.library.audit import row_key
    target = payload["targets"][0]
    piece_key = row_key(target, "Warp fadeout", ["3"])
    strat_key = row_key(target, "Triple jump strat", ["2"])
    cells = {(piece_key, None): 1613, (strat_key, None): 9999}

    def resolve(entity_key, strat_tag, timer_mode, version, **_):
        return 2060 if strat_tag == "Triple jump strat" else None

    lines = column_lines(rows, payload, resolve,
                         held=lambda key, version: cells.get((key, version)))
    assert lines[2] == "16.13", "an unplaced piece prints its held cell"
    assert lines[1] == "20.60", "a row with a PB never prints its stale hold"
    assert column_lines(rows, payload, resolve)[2] == "", (
        "without a held lookup the row stays blank, as before")


def test_a_cell_carries_the_platform_the_resolver_names():
    """Round 29 item 2: `resolve` may answer `(cs, platform)`, and the cell
    keeps the platform beside the text so the clipboard can colour it. A
    bare centisecond answer -- every stub in this file -- is a time with no
    stamp, an empty row names nothing, and `column_lines` is the texts."""
    from sm64_events.library.export_column import column_cells

    rows = [_row(2, "Approach A", "approach", True, ids=("1",))]
    payload = {"targets": [
        {"entity_key": "star:1:0", "label": "Some Star",
         "approaches": [{"name": "Approach A", "ids": ["1"]}],
         "subsections": []}]}
    stamped = column_cells(rows, payload, lambda *a, **_: (4370, "n64"))
    assert stamped[0] == {"text": "43.70", "platform": "n64"}
    bare = column_cells(rows, payload, lambda *a, **_: 4370)
    assert bare[0] == {"text": "43.70", "platform": None}
    silent = column_cells(rows, payload, lambda *a, **_: None)
    assert silent[0] == {"text": "", "platform": None}
    assert column_lines(rows, payload, lambda *a, **_: (4370, "emu")) == [
        cell["text"] for cell in stamped]
