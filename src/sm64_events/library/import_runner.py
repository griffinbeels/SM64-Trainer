"""One runner's Ultimate Sheet column, as times they can import.

The sheet already speaks our vocabulary: every target row carries the trainer's
own entity key, and `library/adopt.py` has already paired most approaches with
the vetted strategy they are the same thing as. So this is a mapping over data
we ship, not a scrape — 15 of DentoriousRed's 16 rows arrive with their
strategy already named.

Three kinds of row are dropped, and each matters:

  * SUBSECTIONS time a stretch inside a target rather than the target. Reading
    one as a personal best would publish a 15.90 s way of doing a 43 s star.
    Filing a subsection under a segment the user built is a different feature
    and already has an owner (`library/adoptions.py`).
  * Rows whose target carries no entity key map to nothing the player can
    practice — the sheet's castle-movement rows, mostly, which
    `library/mapping.py` has not yet paired with the movements we seed.
  * Rows mapped to a SEGMENT. Six of the snapshot's 252 targets carry one, and
    a segment id is LOCAL to each player's database — the mapper resolved
    `segment:6` against the seeding order of the machine that scraped it. A
    FOREIGN id is worse than a missing one: it very likely exists here too and
    names a different movement, so the time would land silently on the wrong
    thing rather than failing. (A segment named in his own sheet is the opposite case and does
    land: `tracking/import_names.py` matches it by NAME against segments the
    player built here, so the id it produces is this database's own.) Segments
    are RTA-only besides, while every sheet approach time is an IGT star time.
    Importing a movement FROM THE SHEET needs the row assigned to a segment
    the user actually built, which is `library/adoptions.py`'s job.

Every dropped entry comes back as ONE NAMED ROW — the target, the approach or
piece where it adds anything, and the sheet's own time — in the same
`{line, text, reason}` shape every door answers with (`line` 0: a sheet cell
has no line to point at). They were COUNTED by kind until round 3
(2026-08-23), which hid exactly what he asked to see: *"it makes more sense to
just show all the things that failed as a list"* — a runner like GTM drops 33
rows to 29 landed, and the list is what lets him review them and say what
each should have mapped to.

Pure — takes a payload, returns candidates. The caller decides where the
payload came from, which is what lets the picker fill from the bundled
snapshot while the import itself reads a fresh fetch.
"""
from sm64_events.tracking.importing import ImportCandidate

# Sheet approach times are STAR times measured the way Usamune measures them.
# Segments are RTA-only and are not reachable through this door.
TIMER_MODE = "igt"

# What this door can land. A segment key is deliberately not here — see the
# module docstring.
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
    return {"line": 0, "text": " — ".join(parts), "reason": reason}


def candidates_for(payload: dict, runner: str):
    """`([ImportCandidate, ...], [{line: 0, text, reason}, ...])` — what lands,
    and one named row per entry that could not."""
    candidates = []
    rejected = []
    for target in payload.get("targets") or []:
        entity_key = target.get("entity_key")
        version = target.get("version")
        for piece in target.get("subsections") or []:
            for entry in piece.get("entries") or []:
                if entry.get("runner") == runner:
                    rejected.append(_dropped(
                        target, piece.get("name"), entry["time_cs"], SUBSECTION))
        for approach in target.get("approaches") or []:
            # The vetted name wins where the sheet's approach was paired with
            # one; the sheet's own name is the honest fallback.
            strategy = approach.get("matched_strategy") or approach.get("name")
            for entry in approach.get("entries") or []:
                if entry.get("runner") != runner:
                    continue
                if not entity_key:
                    rejected.append(_dropped(
                        target, approach.get("name"), entry["time_cs"], NO_ENTITY))
                    continue
                if not entity_key.startswith(IMPORTABLE_KIND):
                    rejected.append(_dropped(
                        target, approach.get("name"), entry["time_cs"], SEGMENT))
                    continue
                candidates.append(ImportCandidate(
                    entity_key=entity_key, strat_tag=strategy,
                    time_cs=int(entry["time_cs"]), game_version=version,
                    timer_mode=TIMER_MODE))
    return candidates, rejected
