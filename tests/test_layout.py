"""The layout contract: one row per field, one derivation per row, one door."""
import dataclasses

import pytest

from sm64_events.memory import layout as L


def test_every_row_is_a_field_and_every_field_is_a_row():
    fields = {f.name for f in dataclasses.fields(L.Layout)} - {"version"}
    rows = {row.field for row in L.LAYOUT_ROWS}
    assert fields == rows


def test_every_row_has_exactly_one_derivation():
    for row in L.LAYOUT_ROWS:
        assert (row.symbol is None) != (row.hunt is None), row.field
        assert row.note, row.field


def test_layout_for_answers_both_versions_and_refuses_others():
    assert L.layout_for("us") is L.US
    assert L.layout_for("jp") is L.JP
    with pytest.raises(KeyError):
        L.layout_for("eu")


def test_us_is_complete_and_jp_starts_empty():
    assert L.US.missing() == ()
    assert set(L.JP.missing()) == {row.field for row in L.LAYOUT_ROWS}


def test_require_names_the_missing_field():
    with pytest.raises(L.LayoutIncomplete, match="global_timer"):
        L.JP.require("global_timer", "curr_level")
    L.US.require("global_timer")            # no raise


def test_version_from_argv_reads_the_flag_and_refuses_an_unknown_version():
    assert L.version_from_argv(["--version", "jp"]) == "jp"
    assert L.version_from_argv(["--version=jp"]) == "jp"
    assert L.version_from_argv([]) == "us"
    with pytest.raises(SystemExit):
        L.version_from_argv(["--version", "usa"])


def test_the_controller_has_a_us_address_and_no_jp_one_yet():
    assert L.US.player1_controller is not None
    assert L.JP.player1_controller is None, (
        "a JP value lands only after its gate is verified and the sync "
        "report says so")


def test_the_button_table_covers_every_n64_button_exactly_once():
    from sm64_events.memory import addresses as A
    names = [name for _bit, name in A.BUTTON_BITS]
    assert len(names) == len(set(names)) == 14
    covered = 0
    for bit, _name in A.BUTTON_BITS:
        assert bin(bit).count("1") == 1, f"{bit:#06x} is not a single bit"
        covered |= bit
    # 0x0080 (the console reset line) and 0x0040 (unused) are never set by a
    # controller, so the table's coverage IS the validity mask.
    assert covered == A.BUTTON_VALID_MASK
