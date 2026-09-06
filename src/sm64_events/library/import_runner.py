"""One runner's Ultimate Sheet column, as times they can import.

The sheet already speaks our vocabulary: every target row carries the trainer's
own entity key, and `library/adopt.py` has already paired most approaches with
the vetted strategy they are the same thing as. So this is a mapping over data
we ship, not a scrape — 15 of DentoriousRed's 16 rows arrive with their
strategy already named.

A STAR row lands on its star. Every other row — a SUBSECTION (a stretch inside
a target), a castle-movement approach the mapper never paired, a Bowser row
the mapper stamped with a foreign `segment:N` — lands only where the CALLER
can place it, through `place(target, item, kind)`. The router builds that
placer from the same facts the Library tab shows (`server/import_api.py::
sheet_row_placer`): the row's explicit link to a segment he built
(`library/adoptions.py`), the name-match an entity-less target gets unasked,
and the seed_key behind a sheet Bowser id (`library/mapping.py`). His ruling
(2026-08-23): *"If an entry is a subsection AND we've successfully linked an
actual subsection segment that we've recorded to that library entry, then
when we import, it should import correctly... once we've defined what the
subsection actually links to, we should be able to import it very easily."*
Until then every subsection row was dropped regardless of any link.

A row nobody can place is HELD, NAMED — one row per held entry in the
`{text, reason}` shape every door answers with, carrying the row's stable
`row_key`, its `time_cs` and the ROM it was set on, so the caller can keep
the cell (`TrackerService.import_times(held=)`) rather than drop it. Round
28 (2026-09-04), his ruling: *"maximize compatibility with the sheet"* --
a row the trainer has no home for yet still round-trips, shows on its
Library row, and lands the moment the row is linked. They were COUNTED by
kind until round 3 (2026-08-23), which hid exactly what he asked to see:
*"it makes more sense to just show all the things that failed as a list"*.
The reasons:

  * `subsections` — a piece with no link. Landing it on the target's star
    would publish a 15.90 s way of doing a 43 s star.
  * `no_entity` — a castle movement neither linked nor name-matched, a
    stage RTA (a route, not a target), a row the mapper names as no target.
  * `segments` — a Bowser row whose seeded movement this database no longer
    holds. A bare segment id is LOCAL to each database (the mapper resolved
    `segment:6` against the seeding order of the machine that scraped it),
    so the reader never lands the number itself.
  * `real_time` — a star row the sheet times on a REAL-TIME clock ("[N64
    REAL TIME] w/ sub", one approach on the whole sheet). A star here is
    a frame count, so 42.52 could only land as 42.53; held as written.

Pure — takes a payload, returns candidates. The caller decides where the
payload came from, which is what lets the picker fill from the bundled
snapshot while the import itself reads a fresh fetch.
"""
import re

from sm64_events.library.adoptions import sheet_strategy
from sm64_events.library.audit import row_key
from sm64_events.library.sheet import entry_version
from sm64_events.tracking.importing import ImportCandidate

# Sheet approach times are STAR times measured the way Usamune measures them.
# A placed row lands on whatever clock the placer names for it.
TIMER_MODE = "igt"

# What this door lands without anyone vouching. Everything else goes through
# `place` — see the module docstring.
IMPORTABLE_KIND = "star:"

# The four reasons a sheet row is held. `ui/components/importflow.js`
# `REASONS` puts each into words; a new key here owes a sentence there.
SUBSECTION = "subsections"
NO_ENTITY = "no_entity"
SEGMENT = "segments"
REAL_TIME = "real_time"

# The sheet's own marker for a row timed on a real-time clock rather than
# the frame counter -- "[N64 REAL TIME] w/ sub". Matched on the words, not
# the brackets, so a re-styled marker still reads.
_REAL_TIME = re.compile(r"\bREAL[ -]?TIME\b", re.IGNORECASE)


def timed_in_real_time(item: dict) -> bool:
    return bool(_REAL_TIME.search(item.get("name") or ""))


def _sheet_time(centiseconds: int) -> str:
    """The sheet's own number as `m'ss"cc`, THE way a time reads here.

    Not `core/timefmt.format_igt`: that formats FRAMES, and this row is about
    what the sheet says, before any snap to the timer's displayable set."""
    minutes, rest = divmod(int(centiseconds), 6000)
    seconds, cents = divmod(rest, 100)
    return f"{minutes}'{seconds:02d}\"{cents:02d}"


