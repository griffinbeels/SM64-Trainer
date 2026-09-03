"""Where the Ultimate Sheet comes from.

One place, because two now need it: `tools/scrape_sheet.py` rebuilds the
bundled snapshot, and the server refreshes a user's copy on demand. A second
copy of this URL is a second thing to update when the sheet moves.

**Testing reads his COPY, never the live document** -- `SM64_SHEET_ID`
overrides the id, and `TEST_SHEET_ID` below is the copy he keeps for exactly
this. The reason is not politeness: he was BANNED from the live sheet for
leaving a column in it (2026-09-02), and while this app only ever READS, a
test loop that ends in a paste does not. Anything that round-trips a column
points here. `tools/roundtrip_sheet.py` needs no sheet at all -- it imports
and exports in memory -- but a loop that ever does needs this override."""
import os
import urllib.request

LIVE_SHEET_ID = "1J20aivGnvLlAuyRIMMclIFUmrkHXUzgcDmYa31gdtCI"
# His own copy, world-readable, kept in sync by copying the "Ultimate Star
# Spreadsheet v2" tab across. Not the live document, and good enough to prove
# a round trip against.
TEST_SHEET_ID = "1B5eldvNTk7LoIZAVKSh9ovt4m38lz3QvzDqqljLZiKE"

SHEET_ID = os.environ.get("SM64_SHEET_ID") or LIVE_SHEET_ID

# The .xlsx export is the only form carrying the video links -- CSV drops every
# one of them. ~5.6 MB, no auth (verified 2026-08-04).
FETCH_TIMEOUT_S = 180


def export_url(fmt: str = "xlsx") -> str:
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format={fmt}"


def fetch(timeout: float = FETCH_TIMEOUT_S) -> bytes:
    with urllib.request.urlopen(export_url(), timeout=timeout) as response:
        return response.read()
