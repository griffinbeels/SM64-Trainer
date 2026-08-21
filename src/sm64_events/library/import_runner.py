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
    practice — stage RTA routes, mostly.
  * Rows mapped to a SEGMENT. Six of the snapshot's 252 targets carry one, and
    a segment id is LOCAL to each player's database — the mapper resolved
    `segment:6` against the seeding order of the machine that scraped it. A
    FOREIGN id is worse than a missing one: it very likely exists here too and
    names a different movement, so the time would land silently on the wrong
    thing rather than failing. (A LiveSplit gold is the opposite case and does
    land: `tracking/import_names.py` matches it by NAME against segments the
    player built here, so the id it produces is this database's own.) Segments
    are RTA-only besides, while every sheet approach time is an IGT star time.
    Importing a movement FROM THE SHEET needs the row assigned to a segment
    the user actually built, which is `library/adoptions.py`'s job.

All three are COUNTED rather than silently skipped: a drop nobody can see
reads as an import that half-worked.

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


def candidates_for(payload: dict, runner: str):
    """`([ImportCandidate, ...], {"subsections": n, "no_entity": n,
    "segments": n})`."""
    candidates = []
    rejected = {"subsections": 0, "no_entity": 0, "segments": 0}
    for target in payload.get("targets") or []:
        entity_key = target.get("entity_key")
        version = target.get("version")
        for piece in target.get("subsections") or []:
            rejected["subsections"] += sum(
                1 for entry in piece.get("entries") or []
                if entry.get("runner") == runner)
        for approach in target.get("approaches") or []:
            # The vetted name wins where the sheet's approach was paired with
            # one; the sheet's own name is the honest fallback.
            strategy = approach.get("matched_strategy") or approach.get("name")
            for entry in approach.get("entries") or []:
                if entry.get("runner") != runner:
                    continue
                if not entity_key:
                    rejected["no_entity"] += 1
                    continue
                if not entity_key.startswith(IMPORTABLE_KIND):
                    rejected["segments"] += 1
                    continue
                candidates.append(ImportCandidate(
                    entity_key=entity_key, strat_tag=strategy,
                    time_cs=int(entry["time_cs"]), game_version=version,
                    timer_mode=TIMER_MODE))
    return candidates, rejected
