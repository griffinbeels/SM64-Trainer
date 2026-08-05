"""Where the Ultimate Sheet comes from.

One place, because two now need it: `tools/scrape_sheet.py` rebuilds the
bundled snapshot, and the server refreshes a user's copy on demand. A second
copy of this URL is a second thing to update when the sheet moves."""
import urllib.request

SHEET_ID = "1J20aivGnvLlAuyRIMMclIFUmrkHXUzgcDmYa31gdtCI"

# The .xlsx export is the only form carrying the video links -- CSV drops every
# one of them. ~5.6 MB, no auth (verified 2026-08-04).
FETCH_TIMEOUT_S = 180


def export_url(fmt: str = "xlsx") -> str:
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format={fmt}"


def fetch(timeout: float = FETCH_TIMEOUT_S) -> bytes:
    with urllib.request.urlopen(export_url(), timeout=timeout) as response:
        return response.read()
