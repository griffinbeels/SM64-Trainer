"""Per-star external link registry (feature #9).

Ukikipedia RTA-guide URLs are generated from the star name (spaces ->
underscores, punctuation kept — pattern live-confirmed 2026-06-10);
100-coin stars use the community course abbreviation (WF_100_Coins).
OVERRIDES holds hand-curated URLs (e.g. Ultimate Star Spreadsheet deep
links, which need a one-time manual gid/range harvest). Ukikipedia 403s
bot fetches: links are for the user's browser, never validated here."""
from sm64_events.memory.addresses import star_name

UKIKIPEDIA_RTA = "https://ukikipedia.net/wiki/RTA_Guide/"

COURSE_ABBREV = {
    1: "BoB", 2: "WF", 3: "JRB", 4: "CCM", 5: "BBH", 6: "HMC", 7: "LLL",
    8: "SSL", 9: "DDD", 10: "SL", 11: "WDW", 12: "TTM", 13: "THI",
    14: "TTC", 15: "RR",
}

# (course_id, star_id) -> {"example": url} — hand-curated additions.
OVERRIDES: dict[tuple[int, int], dict] = {}

# xcams "Daily Star" per-star page. URL pattern confirmed live (human, 2026-06-29):
#   .../home/history?star=<abbrev>_<xcams_star_id>
# <abbrev> = the lowercase course abbreviation (matches COURSE_ABBREV); for main
# courses <xcams_star_id> = trainer star_id + 1 (star:8:2 -> ssl_3). Secret stars
# and Bowser courses key off their own short codes (inverse of the scraper's
# _SECRET / _BOWSER maps). Movement segments (LBLJ etc.) have no xcams page.
XCAMS_HISTORY = "https://sm64-xcams.netlify.app/home/history"
XCAMS_SECRET = {19: "pss", 20: "mc", 21: "wc", 22: "vc", 23: "wmotr", 24: "aqua"}
XCAMS_BOWSER = {5: "1n", 6: "2n", 7: "3n", 8: "1x", 9: "2x", 10: "3x"}


def _xcams_star_key(course_id: int, star_id: int) -> str | None:
    if course_id in COURSE_ABBREV:                     # main courses 1-15
        return f"{COURSE_ABBREV[course_id].lower()}_{star_id + 1}"
    if course_id in XCAMS_SECRET:                       # Castle Secret Stars (VERIFY prefix)
        return XCAMS_SECRET[course_id]
    return None


def xcams_url(entity_key: str) -> str | None:
    """xcams Daily Star history page for a rank entity ("star:c:s" / "segment:id"),
    or None when it has no xcams page. Identity-driven (no seed field) so a wrong
    abbrev is a one-line fix here, never a re-scrape."""
    kind, _, rest = entity_key.partition(":")
    if kind == "star":
        course, _, star = rest.partition(":")
        if course.isdigit() and star.isdigit():
            key = _xcams_star_key(int(course), int(star))
            return f"{XCAMS_HISTORY}?star={key}" if key else None
    elif kind == "segment" and rest.isdigit() and int(rest) in XCAMS_BOWSER:
        return f"{XCAMS_HISTORY}?star=bow_{XCAMS_BOWSER[int(rest)]}"
    return None


def star_links(course_id: int, star_id: int) -> dict:
    if star_id == 6 and course_id in COURSE_ABBREV:
        page = f"{COURSE_ABBREV[course_id]}_100_Coins"
    else:
        page = star_name(course_id, star_id).replace(" ", "_")
    override = OVERRIDES.get((course_id, star_id), {})
    return {"ukikipedia": UKIKIPEDIA_RTA + page,
            "example": override.get("example")}
