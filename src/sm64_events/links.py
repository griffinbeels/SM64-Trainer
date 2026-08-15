"""Per-star external link registry (feature #9).

Ukikipedia links go through `ukikipedia_url`, which resolves a target's
identity against the wiki's OWN page list (`ukikipedia_titles.py`, a snapshot
`tools/scrape_ukikipedia.py` refreshes) and answers None for anything without
a page. Until 2026-08-15 the URL was generated from the star name and hoped
for -- which 404'd on every title the wiki spells differently ("Can the Eel
Come out to Play?") and on all nine secret-course stars (their star_name is
"8 Red Coins"). OVERRIDES holds hand-curated URLs (e.g. Ultimate Star
Spreadsheet deep links, which need a one-time manual gid/range harvest)."""
import re
import urllib.parse

from sm64_events.memory.addresses import COURSE_ABBREV, course_name, star_name  # noqa: F401
from sm64_events.ukikipedia_titles import TITLES

# COURSE_ABBREV moved to memory/addresses.py on 2026-08-03, when the practice
# card's step track needed the same fifteen strings to label a course chip.
# Re-exported here because these URL builders read it, and because it is the
# registry of what the world IS — the same argument node_key/node_label rest on.

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
# The Bowser courses' 8-red-coin star (star 0) — xcams files it under the same
# bow_ family as the No Reds / Battle pages, keyed "<n>r" (human-confirmed
# 2026-07-23). Inverse of the scraper's _BOWSER_REDS.
XCAMS_BOWSER_REDS = {16: "1r", 17: "2r", 18: "3r"}


def _xcams_star_key(course_id: int, star_id: int) -> str | None:
    if course_id in COURSE_ABBREV:                     # main courses 1-15
        return f"{COURSE_ABBREV[course_id].lower()}_{star_id + 1}"
    if course_id in XCAMS_SECRET:                       # Castle Secret Stars (VERIFY prefix)
        return XCAMS_SECRET[course_id]
    if course_id in XCAMS_BOWSER_REDS and star_id == 0:  # Bowser reds
        return f"bow_{XCAMS_BOWSER_REDS[course_id]}"
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


UKIKIPEDIA_WIKI = "https://ukikipedia.net/wiki/"
_RTA_PREFIX = "RTA Guide/"

# Where the wiki files things whose page is not the star's own name.
_BOWSER_COURSE = {16: "Bowser in the Dark World", 17: "Bowser in the Fire Sea",
                  18: "Bowser in the Sky"}
_BOWSER_COURSE_SEGMENT = {5: 16, 6: 17, 7: 18}       # the seeded course segments
_BOWSER_BATTLE_SEGMENTS = {8, 9, 10}                   # the seeded battle segments
_SECRET_COURSE_STARS = {19, 20, 21, 22, 23, 24}        # PSS, CotMC, TotWC, VCutM, WMotR, SA
# A movement is named by what it does, not by a star; these are the wiki
# pages the Ultimate Sheet's castle-movement labels can be recognised as.
# Substring match, case-insensitive, first hit wins. "Castle Movement" itself
# REDIRECTS to Lakitu Skip on the wiki (checked 2026-08-15), so there is no
# general castle-movement page to fall back on -- a lobby door-to-door row
# genuinely has no page, and gets no mark.
_MOVEMENT_ALIASES = (
    ("LBLJ", "Lobby Backwards Long Jump"),
    ("Lakitu skip", "Lakitu Skip"),
    ("MIPS Clip", "MIPS Clip"),           # before the rabbit himself
    ("MIPS", "MIPS"),
    ("CotMC", "Cavern of the Metal Cap"),
    ("DDD skip", "DDD skip"),
    ("Endless stairs", "50 Star Door and Endless Staircase BLJs"),
)
# A "(Toad)" movement is the detour for a Toad star; the wiki names each Toad
# by where he stands (page text, 2026-08-15): HMC Toad by the HMC entrance,
# Upstairs Toad by the TTM painting, Tippy Toad between TTC and RR. The
# course token in the label says which.
_TOAD_BY_COURSE = (("HMC", "HMC Toad"), ("TTM", "Upstairs Toad"),
                   ("TTC", "Tippy Toad"), ("RR", "Tippy Toad"))
