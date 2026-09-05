"""Reads the Google Sheets .xlsx export with the standard library only.

No openpyxl on purpose: the server owns this parse from phase 3, so any
dependency here ships in the exe -- and the videos live in TWO cell forms
(relationship hyperlinks and inline HYPERLINK() formulas) which openpyxl can
only reach across two loads of a 10 MB sheet, because its formula and
cached-value modes are exclusive.

Measured on the live workbook 2026-08-04: 3,833 relationship hyperlinks and
3,164 HYPERLINK() formulas. Dropping either form silently halves the library.
"""
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta

SHEET_MAIN = "Ultimate Star Spreadsheet v2"
SHEET_LOG = "Log"
# The revision tab, newest name LAST so an older workbook still reads. The
# sheet split it into `Log (Main)` / `Log (Extensions)` some time before
# 2026-08-20; see `log_revision` for what that cost.
LOG_TABS = (SHEET_LOG, "Log (Main)")

# Excel's serial-date epoch. Google exports the Log tab's timestamps as serial
# days; the sheet's own timezone is unstated, so these ORDER revisions and are
# never presented as a wall clock.
_EPOCH = datetime(1899, 12, 30)

_CELL = re.compile(r'<c r="([A-Z]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)
_HYPERLINK_FORMULA = re.compile(r"HYPERLINK\(&quot;(.*?)&quot;", re.I)


@dataclass(frozen=True)
class Cell:
    row: int
    col: int
    value: str
    bold: bool
    font_rgb: str | None
    link: str | None
    # The cell's background as six hex digits (RRGGBB, alpha dropped), None
    # for no fill. Round 29 item 2: a runner may colour a time by the machine
    # that set it, and the fill is the only place the sheet keeps that.
    fill_rgb: str | None = None


def _col_index(letters: str) -> int:
    out = 0
    for ch in letters:
        out = out * 26 + (ord(ch) - 64)
    return out


def _unescape(text: str) -> str:
    return (text.replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&apos;", "'")
                .replace("&amp;", "&"))


# Excel's `theme="N"` colour index order. The scheme is written dk1, lt1,
# dk2, lt2, accent1..6, hlink, folHlink, but the INDEX swaps each of the first
# two pairs -- 0 is lt1 and 1 is dk1 -- which is why a hand-rolled "read them
# in file order" resolves every theme fill to the wrong colour. Measured on the
# live export 2026-09-04: Raisn's N64 cells are `theme="8"`, accent5, FF6D01
# (Sheets' orange), which is what his screenshot shows.
_THEME_ORDER = ("lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3",
                "accent4", "accent5", "accent6", "hlink", "folHlink")


def _theme_palette(zf: zipfile.ZipFile) -> list:
    """RRGGBB per theme index, or [] for a workbook with no theme part."""
    names = [name for name in zf.namelist() if name.startswith("xl/theme/")]
    if not names:
        return []
    theme = zf.read(names[0]).decode("utf-8")
    scheme = re.search(r"<a:clrScheme[^>]*>(.*?)</a:clrScheme>", theme, re.S)
    if not scheme:
        return []
    by_name = {}
    for name, body in re.findall(
            r"<a:(dk1|lt1|dk2|lt2|accent\d|hlink|folHlink)>(.*?)</a:\1>",
            scheme.group(1), re.S):
        hit = re.search(r'(?:srgbClr val|sysClr[^>]*lastClr)="([0-9A-Fa-f]{6})"', body)
        by_name[name] = hit.group(1).upper() if hit else None
    return [by_name.get(name) for name in _THEME_ORDER]


def _fill_rgb(fill_xml: str, palette: list) -> str | None:
    """A fill's colour as RRGGBB: an explicit `rgb` (alpha dropped) or a
    `theme` index through the palette; None for no fill or a form this does
    not read (the live export uses exactly those two -- 257 rgb, 6 theme)."""
    if 'patternType="none"' in fill_xml:
        return None
    explicit = re.search(r'<fgColor rgb="([0-9A-Fa-f]{8})"', fill_xml)
    if explicit:
        return explicit.group(1).upper()[-6:]
    themed = re.search(r'<fgColor theme="(\d+)"', fill_xml)
    if themed and int(themed.group(1)) < len(palette):
        return palette[int(themed.group(1))]
    return None


def _style_by_xf(styles_xml: str, palette: list = ()) -> dict:
    """cellXfs index -> (bold, font rgb, fill rgb)."""
    fonts = re.findall(r"<font>(.*?)</font>", styles_xml, re.S)
    fills = re.findall(r"<fill>(.*?)</fill>", styles_xml, re.S)
    block = re.search(r"<cellXfs[^>]*>(.*?)</cellXfs>", styles_xml, re.S)
    xfs = re.findall(r"<xf [^>]*/>|<xf [^>]*>.*?</xf>",
                     block.group(1) if block else "", re.S)
    out = {}
    for index, xf in enumerate(xfs):
        ref = re.search(r'fontId="(\d+)"', xf)
        font = fonts[int(ref.group(1))] if ref and int(ref.group(1)) < len(fonts) else ""
        rgb = re.search(r'<color rgb="([0-9A-Fa-f]{8})"', font)
        fill_ref = re.search(r'fillId="(\d+)"', xf)
        fill = (fills[int(fill_ref.group(1))]
                if fill_ref and int(fill_ref.group(1)) < len(fills) else "")
        out[index] = ("<b/>" in font, rgb.group(1).upper() if rgb else None,
                      _fill_rgb(fill, list(palette)) if fill else None)
    return out


def _font_by_style(styles_xml: str) -> dict:
    """cellXfs index -> (bold, font rgb) -- `_style_by_xf` without the fill,
    kept for the callers that only ever asked about the font."""
    return {index: (bold, rgb) for index, (bold, rgb, _fill)
            in _style_by_xf(styles_xml).items()}


def _sheet_part(zf: zipfile.ZipFile, sheet_name: str) -> str:
    rels = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"',
                           zf.read("xl/_rels/workbook.xml.rels").decode("utf-8")))
    book = zf.read("xl/workbook.xml").decode("utf-8")
    # Attribute ORDER varies: the live export writes `state="visible"` before
    # `name=`, so a regex anchoring name to the tag opener finds no sheets at
    # all. Same trap as the <hyperlink> tags below.
    for tag in re.findall(r"<sheet [^>]*/>", book):
        name = re.search(r'name="([^"]*)"', tag)
        rid = re.search(r'r:id="([^"]+)"', tag)
        if name and rid and _unescape(name.group(1)) == sheet_name:
            target = rels[rid.group(1)].lstrip("/")
            return target if target.startswith("xl/") else "xl/" + target
    raise LookupError(f"no sheet named {sheet_name!r} in the workbook")


