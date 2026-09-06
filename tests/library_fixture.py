"""Builds a minimal Google-Sheets-shaped .xlsx in memory.

Hand-built rather than a trimmed copy of the real 5.6 MB workbook, because the
classification guard is proved by MUTATION -- restyling one row and watching
the parser refuse it -- and that is trivial against a builder and painful
against a binary fixture."""
import zipfile
from io import BytesIO
from xml.sax.saxutils import escape

BLACK, GREY = "FF000000", "FF434343"

_CT = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.'
       'openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
       'package.relationships+xml"/><Default Extension="xml" ContentType='
       '"application/xml"/></Types>')
_ROOT_RELS = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns='
              '"http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org'
              '/officeDocument/2006/relationships/officeDocument" Target='
              '"xl/workbook.xml"/></Relationships>')
_NS_R = ('xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
         'relationships"')


def _col_letter(col):
    out = ""
    while col:
        col, rem = divmod(col - 1, 26)
        out = chr(65 + rem) + out
    return out


# A theme palette in the live export's own shape (Google's accents), so a
# fixture cell may carry `fill: "theme:8"` and resolve the way Raisn's N64
# cells do (accent5, FF6D01).
THEME_ACCENTS = {"dk1": "000000", "lt1": "FFFFFF", "dk2": "000000", "lt2": "FFFFFF",
                 "accent1": "4285F4", "accent2": "EA4335", "accent3": "FBBC04",
                 "accent4": "34A853", "accent5": "FF6D01", "accent6": "46BDC6",
                 "hlink": "1155CC", "folHlink": "1155CC"}


def _fill_xml(fill):
    if fill is None:
        return '<fill><patternFill patternType="none"/></fill>'
    if fill.startswith("theme:"):
        return (f'<fill><patternFill patternType="solid"><fgColor theme="{fill[6:]}"/>'
                f'</patternFill></fill>')
    return (f'<fill><patternFill patternType="solid"><fgColor rgb="{fill}"/>'
            f'<bgColor rgb="{fill}"/></patternFill></fill>')


def _styles_xml(cells_by_sheet):
    """One cellXfs row per (bold, font rgb, fill) combination the cells use;
    returns `(styles xml, {(bold, rgb, fill): xf index})`."""
    fonts = [(False, BLACK), (True, BLACK), (False, GREY), (True, GREY)]
    combos = [(b, c, None) for b, c in fonts]
    for cells in cells_by_sheet.values():
        for cell in cells.values():
            combo = (bool(cell.get("bold")), cell.get("rgb", BLACK), cell.get("fill"))
            if combo not in combos:
                combos.append(combo)
    fills = [None] + sorted({fill for _b, _c, fill in combos if fill})
    font_xml = "".join(
        f'<font>{"<b/>" if b else ""}<color rgb="{c}"/></font>' for b, c in fonts)
    xf_xml = "".join(
        f'<xf fontId="{fonts.index((b, c))}" fillId="{fills.index(fill)}" borderId="0"/>'
        for b, c, fill in combos)
    styles = ('<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://'
              'schemas.openxmlformats.org/spreadsheetml/2006/main">'
              f'<fonts count="{len(fonts)}">{font_xml}</fonts>'
              f'<fills count="{len(fills)}">{"".join(_fill_xml(f) for f in fills)}</fills>'
              '<borders count="1"><border/></borders>'
              f'<cellXfs count="{len(combos)}">{xf_xml}</cellXfs></styleSheet>')
    return styles, {combo: index for index, combo in enumerate(combos)}


_THEME_XML = ('<?xml version="1.0" encoding="UTF-8"?><a:theme xmlns:a="http://'
              'schemas.openxmlformats.org/drawingml/2006/main" name="Sheets">'
              '<a:themeElements><a:clrScheme name="Sheets">'
              + "".join(f'<a:{name}><a:srgbClr val="{rgb}"/></a:{name}>'
                        for name, rgb in THEME_ACCENTS.items())
              + '</a:clrScheme></a:themeElements></a:theme>')


def build_workbook(sheets):
    """sheets: {name: {(row, col): cell}} where cell is a dict with keys
    `text` (str), optional `bold` (bool), `rgb` (font colour), `link` (url),
    `link_kind` ("rel" | "formula", default "rel") and `fill` (a background:
    eight hex digits like the export writes, or "theme:N")."""
    styles, style_of = _styles_xml(sheets)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/theme/theme1.xml", _THEME_XML)
        sheet_tags, wb_rels = [], []
        for idx, name in enumerate(sheets, start=1):
            rid = f"rId{idx}"
            sheet_tags.append(f'<sheet name="{name}" sheetId="{idx}" r:id="{rid}"/>')
            wb_rels.append(
                f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org'
                f'/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{idx}.xml"/>')
            _write_sheet(z, idx, sheets[name], style_of)
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://'
                   'schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   f'{_NS_R}><sheets>{"".join(sheet_tags)}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns='
                   '"http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'{"".join(wb_rels)}</Relationships>')
    return buf.getvalue()


def _write_sheet(z, idx, cells, style_of):
    rels, links, rows = [], [], {}
    for (row, col), cell in sorted(cells.items()):
        ref = f"{_col_letter(col)}{row}"
        style = style_of[(bool(cell.get("bold")), cell.get("rgb", BLACK), cell.get("fill"))]
        text = cell["text"].replace("&", "&amp;").replace("<", "&lt;")
        url = cell.get("link")
        if url and cell.get("link_kind", "rel") == "formula":
            target = url.replace('"', '""')
            label = cell["text"].replace('"', '""')
            formula = escape(f'HYPERLINK("{target}","{label}")', {'"': "&quot;"})
            body = f'<f>{formula}</f><v>{text}</v>'
            xml = f'<c r="{ref}" s="{style}" t="str">{body}</c>'
        else:
            xml = (f'<c r="{ref}" s="{style}" t="inlineStr">'
                   f'<is><t>{text}</t></is></c>')
            if url:
                rid = f"hl{len(rels) + 1}"
                rels.append(f'<Relationship Id="{rid}" Type="http://schemas.'
                            f'openxmlformats.org/officeDocument/2006/relationships'
                            f'/hyperlink" Target="{url.replace("&", "&amp;")}" '
                            f'TargetMode="External"/>')
                links.append(f'<hyperlink ref="{ref}" r:id="{rid}"/>')
        rows.setdefault(row, []).append(xml)
    body = "".join(f'<row r="{r}">{"".join(cs)}</row>'
                   for r, cs in sorted(rows.items()))
    hyperlinks = f'<hyperlinks>{"".join(links)}</hyperlinks>' if links else ""
    z.writestr(f"xl/worksheets/sheet{idx}.xml",
               '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://'
               'schemas.openxmlformats.org/spreadsheetml/2006/main" '
               f'{_NS_R}><sheetData>{body}</sheetData>{hyperlinks}</worksheet>')
    if rels:
        z.writestr(f"xl/worksheets/_rels/sheet{idx}.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns='
                   '"http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'{"".join(rels)}</Relationships>')
