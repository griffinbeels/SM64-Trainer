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

    def resolve(entity_key, strat_tag, timer_mode, version):
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

    def resolve(entity_key, strat_tag, timer_mode, version):
        seen.append(version)
        return None                                    # the caller says "no match"

    lines = column_lines(rows, payload, resolve)
    assert lines == [""]
    assert seen == ["jp"]


def test_no_placer_means_anything_not_matched_by_strategy_stays_blank():
    rows, payload = _fixture()
    lines = column_lines(rows, payload, lambda *a: None)
    assert lines[1] == ""                               # Approach B needed a placer


def test_no_rows_is_no_lines():
    assert column_lines([], {"targets": []}, lambda *a: None) == []
