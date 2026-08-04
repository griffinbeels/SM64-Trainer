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
