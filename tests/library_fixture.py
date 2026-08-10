"""Builds a minimal Google-Sheets-shaped .xlsx in memory.

Hand-built rather than a trimmed copy of the real 5.6 MB workbook, because the
classification guard is proved by MUTATION -- restyling one row and watching
the parser refuse it -- and that is trivial against a builder and painful
against a binary fixture."""
import zipfile
from io import BytesIO

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


def build_workbook(sheets):
    """sheets: {name: {(row, col): cell}} where cell is a dict with keys
    `text` (str), optional `bold` (bool), `rgb` (font colour), `link` (url)
    and `link_kind` ("rel" | "formula", default "rel")."""
    fonts = [(False, BLACK), (True, BLACK), (False, GREY), (True, GREY)]
    font_xml = "".join(
        f'<font>{"<b/>" if b else ""}<color rgb="{c}"/></font>' for b, c in fonts)
    styles = ('<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://'
              'schemas.openxmlformats.org/spreadsheetml/2006/main">'
              f'<fonts count="{len(fonts)}">{font_xml}</fonts>'
              '<fills count="1"><fill><patternFill patternType="none"/></fill>'
              '</fills><borders count="1"><border/></borders>'
              f'<cellXfs count="{len(fonts)}">'
              + "".join(f'<xf fontId="{i}" fillId="0" borderId="0"/>'
                        for i in range(len(fonts)))
              + '</cellXfs></styleSheet>')

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/styles.xml", styles)
        sheet_tags, wb_rels = [], []
        for idx, name in enumerate(sheets, start=1):
            rid = f"rId{idx}"
            sheet_tags.append(f'<sheet name="{name}" sheetId="{idx}" r:id="{rid}"/>')
            wb_rels.append(
                f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org'
                f'/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{idx}.xml"/>')
            _write_sheet(z, idx, sheets[name])
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://'
                   'schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   f'{_NS_R}><sheets>{"".join(sheet_tags)}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns='
                   '"http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'{"".join(wb_rels)}</Relationships>')
    return buf.getvalue()


def _write_sheet(z, idx, cells):
    style_of = {(False, BLACK): 0, (True, BLACK): 1,
                (False, GREY): 2, (True, GREY): 3}
    rels, links, rows = [], [], {}
    for (row, col), cell in sorted(cells.items()):
        ref = f"{_col_letter(col)}{row}"
        style = style_of[(bool(cell.get("bold")), cell.get("rgb", BLACK))]
        text = cell["text"].replace("&", "&amp;").replace("<", "&lt;")
        url = cell.get("link")
        if url and cell.get("link_kind", "rel") == "formula":
            esc = url.replace("&", "&amp;")
            body = (f'<f>HYPERLINK(&quot;{esc}&quot;,&quot;{text}&quot;)</f>'
                    f'<v>{text}</v>')
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
