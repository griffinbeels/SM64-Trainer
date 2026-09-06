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


def test_formula_recording_quotes_and_xml_entities_roundtrip_with_styling():
    """A Sheets HYPERLINK formula escapes quotes before XML escapes entities.

    Literal entity-looking URL text must decode only once: `&quot;` inside
    the URL is not a formula delimiter, and `&amp;` stays literal text.
    """
    import io
    import zipfile

    url = 'https://example.com/"a""b"?x=1&literal=&quot;&other=&amp;'
    data = build_workbook({wb.SHEET_MAIN: {
        (3, 7): {"text": "15.90", "link": url, "link_kind": "formula",
                 "fill": "FFA5A9F1", "rgb": GREY}}})
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert '&quot;&quot;a&quot;&quot;&quot;&quot;b&quot;&quot;' in xml
    assert '&amp;literal=&amp;quot;' in xml
    cell = wb.read_sheet(data, wb.SHEET_MAIN)[(3, 7)]
    assert cell.link == url
    assert cell.value == "15.90"
    assert cell.fill_rgb == "A5A9F1"
    assert cell.font_rgb == GREY


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


def test_reads_a_cells_fill_in_both_forms_the_export_writes():
    """Round 29 item 2: a runner colours a time by the machine that set it,
    and the fill is where the sheet keeps that. The live export writes a
    fill's colour two ways -- an explicit rgb (257 of 263 fills, alpha first)
    and a theme index (6 fills; Raisn's N64 cells are theme 8 = accent5,
    Sheets' orange FF6D01) -- and a cell with no fill reads None, never a
    white that would match nothing."""
    from library_fixture import THEME_ACCENTS

    data = build_workbook({
        wb.SHEET_MAIN: {
            (1, 7): {"text": "Emu", "fill": "FFA5A9F1"},
            (2, 7): {"text": "N64", "fill": "theme:8"},
            (3, 7): {"text": "43.63"},
        },
        wb.SHEET_LOG: {(1, 1): {"text": "46238.5"}},
    })
    cells = wb.read_sheet(data, wb.SHEET_MAIN)
    assert cells[(1, 7)].fill_rgb == "A5A9F1"
    assert cells[(2, 7)].fill_rgb == THEME_ACCENTS["accent5"] == "FF6D01"
    assert cells[(3, 7)].fill_rgb is None
