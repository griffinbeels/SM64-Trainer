"""A sheet target's name -> a trainer entity key, or nothing.

The one place a sheet label becomes `star:c:s` or `segment:id`. Its MISSES are
part of the output, not an error path: the sheet's Castle Movements are
micro-optimisations at a granularity we do not model (145 rows against our 63
segments), and a stage RTA row times a whole course, which is not a target
here. Both are expected; anything else is reported so a mapping hole shows up
as a number instead of silence.

Several sheet targets legitimately share one entity. Every `+ 100c` row is a
run of that course's 100-COIN star ending on the star it names -- which is
exactly the exit-star variant model `ranks/standards.py` already carries -- so
CCM's four such rows all map to `star:4:6`. The builder keeps them apart as
separate approaches; only the entity is shared."""
import re

from sm64_events.memory.addresses import course_name, star_count, star_name

MAIN_COURSES = range(1, 16)
SECRET_COURSES = (19, 20, 21, 22, 23, 24)
# (course, "Course" segment, "Battle" segment) -- the pipe-entry and fight
# segments, matching tools/scrape_ranks.py's own Bowser mapping.
BOWSER_COURSES = ((16, 5, 8), (17, 6, 9), (18, 7, 10))

# The 100-coin star is star 6 on every main course (addresses.star_count).
HUNDRED_COIN_STAR = 6

UNMAPPED_EXPECTED = frozenset({"stage_rta", "castle_movement", "not_a_target"})

_NUMBERED = re.compile(r"^\s*(\d{1,2})\.\s")
_STAGE_RTA = re.compile(r"\bRTA\b")
_HUNDRED = re.compile(r"\b100c\b", re.I)
# Every trailing parenthetical is an annotation rather than part of the name:
# "(JP)", "(US)", "(PAUSE TIME INCLUDED)", "(120 star file, JP)".
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")


def _normalize(text: str) -> str:
    previous = None
    while previous != text:
        previous, text = text, _PARENTHETICAL.sub("", text or "")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def section_course(section: str) -> int | None:
    """The course id a numbered section names, else None."""
    match = _NUMBERED.match(section or "")
    return int(match.group(1)) if match else None


def _star_in_course(course: int, wanted: str) -> str | None:
    for star in range(star_count(course)):
        if _normalize(star_name(course, star)) == wanted:
            return f"star:{course}:{star}"
    return None


def map_target(section: str, label: str) -> str | None:
    wanted = _normalize(label)
    if not wanted:
        return None
    course = section_course(section)
    if course is not None:
        if _STAGE_RTA.search(label or ""):
            return None
        if _HUNDRED.search(label or "") and course in MAIN_COURSES:
            # A "+ 100c" run is the 100-coin star ending on the named star,
            # whatever abbreviation the row uses for it ("Reds + 100c ...").
            return f"star:{course}:{HUNDRED_COIN_STAR}"
        return _star_in_course(course, wanted)
    if (section or "").startswith("Castle Secret Stars"):
        # These rows name the COURSE ("Tower of the Wing Cap"), whose stars
        # carry generic names ("8 Red Coins"), so match the course first.
        for secret in SECRET_COURSES:
            if _normalize(course_name(secret)) == wanted:
                return f"star:{secret}:0"
        for secret in SECRET_COURSES:
            hit = _star_in_course(secret, wanted)
            if hit:
                return hit
        return None
    if (section or "").startswith("Bowser Courses"):
        for course, course_segment, battle_segment in BOWSER_COURSES:
            base = _normalize(course_name(course))
            if not wanted.startswith(base):
                continue
            rest = wanted[len(base):].strip()
            if rest == "course":
                return f"segment:{course_segment}"
            if rest == "battle":
                return f"segment:{battle_segment}"
            if rest == "red coins":
                # The sheet says "Red Coins"; our registry names the same star
                # "8 Red Coins". Leaving this unmapped dropped every Bowser
                # reds ladder from the rank seed once already (2026-07-23).
                return f"star:{course}:0"
            return _star_in_course(course, rest)
        return None
    return None


# Rows inside a course section that name something real but not a target we
# model. One row, one reason -- the same by-row exemption shape
# tests/test_docs_links_resolve.py uses, so an exemption always says why.
KNOWN_NON_TARGETS = {
    ("6. Hazy Maze Cave", "Fade of the CotMC entry"):
        "times the fade entering the Cavern of the Metal Cap, not a star",
}


def miss_reason(section: str, label: str, group: str = "") -> str:
    if _STAGE_RTA.search(label or "") and section_course(section) is not None:
        return "stage_rta"
    if (group or section or "").startswith("Castle Movements"):
        return "castle_movement"
    if (section, _PARENTHETICAL.sub("", label or "").strip()) in KNOWN_NON_TARGETS:
        return "not_a_target"
    return "unknown"
