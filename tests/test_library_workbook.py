from library_fixture import GREY, build_workbook
from sm64_events.library import workbook as wb


def _sample():
    return build_workbook({
        wb.SHEET_MAIN: {
            (1, 1): {"text": "[1] Big Bob-omb on the Summit (JP)", "bold": True},
            (1, 7): {"text": "43.63", "link": "https://youtu.be/aaa"},
            (2, 1): {"text": "[1|2] Warp fadeout", "rgb": GREY},
            (2, 7): {"text": "15.90", "link": "https://x.com/i/status/1",
                     "link_kind": "formula"},
        },
        wb.SHEET_LOG: {(1, 1): {"text": "46238.84334791667"},
                       (2, 1): {"text": "46230.01018375"}},
    })


def test_reads_value_and_bold():
    cells = wb.read_sheet(_sample(), wb.SHEET_MAIN)
    assert cells[(1, 1)].value == "[1] Big Bob-omb on the Summit (JP)"
    assert cells[(1, 1)].bold is True
    assert cells[(2, 1)].bold is False


def test_reads_grey_font_colour():
    cells = wb.read_sheet(_sample(), wb.SHEET_MAIN)
    assert cells[(2, 1)].font_rgb == "FF434343"
    assert cells[(1, 1)].font_rgb == "FF000000"


def test_recovers_both_hyperlink_forms():
    cells = wb.read_sheet(_sample(), wb.SHEET_MAIN)
    assert cells[(1, 7)].link == "https://youtu.be/aaa"        # relationship
    assert cells[(2, 7)].link == "https://x.com/i/status/1"    # formula
    assert cells[(2, 7)].value == "15.90"


def test_log_revision_is_the_newest_entry():
    assert wb.log_revision(_sample()) == "2026-08-04T20:14:25"


def _renamed_log_sample():
    """The workbook as it stands on 2026-08-20: the revision tab split into
    `Log (Main)` / `Log (Extensions)`."""
    return build_workbook({
        wb.SHEET_MAIN: {(1, 1): {"text": "[1] Big Bob-omb on the Summit",
                                 "bold": True},
                        (1, 7): {"text": "43.63"}},
        "Log (Main)": {(1, 1): {"text": "46238.84334791667"},
                       (2, 1): {"text": "46230.01018375"}},
        "Log (Extensions)": {(1, 1): {"text": "46200.0"}},
    })


def test_sheet_names_reports_every_tab():
    assert wb.sheet_names(_renamed_log_sample()) == [
        wb.SHEET_MAIN, "Log (Main)", "Log (Extensions)"]


def test_the_revision_survives_the_log_tab_being_renamed():
    """The sheet renamed `Log` to `Log (Main)` under us, and `log_revision`
    RAISES rather than degrading -- so that one rename took down every path
    that reads the live sheet: the library refresh, tools/scrape_sheet.py and
    the sheet import alike (found 2026-08-20 by driving the real import)."""
    assert wb.log_revision(_renamed_log_sample()) == "2026-08-04T20:14:25"


def test_the_historical_name_still_wins_when_both_could_match():
    both = build_workbook({
        wb.SHEET_MAIN: {(1, 1): {"text": "[1] Star"}},
        wb.SHEET_LOG: {(1, 1): {"text": "46238.84334791667"}},
        "Log (Main)": {(1, 1): {"text": "46100.0"}},
    })
    assert wb.log_revision(both) == "2026-08-04T20:14:25"


def test_a_workbook_with_no_log_tab_says_what_it_DOES_have():
    """"no sheet named 'Log'" sends the reader hunting for a DELETED tab when
    the tab was only renamed. Naming what is present is what turns a dead end
    into a one-line fix."""
    import pytest
    no_log = build_workbook({wb.SHEET_MAIN: {(1, 1): {"text": "[1] Star"}}})
    with pytest.raises(LookupError) as raised:
        wb.log_revision(no_log)
    assert wb.SHEET_MAIN in str(raised.value)