_TOAD_LABEL = re.compile(r"\bToad\b")
_COURSE_TOKEN = re.compile(r"\b(HMC|TTM|TTC|RR)\b")
_COURSE_RTA_LABEL = re.compile(r"^([A-Za-z]{2,3}) RTA\b")
_ABBREV_TO_COURSE = {abbrev.lower(): cid for cid, abbrev in COURSE_ABBREV.items()}

# Titles differ by case on the wiki ("Shining Atop"/"Shining atop") and both
# exist; a case-folded lookup means our spelling need not match theirs.
_TITLE_BY_FOLD: dict[str, str] = {}
for _title in sorted(TITLES):
    _TITLE_BY_FOLD.setdefault(_title.casefold(), TITLES[_title])


def _existing(candidate: str) -> str | None:
    """The page a candidate RTA Guide title lands on, or None if the wiki
    has no such page."""
    return _TITLE_BY_FOLD.get((_RTA_PREFIX + candidate).casefold())


def _star_candidates(course_id: int, star_id: int) -> list[str]:
    if course_id in _BOWSER_COURSE:
        return [_BOWSER_COURSE[course_id]]
    if course_id in _SECRET_COURSE_STARS:
        return [course_name(course_id)]
    if course_id not in COURSE_ABBREV:
        return []
    abbrev = COURSE_ABBREV[course_id]
    if star_id == 6:
        return [f"{abbrev} 100 Coins"]
    name = star_name(course_id, star_id)
    # "Through the Jet Stream" is two stars; the wiki disambiguates by course.
    return [f"{name} ({abbrev})", name]


def _segment_candidates(segment_id: int) -> list[str]:
    if segment_id in _BOWSER_COURSE_SEGMENT:
        return [_BOWSER_COURSE[_BOWSER_COURSE_SEGMENT[segment_id]]]
    if segment_id in _BOWSER_BATTLE_SEGMENTS:
        return ["Bowser Battles"]
    return []


def _label_candidates(label: str) -> list[str]:
    hits = []
    # A course's own RTA row ("HMC RTA (... w/ CotMC)") is the course page,
    # whatever detour its parenthetical mentions -- so it is decided first.
    match = _COURSE_RTA_LABEL.match(label)
    if match and match.group(1).lower() in _ABBREV_TO_COURSE:
        hits.append(course_name(_ABBREV_TO_COURSE[match.group(1).lower()]))
    folded = label.casefold()
    hits += [page for needle, page in _MOVEMENT_ALIASES if needle.casefold() in folded]
    if _TOAD_LABEL.search(label):
        toads = dict(_TOAD_BY_COURSE)
        hits += [toads[token] for token in _COURSE_TOKEN.findall(label) if token in toads]
    return hits


def ukikipedia_page(entity_key: str | None, label: str | None = None) -> str | None:
    """The RTA Guide page title for a practiced thing, or None when the wiki
    has none. Identity first (a star's page is its star, whatever the sheet
    called the row), then what the label names (a movement, a course RTA)."""
    candidates: list[str] = []
    kind, _, rest = (entity_key or "").partition(":")
    if kind == "star":
        course, _, star = rest.partition(":")
        if course.isdigit() and star.isdigit():
            candidates += _star_candidates(int(course), int(star))
    elif kind == "segment" and rest.isdigit():
        candidates += _segment_candidates(int(rest))
    if label:
        candidates += _label_candidates(label)
    for candidate in candidates:
        page = _existing(candidate)
        if page:
            return page
    return None


def ukikipedia_url(entity_key: str | None, label: str | None = None) -> str | None:
    """Browser URL of `ukikipedia_page`, or None."""
    page = ukikipedia_page(entity_key, label)
    if page is None:
        return None
    return UKIKIPEDIA_WIKI + urllib.parse.quote(page.replace(" ", "_"),
                                                safe="/:!',()")


def star_links(course_id: int, star_id: int) -> dict:
    override = OVERRIDES.get((course_id, star_id), {})
    return {"ukikipedia": ukikipedia_url(f"star:{course_id}:{star_id}"),
            "example": override.get("example")}
