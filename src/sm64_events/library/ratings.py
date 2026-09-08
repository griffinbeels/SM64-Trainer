"""Community runners graded on the same Overall curves as local MARELO.

Ratings use compatible clocks and each entry's actual ROM, with unannotated
entries eligible for both. Library display visibility remains a separate,
broader reading. Every score goes through the resolved compiled curve and
its displayed-time progress rule; row-specific strategy ladders and the
curve's derived tier cutoffs cannot reconstruct Overall scoring.

Missing times and unrankable entities are absent from scores. The scope alone
supplies their coverage penalty. This module performs no I/O.
"""
from dataclasses import dataclass

from sm64_events.ranks import curves
from sm64_events.ranks.calibration import resolve_curve


def _visible_entries(item: dict, version: str) -> list[dict]:
    """One approach or subsection's entries in one game-version mode -- the
    same rule `librarytarget.js::Section` renders by (round 1, 2026-08-07):
    an entry tagged with the OTHER version disappears, an untagged entry
    shows in both. But a row is only version-FILTERED AT ALL when there is
    something to distinguish -- its own `ladder_jp`, or entries carrying more
    than one distinct tag; otherwise every entry shows in both modes, tagged
    or not. That second clause is not a corner case: 52 approaches/
    subsections in the shipped snapshot carry entries all tagged with the
    SAME single version and have no `ladder_jp` of their own (too few of that
    version's times to fit a second ladder), and Section shows every one of
    their ~1,100 entries in both US and JP mode. Filtering those out under
    the other mode would silently drop real times a runner should be graded
    on."""
    entries = item.get("entries") or []
    tags = {entry["version"] for entry in entries if entry.get("version")}
    versioned = bool(item.get("ladder_jp")) or len(tags) > 1
    if not versioned:
        return entries
    return [entry for entry in entries
            if not entry.get("version") or entry["version"] == version]


def _entries_set_on(item: dict, version: str,
                    unannotated_region: str = "both") -> list[dict]:
    """The reading a runner GOAL takes (round 34, 2026-09-05): an entry
    counts for `version` when its own row claims that ROM, or claims none at
    all -- `sheet.entry_version`, the exact door `library/import_runner.py`
    stamps the same entry through, so a runner's goal and his imported column
    can never disagree about which ROM a time is.

    `_visible_entries` answers a different question and keeps its looser
    rule: it says what the Library page DRAWS in a mode, where a single-tag
    row shows under both (52 rows, ~1,100 entries would otherwise vanish).
    Offering that as a GOAL read as a gap against the runner's own import --
    the parity walk found it under `regions=["jp"]`, YOU with no row and the
    goal holding a US-tagged time."""
    from sm64_events.library.populations import eligible_entries
    return eligible_entries(item, version, unannotated_region)


def _row_entity(target: dict, item: dict, kind: str, adopted_rows: dict
                ) -> str | None:
    """The entity ONE row (one approach or one subsection) grades against, or
    None if it grades against nothing.

    `placements.row_identity` supplies the same local entity used by imports
    and standards. Only a star approach can inherit its source entity key;
    a Sheet segment key requires a current local link. A SUBSECTION never
    inherits its target's `entity_key` this way,
    even when one exists, and this is NOT a defensible-either-way choice --
    it is required, and letting a subsection inherit is a scoring bug with a
    measured direction. A subsection times a STRETCH inside its target, not
    the whole thing (every one of the shipped snapshot's 122 subsections
    sits inside a target that already carries an entity_key -- a star), so
    its time is only ever a fraction of that star's own cutoffs. Because
    `runner_times` takes the MINIMUM across every mapping to an entity, a
    subsection that inherited would ALWAYS win over the runner's real star
    time -- a 2-second fragment graded against a 12-second Mario cutoff reads
    as an outlandishly fast star, never a merely-good one. Measured on the
    shipped snapshot: letting subsections inherit moves the runner corpus
    from 442 to 446 and its top MARELO from 85.2 to 88.8 -- a runner's rating
    can go up for grading a piece of a star as if it were the whole thing.
    A subsection contributes only through its own local practice entry."""
    from sm64_events.library.placements import row_identity
    identity = row_identity(target, item, "approach" if kind == "approaches"
                            else "subsection", adopted_rows)
    return identity[0] if identity else None


