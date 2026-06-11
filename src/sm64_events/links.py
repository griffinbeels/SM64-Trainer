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


def star_links(course_id: int, star_id: int) -> dict:
    if star_id == 6 and course_id in COURSE_ABBREV:
        page = f"{COURSE_ABBREV[course_id]}_100_Coins"
    else:
        page = star_name(course_id, star_id).replace(" ", "_")
    override = OVERRIDES.get((course_id, star_id), {})
    return {"ukikipedia": UKIKIPEDIA_RTA + page,
            "example": override.get("example")}
