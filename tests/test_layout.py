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
