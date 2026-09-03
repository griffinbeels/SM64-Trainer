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

A row nobody can place is dropped, NAMED — one row per dropped entry in the
`{text, reason}` shape every door answers with. They were COUNTED by kind until round 3
(2026-08-23), which hid exactly what he asked to see: *"it makes more sense to
just show all the things that failed as a list"*. The reasons:

  * `subsections` — a piece with no link. Landing it on the target's star
    would publish a 15.90 s way of doing a 43 s star.
  * `no_entity` — a castle movement neither linked nor name-matched.
  * `segments` — a Bowser row whose seeded movement this database no longer
    holds. A bare segment id is LOCAL to each database (the mapper resolved
    `segment:6` against the seeding order of the machine that scraped it),
    so the reader never lands the number itself.

Pure — takes a payload, returns candidates. The caller decides where the
payload came from, which is what lets the picker fill from the bundled
snapshot while the import itself reads a fresh fetch.
"""
from sm64_events.library.adoptions import DEFAULT_STRATEGY, strategy_name
from sm64_events.tracking.importing import ImportCandidate

# Sheet approach times are STAR times measured the way Usamune measures them.
# A placed row lands on whatever clock the placer names for it.
TIMER_MODE = "igt"

# What this door lands without anyone vouching. Everything else goes through
# `place` — see the module docstring.
IMPORTABLE_KIND = "star:"

# The three reasons a sheet row is dropped. `ui/components/importflow.js`
# `REASONS` puts each into words; a new key here owes a sentence there.
SUBSECTION = "subsections"
NO_ENTITY = "no_entity"
SEGMENT = "segments"


def _sheet_time(centiseconds: int) -> str:
    """The sheet's own number as `m'ss"cc`, THE way a time reads here.

    Not `core/timefmt.format_igt`: that formats FRAMES, and this row is about
    what the sheet says, before any snap to the timer's displayable set."""
    minutes, rest = divmod(int(centiseconds), 6000)
    seconds, cents = divmod(rest, 100)
    return f"{minutes}'{seconds:02d}\"{cents:02d}"


def _dropped(target: dict, detail: str | None, time_cs: int, reason: str) -> dict:
    """One reviewable row for an entry this door could not land.

    The target label carries its ROM version where the sheet opened a
    separate target for it (BBH's Ghost Hunt, the JP movements), or two
    dropped rows read as one. The approach or piece name is added only where
    it says more than the target does — a castle movement's single approach
    is named after the movement itself."""
    label = target.get("label") or "?"
    if target.get("version"):
        label = f"{label} ({target['version'].upper()})"
    parts = [label]
    if detail and detail != target.get("label"):
        parts.append(detail)
    parts.append(_sheet_time(time_cs))
    return {"text": " — ".join(parts), "reason": reason}


def _drop_reason(target: dict, kind: str) -> str:
    if kind == "subsection":
        return SUBSECTION
    entity_key = target.get("entity_key") or ""
    return SEGMENT if entity_key.startswith("segment:") else NO_ENTITY


def candidates_for(payload: dict, runner: str, place=None):
    """`([ImportCandidate, ...], [{text, reason}, ...])` — what lands,
    and one named row per entry that could not.

    `place(target, item, kind) -> (entity_key, timer_mode, strategy | None)`
    is how the caller vouches for anything that is not a star row: the local
    entity it lands on, the clock that entity is timed on, and the strategy
    to file it under (None = the sheet's own: the vetted pairing where there
    is one, else the approach's name). Without it, or when it answers None,
    such rows are dropped, named."""
    candidates = []
    rejected = []
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
                # A row whose name IS the target's own is that thing's
                # STANDARD strategy, not a strategy called after the star --
                # his ruling (2026-09-02): "if the name of the row is just the
                # name of the star, then it should be given a Standard
                # strategy... For the other sub rows, those are either
                # subsections of the star, or they're genuine alternative
                # strategies, which are always named."
                #
                # `adoptions.strategy_name` is that rule and already answered
                # it this way for every row that goes through `place`; the
                # star branch below kept its own answer and filed those times
                # under "Big Bob-omb on the Summit". That is why a column he
                # imported did not line up with the times he PLAYS, which are
                # under Standard like everything else in this app.
                #
                # The vetted name still wins on a row that names a strategy;
                # a target-named row has no strategy to be vetted against.
                named = strategy_name(target.get("label") or "",
                                      item.get("name") or "", kind=kind)
                sheet_strategy = (named if named == DEFAULT_STRATEGY
                                  else (item.get("matched_strategy")
                                        or item.get("name")))
                placed = place(target, item, kind) if place else None
                if placed:
                    entity_key, timer_mode, strategy = placed
                    strategy = strategy or sheet_strategy
                elif kind == "approach" and target_key.startswith(IMPORTABLE_KIND):
                    entity_key, timer_mode, strategy = (
                        target_key, TIMER_MODE, sheet_strategy)
                else:
                    reason = _drop_reason(target, kind)
                    rejected.extend(_dropped(target, item.get("name"),
                                             entry["time_cs"], reason)
                                    for entry in entries)
                    continue
                # The ENTRY's own version, not the target's. A merged
                # (JP)/(US) approach holds both regions' times in one item and
                # the TARGET carries only one of the two labels -- Big Bob-omb
                # on the Summit is stamped `jp`, so Raisn's US 45.70 landed as
                # a JP time and its own (US) row exported blank. 57 of his 58
                # missing star cells were this (measured 2026-09-02). The
                # target's version stays the fallback for a row the sheet does
                # not tag.
                candidates.extend(ImportCandidate(
                    entity_key=entity_key, strat_tag=strategy,
                    time_cs=int(entry["time_cs"]),
                    game_version=entry.get("version") or version,
                    timer_mode=timer_mode) for entry in entries)
    return candidates, rejected
