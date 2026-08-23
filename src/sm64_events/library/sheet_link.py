"""Importing from a link to somebody's own spreadsheet.

The task this exists for, in his words: *"They provide the link for their XCAM
logs (not necessarily the ultimate sheet), and then we identify WHICH sheet it
is, and then can extract a designated player from the sheet."*

So there are two shapes and the workbook itself says which:

  * an ULTIMATE-SHEET-shaped workbook — the real one, or a personal copy of it,
    identified by the `Ultimate Star Spreadsheet v2` tab. Read by the existing
    `library/sheet.py`, and a named runner's column extracted exactly as the
    bundled snapshot's is.
  * ANY OTHER GRID. Every tab's rows become lines and go through the same
    parser (`tracking/import_names.py`), so a personal sheet of `star | time | strat`
    needs no format of its own and nothing new to learn.

The second path is why this is small rather than a second importer: the name
resolver already answers "which star is this text naming", so a spreadsheet is
just lines somebody has not typed.

ONLY GOOGLE SHEETS LINKS ARE ACCEPTED, and that is a real restriction rather
than a convenience. The server fetches whatever URL it is handed, so an
unrestricted version would fetch from inside this machine's network on
somebody's say-so. A link that is not a Google Sheet is refused by name, which
also happens to be the honest answer to "we identify WHICH sheet it is" — we
identify the ones we can actually read.
"""
import re
from urllib.parse import urlparse

from sm64_events.library.workbook import (SHEET_MAIN, read_sheet, sheet_names)
from sm64_events.tracking.import_names import Unresolved, parse_block

# `https://docs.google.com/spreadsheets/d/<id>/edit#gid=0` and every other
# form of the same link. The id is the only part that matters.
_SHEET_ID = re.compile(r"/spreadsheets/d/(?:e/)?([A-Za-z0-9_-]{16,})")

# The one host this will fetch from. See the module docstring: the server does
# the fetching, so "any URL" means "any URL reachable from this machine".
ALLOWED_HOSTS = ("docs.google.com",)


def sheet_id_from(url: str) -> str:
    """The spreadsheet id in a Google Sheets link.

    Raises `ValueError` naming what IS supported — a link that silently
    imports nothing is indistinguishable from a sheet with no times in it."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") \
            or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(
            "that is not a Google Sheets link — paste the address from the "
            "sheet's own address bar (docs.google.com/spreadsheets/…)")
    match = _SHEET_ID.search(parsed.path)
    if not match:
        raise ValueError(
            "that link has no spreadsheet id in it — open the sheet and copy "
            "the address from the address bar")
    return match.group(1)


def export_url(sheet_id: str) -> str:
    """The .xlsx export, the same form `library/source.py` uses for the
    Ultimate Sheet — it is the only export that carries video links, and it is
    the one shape `library/workbook.py` can read."""
    return (f"https://docs.google.com/spreadsheets/d/{sheet_id}"
            "/export?format=xlsx")


def is_ultimate_shaped(data: bytes) -> bool:
    """Whether this workbook is the Ultimate Sheet, or somebody's copy of it.

    By the presence of its MAIN tab, not by the URL: a personal copy has a
    different id and the same structure, and reading it with the real reader
    is what makes "import from my own copy" work at all."""
    return SHEET_MAIN in sheet_names(data)


def grid_blocks(data: bytes):
    """`[(tab name, text)]` — every tab as lines the block parser can read.

    Each line is the row's non-empty cells joined by TABS, and the list is
    padded so line N of the block is row N of the sheet: an unreadable row has
    to be findable by the number the spreadsheet itself shows, or "row 47" is
    advice nobody can follow.
    """
    blocks = []
    for name in sheet_names(data):
        try:
            cells = read_sheet(data, name)
        except (LookupError, KeyError):
            continue                     # a tab with no worksheet part
        if not cells:
            continue
        rows: dict[int, list] = {}
        for (row, col), cell in sorted(cells.items()):
            value = (cell.value or "").strip()
            if value:
                rows.setdefault(row, []).append((col, value))
        if not rows:
            continue
        lines = []
        for number in range(1, max(rows) + 1):
            ordered = [value for _col, value in sorted(rows.get(number, []))]
            lines.append("\t".join(ordered))
        blocks.append((name, "\n".join(lines)))
    return blocks


def candidates_from_grid(data: bytes, catalog, timer_mode_for=None):
    """`([ImportCandidate, ...], [Unresolved, ...])` for a non-Ultimate sheet.

    Every tab is parsed on its own so line numbers stay the sheet's own row
    numbers, and each unreadable row is stamped with the tab it came from —
    `Times!14` is findable, `line 214` is not.
    """
    candidates, unresolved = [], []
    for name, block in grid_blocks(data):
        found, problems = parse_block(block, catalog,
                                      timer_mode_for=timer_mode_for)
        candidates.extend(found)
        unresolved.extend(
            Unresolved(item.line, f"{name}!{item.line}: {item.text}",
                       item.reason)
            for item in problems)
    return candidates, unresolved