def best_entries(payload: dict, adopted_rows: dict, *, version: str = "us",
                 strict: bool = False, clock_of=None,
                 unannotated_region_of=None) -> dict[str, dict[str, dict]]:
    """{runner: {entity_key: the sheet entry that set their best time}}.
    `strict` reads each row by the ROM its entries were SET on
    (`_entries_set_on`) rather than by what the Library shows in that mode
    (`_visible_entries`). Scoring callers also pass `clock_of` to exclude
    explicitly real-time rows from an entity measured on IGT, and
    `unannotated_region_of(key)` to share the effective curve's region policy.

    A runner's time for an entity is the MINIMUM `time_cs` over every
    approach and subsection, on every target, that maps to it -- several
    targets per entity is normal, each "+ 100c" row is the same star a
    different way (`library/store.py::LibraryStore.for_entity`). The whole
    entry is kept, not just the number, because the [[Runner page]] plays
    that entry's video beside the time (round 1, third read)."""
    from sm64_events.library.placements import scoring_identity
    from sm64_events.library.populations import runner_identity

    best: dict[str, dict[str, dict]] = {}
    for target in payload["targets"]:
        for kind in ("approaches", "subsections"):
            for item in target[kind]:
                if strict:
                    identity = scoring_identity(
                        target, item, "approach" if kind == "approaches" else "subsection",
                        adopted_rows, clock_of)
                    entity_key = identity[0] if identity else None
                else:
                    entity_key = _row_entity(target, item, kind, adopted_rows)
                if not entity_key:
                    continue
                unannotated = (unannotated_region_of(entity_key)
                               if unannotated_region_of else "both")
                visible = (_entries_set_on(item, version, unannotated) if strict
                           else _visible_entries(item, version))
                for entry in visible:
                    if strict:
                        runner = runner_identity(entry)
                    else:
                        runner = entry.get("runner")
                    if not runner:
                        continue
                    by_entity = best.setdefault(runner, {})
                    if (entity_key not in by_entity
                            or entry["time_cs"] < by_entity[entity_key]["time_cs"]):
                        by_entity[entity_key] = entry
    return best


def runner_times(payload: dict, adopted_rows: dict, *, version: str = "us",
                 strict: bool = False) -> dict[str, dict[str, int]]:
    """{runner: {entity_key: best time_cs}} -- `best_entries` reduced to the
    number, the shape `rate_runners` grades and the tests pin. `strict` as
    in `best_entries`."""
    return {runner: {key: entry["time_cs"] for key, entry in by_entity.items()}
            for runner, by_entity in best_entries(payload, adopted_rows, version=version,
                                                  strict=strict).items()}


@dataclass(frozen=True)
class RatedRunners:
    """Every runner on the sheet, rated. `times` is {runner: {entity_key:
    best time_cs}}; `videos` is the same map's entry video (or None);
    `scores` is {runner: {entity_key: 0..100}}, the input
    `ranks/scopes.py::aggregate` takes one runner at a time. A runner whose
    entities all lack a standards ladder is absent from `scores`."""
    times: dict[str, dict[str, int]]
    videos: dict[str, dict[str, str | None]]
    scores: dict[str, dict[str, float]]


def rate_runners(payload: dict, ranks_store, adopted_rows: dict, *,
                 version: str = "us") -> RatedRunners:
    """The one door from a sheet payload to every runner's rating. An
    entity with no ladder in `ranks_store` (a target the sheet reaches that
    carries no rank standards) is omitted the same as an entity the runner
    never ran."""
    resolved = {}

    def unannotated_region_of(entity_key):
        if entity_key not in resolved:
            resolved[entity_key] = resolve_curve(ranks_store, entity_key, version)
        return resolved[entity_key]["metadata"].get("unannotated_region", "both")

    clock_of = getattr(ranks_store, "clock_for", lambda key: "igt")
    best = best_entries(payload, adopted_rows, version=version, strict=True,
                        clock_of=clock_of, unannotated_region_of=unannotated_region_of)
    times = {runner: {key: entry["time_cs"] for key, entry in by_entity.items()}
             for runner, by_entity in best.items()}
    videos = {runner: {key: entry.get("video") or None for key, entry in by_entity.items()}
              for runner, by_entity in best.items()}
    scores: dict[str, dict[str, float]] = {}
    for runner, by_entity in times.items():
        runner_scores_map = {}
        for entity_key, time_cs in by_entity.items():
            progress = curves.progress_for_time(resolved[entity_key], time_cs)
            score = progress["score"] if progress is not None else None
            if score is not None:
                runner_scores_map[entity_key] = score
        if runner_scores_map:
            scores[runner] = runner_scores_map
    return RatedRunners(times=times, videos=videos, scores=scores)
