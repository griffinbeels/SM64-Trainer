"""Importing from a link to somebody's own spreadsheet."""
import pytest
from library_fixture import build_workbook

from sm64_events.library import sheet_link
from sm64_events.library import workbook as wb
from sm64_events.tracking.import_names import star_catalog


# -- the link ----------------------------------------------------------------

def test_every_form_of_a_google_sheets_link():
    for url in (
        "https://docs.google.com/spreadsheets/d/1J20aivGnvLlAuyRIMMclIFUmrk/edit",
        "https://docs.google.com/spreadsheets/d/1J20aivGnvLlAuyRIMMclIFUmrk/edit#gid=0",
        "http://docs.google.com/spreadsheets/d/1J20aivGnvLlAuyRIMMclIFUmrk/view",
        "  https://docs.google.com/spreadsheets/d/1J20aivGnvLlAuyRIMMclIFUmrk/  ",
    ):
        assert sheet_link.sheet_id_from(url) == "1J20aivGnvLlAuyRIMMclIFUmrk"


def test_a_published_link_resolves_too():
    assert sheet_link.sheet_id_from(
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vQabcdefghijklmn/pubhtml"
    ) == "2PACX-1vQabcdefghijklmn"


def test_only_google_sheets_is_fetched_and_the_refusal_says_so():
    """The SERVER does the fetching, so "any URL" means "any URL reachable
    from this machine" — an unrestricted version would fetch from inside the
    network on somebody's say-so."""
    for url in ("http://127.0.0.1:8065/api/session",
                "http://169.254.169.254/latest/meta-data/",
                "file:///c:/Windows/win.ini",
                "https://example.com/spreadsheets/d/abcdefghijklmnop/edit"):
        with pytest.raises(ValueError, match="Google Sheets link"):
            sheet_link.sheet_id_from(url)


def test_a_google_link_that_is_not_a_sheet_is_refused_by_name():
    with pytest.raises(ValueError, match="no spreadsheet id"):
        sheet_link.sheet_id_from("https://docs.google.com/document/d/abc/edit")


def test_the_export_is_the_xlsx_form():
    assert sheet_link.export_url("abc").endswith("/export?format=xlsx")


# -- identifying the workbook ------------------------------------------------

def personal_sheet():
    """Somebody's own xcam log: a couple of tabs of `star | time | strat`."""
    return build_workbook({
        "Times": {
            (1, 1): {"text": "Star"}, (1, 2): {"text": "Time"},
            (2, 1): {"text": "BoB 1"}, (2, 2): {"text": "0:23.57"},
            (3, 1): {"text": "WF 6"}, (3, 2): {"text": "8.86"},
            (3, 3): {"text": "LJ"},
            (5, 1): {"text": "Chungus Skip"}, (5, 2): {"text": "12.00"},
        },
        "Notes": {(1, 1): {"text": "remember to breathe"}},
    })


def ultimate_shaped():
    return build_workbook({
        wb.SHEET_MAIN: {(1, 1): {"text": "[1] Big Bob-omb on the Summit"},
                        (1, 7): {"text": "43.63"}},
        "Log (Main)": {(1, 1): {"text": "46238.84334791667"}},
    })


def test_a_copy_of_the_ultimate_sheet_is_recognised_by_its_own_tab():
    """By STRUCTURE, not by URL: a personal copy has a different id and the
    same shape, and reading it with the real reader is what makes "import
    from my own copy" work at all."""
    assert sheet_link.is_ultimate_shaped(ultimate_shaped())
    assert not sheet_link.is_ultimate_shaped(personal_sheet())


# -- reading an ordinary grid ------------------------------------------------

def test_every_tab_becomes_lines_the_paste_parser_can_read():
    blocks = dict(sheet_link.grid_blocks(personal_sheet()))
    assert set(blocks) == {"Times", "Notes"}
    assert blocks["Times"].splitlines()[1] == "BoB 1\t0:23.57"


def test_a_line_keeps_the_row_number_the_spreadsheet_shows():
    """"Row 47" is advice somebody can follow; "line 214" is not."""
    lines = dict(sheet_link.grid_blocks(personal_sheet()))["Times"].splitlines()
    assert lines[0] == "Star\tTime"       # row 1
    assert lines[3] == ""                 # row 4 really is empty
    assert lines[4].startswith("Chungus Skip")   # row 5


def test_a_personal_grid_lands_its_times():
    candidates, _ = sheet_link.candidates_from_grid(
        personal_sheet(), star_catalog())
    assert [(c.entity_key, c.time_cs, c.strat_tag) for c in candidates] == [
        ("star:1:0", 2357, ""), ("star:2:5", 886, "LJ")]


def test_an_unreadable_row_is_stamped_with_its_tab_and_its_row():
    _, unresolved = sheet_link.candidates_from_grid(
        personal_sheet(), star_catalog())
    texts = [item.text for item in unresolved]
    assert any(text.startswith("Times!5: Chungus Skip") for text in texts), texts
    assert any(text.startswith("Notes!1:") for text in texts), texts


def test_a_header_row_is_reported_rather_than_guessed_at():
    """`Star  Time` names no star and carries no time, so it comes back as a
    line nobody could read — which is honest. Silently skipping anything that
    looks like a header is how a real row gets skipped too."""
    _, unresolved = sheet_link.candidates_from_grid(
        personal_sheet(), star_catalog())
    assert any(item.text.startswith("Times!1: Star") for item in unresolved)