def _held(target: dict, item: dict, entry: dict, reason: str,
          version: str | None) -> dict:
    """One reviewable row for an entry this door could not land -- and
    everything the caller needs to KEEP it: the row's stable key, the
    sheet's own centiseconds, the ROM the entry was set on.

    The target label carries its ROM version where the sheet opened a
    separate target for it (BBH's Ghost Hunt, the JP movements), or two
    held rows read as one. The approach or piece name is added only where
    it says more than the target does — a castle movement's single approach
    is named after the movement itself."""
    label = target.get("label") or "?"
    if target.get("version"):
        label = f"{label} ({target['version'].upper()})"
    parts = [label]
    detail = item.get("name")
    if detail and detail != target.get("label"):
        parts.append(detail)
    parts.append(_sheet_time(entry["time_cs"]))
    return {"text": " — ".join(parts), "reason": reason,
            "row_key": row_key(target, item.get("name") or "",
                               item.get("ids") or ()),
            "time_cs": int(entry["time_cs"]), "game_version": version,
            "platform": entry.get("platform"), "video": entry.get("video")}


def _hold_reason(target: dict, item: dict, kind: str) -> str:
    if kind == "subsection":
        return SUBSECTION
    if timed_in_real_time(item):
        return REAL_TIME
    entity_key = target.get("entity_key") or ""
    return SEGMENT if entity_key.startswith("segment:") else NO_ENTITY


def candidates_for(payload: dict, runner: str, place=None):
    """`([ImportCandidate, ...], [{text, reason, row_key, time_cs,
    game_version}, ...])` — what lands, and one named row per entry that
    could not, carrying what it takes to HOLD it.

    `place(target, item, kind) -> (entity_key, timer_mode, strategy | None)`
    is how the caller vouches for anything that is not a star row: the local
    entity it lands on, the clock that entity is timed on, and the strategy
    to file it under (None = the sheet's own: the vetted pairing where there
    is one, else the approach's name). Without it, or when it answers None,
    such rows are held, named."""
    candidates = []
    held = []
    for target in payload.get("targets") or []:
        target_key = target.get("entity_key") or ""
        version = target.get("version")
        for kind, collection in (("subsection", "subsections"),
                                 ("approach", "approaches")):
            for item in target.get(collection) or []:
                entries = [entry for entry in item.get("entries") or []
                           if entry.get("runner") == runner]
                if not entries:
                    continue
                # WHICH slot this row is -- a row named after the target is
                # its Standard, a repeated name is qualified by the approach
                # it sits under, a 100-coin route's sub-row by its route.
                # `adoptions.sheet_strategy` is that rule, and the column
                # export reads the SAME function, which is what lets a
                # column round-trip: every worksheet row of one entity has
                # a slot of its own (round 28, 2026-09-04; round 27 measured
                # 20 of Raisn's star rows sharing a slot with a sibling).
                own_strategy = sheet_strategy(target, item, kind)
                placed = place(target, item, kind) if place else None
                if placed:
                    entity_key, timer_mode, strategy = placed
                    strategy = strategy or own_strategy
                elif (kind == "approach" and target_key.startswith(IMPORTABLE_KIND)
                        and not timed_in_real_time(item)):
                    entity_key, timer_mode, strategy = (
                        target_key, TIMER_MODE, own_strategy)
                else:
                    reason = _hold_reason(target, item, kind)
                    held.extend(_held(target, item, entry, reason,
                                      entry_version(entry))
                                for entry in entries)
                    continue
                # The ENTRY's own version and nothing else -- `sheet.
                # entry_version` is that rule, and the Scorecard's runner goal
                # reads the same one. A merged (JP)/(US) approach holds both
                # regions' times in one item while the TARGET carries only one
                # of the two labels, so Raisn's US 45.70 landed as a JP time
                # and its own (US) row exported blank (57 of his 58 missing
                # star cells, 2026-09-02). Falling back to the target's label
                # for a row that declares none was the same artifact one level
                # up: round 35 dropped it, and such a time is now unversioned
                # -- the same on both ROMs, which is what the sheet says.
                candidates.extend(ImportCandidate(
                    entity_key=entity_key, strat_tag=strategy,
                    time_cs=int(entry["time_cs"]),
                    game_version=entry_version(entry),
                    timer_mode=timer_mode,
                    platform=entry.get("platform"), video=entry.get("video"),
                    row_key=row_key(target, item.get("name") or "",
                                    item.get("ids") or ())) for entry in entries)
    return candidates, held