def sheet_names(data: bytes) -> list[str]:
    """Every tab in the workbook, in book order. Answers "what is it called
    NOW" without a second parse for a caller that has to guess."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        book = zf.read("xl/workbook.xml").decode("utf-8")
    return [_unescape(match) for match in
            re.findall(r'<sheet\b[^>]*?\bname="([^"]*)"', book)]


def _first_present(data: bytes, candidates) -> str:
    """The first candidate tab the workbook actually has.

    Raises naming what IS there, because "no sheet named 'Log'" sends the
    reader looking for a deleted tab when the tab was only renamed."""
    present = sheet_names(data)
    for candidate in candidates:
        if candidate in present:
            return candidate
    raise LookupError(
        f"none of {list(candidates)} is a tab in this workbook; it has "
        f"{present}")


def _read_optional(zf: zipfile.ZipFile, name: str) -> str | None:
    try:
        return zf.read(name).decode("utf-8")
    except KeyError:
        return None


def _relationship_links(sheet_xml: str, rels: dict) -> dict:
    """{cell ref: url} for <hyperlink> elements, whose attribute ORDER varies
    between exporters -- Google emits r:id first, the spec's own examples emit
    ref first, and matching only one silently loses every link."""
    out = {}
    for tag in re.findall(r"<hyperlink [^>]*/>", sheet_xml):
        ref = re.search(r'ref="([A-Z]+\d+)"', tag)
        rid = re.search(r'r:id="([^"]+)"', tag)
        if ref and rid and rid.group(1) in rels:
            out[ref.group(1)] = _unescape(rels[rid.group(1)])
    return out


def read_sheet(data: bytes, sheet_name: str) -> dict:
    """{(row, col): Cell} for one worksheet, 1-based on both axes."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        part = _sheet_part(zf, sheet_name)
        sheet_xml = zf.read(part).decode("utf-8")
        styles_xml = _read_optional(zf, "xl/styles.xml") or ""
        palette = _theme_palette(zf)
        shared_xml = _read_optional(zf, "xl/sharedStrings.xml")
        rels_name = part.replace("xl/worksheets/", "xl/worksheets/_rels/") + ".rels"
        rels_xml = _read_optional(zf, rels_name)

    styles = _style_by_xf(styles_xml, palette)
    shared = ([_unescape(re.sub(r"<[^>]+>", "", si))
               for si in re.findall(r"<si>(.*?)</si>", shared_xml, re.S)]
              if shared_xml else [])
    rels = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels_xml)) if rels_xml else {}
    links = _relationship_links(sheet_xml, rels)

    out = {}
    for match in _CELL.finditer(sheet_xml):
        letters, row, attrs, body = match.group(1), int(match.group(2)), match.group(3), match.group(4) or ""
        style = re.search(r's="(\d+)"', attrs)
        bold, rgb, fill = (styles.get(int(style.group(1)), (False, None, None))
                           if style else (False, None, None))

        link = links.get(f"{letters}{row}")
        formula = re.search(r"<f>(.*?)</f>", body, re.S)
        if link is None and formula:
            hit = _HYPERLINK_FORMULA.search(formula.group(1))
            if hit:
                link = _unescape(hit.group(1))

        inline = re.search(r"<is>.*?<t[^>]*>(.*?)</t>", body, re.S)
        cached = re.search(r"<v>(.*?)</v>", body, re.S)
        if inline:
            value = _unescape(inline.group(1))
        elif cached and 't="s"' in attrs:
            index = int(cached.group(1))
            value = shared[index] if index < len(shared) else ""
        elif cached:
            value = _unescape(cached.group(1))
        else:
            value = ""
        out[(row, _col_index(letters))] = Cell(row, _col_index(letters), value,
                                               bold, rgb, link, fill)
    return out


def log_revision(data: bytes) -> str:
    """ISO-8601 of the newest Log entry -- the sheet's OWN revision clock.

    This is what lets two machines that fetched at different moments agree on
    which snapshot is newer, which our own fetch time cannot answer.

    The tab is named by CANDIDATE, not by one literal, because it has already
    been renamed once under us: on 2026-08-20 the workbook carried
    `Log (Main)` and `Log (Extensions)` where it had carried `Log`, and since
    this raises rather than degrades, that rename took the whole refresh path
    down -- `POST /api/library/refresh`, `tools/scrape_sheet.py` and the sheet
    import alike -- with an error naming a missing tab rather than a moved
    one. Order is preference: the historical name first so an older workbook
    still reads, then the split form."""
    serials = []
    for (_, col), cell in read_sheet(data, _first_present(data, LOG_TABS)).items():
        if col != 1:
            continue
        try:
            serials.append(float(cell.value))
        except ValueError:
            continue
    if not serials:
        raise LookupError("the Log tab carries no serial timestamps")
    return (_EPOCH + timedelta(days=max(serials))).replace(microsecond=0).isoformat()
