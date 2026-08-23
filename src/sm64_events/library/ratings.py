"""The Ultimate Sheet's 448 community runners, rated the same way MARELO
rates the user -- the twin of `tracking/marelo.py::entity_scores` for a
community sheet instead of a practice log. This is what turns the sheet from
a table of times into a leaderboard the user can climb: the same 0..100 curve,
the same ladders, the same coverage penalty.

**Never grade against a sheet row's own fitted ladder**
(`item["ladder"]`/`item["ladder_jp"]`, `library/ladders.py::fit_payload`). A
fitted ladder is percentiles measured off that ROW's own community
distribution, not the vetted Daily Star cutoffs MARELO grades the user on --
on the shipped snapshot the two curves differ on every matched approach, so a
runner scored on the sheet's own curve would look like a MARELO number and
would not BE one; it could never be compared to the user's. Every score here
therefore comes from the STANDARDS ladder,
`scoring.best_ladder(ranks_store.ladders(entity_key, version))` fed to
`scoring.progress_for_time(...)["score"]` -- exactly the pair
`tracking/marelo.py`'s two score paths already use for the user's own
attempts, over a different input. NEVER the raw `scoring.score_for`:
`tests/test_single_source.py`'s "turning a TIME into a rank" row reserves
that call for scoring.py itself, because it disagrees with
`progress_for_time` by up to half a centisecond at a division edge -- the
exact gap that once printed "0.00s to rank up" (2026-07-29). A runner sitting
on that edge deserves the same rounding rule the user's own banner gets.

Absent, never zero, same rule `marelo.py`'s own docstring states: a runner
with no time on an entity -- or one whose entity has no standards ladder at
all -- is OMITTED from the returned map. `ranks/scopes.py::aggregate` supplies
the coverage penalty (0 to the numerator, 1 to the denominator) for whatever
is missing; writing a 0.0 here would double it.

Pure: no db, no file I/O, no network, same discipline `ranks/scopes.py`
already holds to, so pytest drives this module directly."""
from dataclasses import dataclass

from sm64_events.library.audit import row_key
from sm64_events.ranks import scoring


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


def _row_entity(target: dict, item: dict, kind: str, adopted_rows: dict
                ) -> str | None:
    """The entity ONE row (one approach or one subsection) grades against, or
    None if it grades against nothing.

    An adoption always wins when one exists. Failing that: an APPROACH
    adopts itself when its target already carries an `entity_key` -- the
    sheet's own star mapping (`library/mapping.py`) already names it, which
    is what `library/adoptions.py`'s docstring calls a row that "adopts
    itself". A SUBSECTION never inherits its target's `entity_key` this way,
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
    Until the user has built and adopted a segment for that exact stretch, a
    subsection contributes nothing here."""
    adopted = adopted_rows.get(row_key(target, item["name"], item["ids"]))
    if adopted:
        return adopted
    if kind == "approaches":
        return target.get("entity_key")
    return None


def best_entries(payload: dict, adopted_rows: dict, *, version: str = "us"
                 ) -> dict[str, dict[str, dict]]:
    """{runner: {entity_key: the sheet entry that set their best time}}.

    A runner's time for an entity is the MINIMUM `time_cs` over every
    approach and subsection, on every target, that maps to it -- several
    targets per entity is normal, each "+ 100c" row is the same star a
    different way (`library/store.py::LibraryStore.for_entity`). The whole
    entry is kept, not just the number, because the [[Runner page]] plays
    that entry's video beside the time (round 1, third read)."""
    best: dict[str, dict[str, dict]] = {}
    for target in payload["targets"]:
        for kind in ("approaches", "subsections"):
            for item in target[kind]:
                entity_key = _row_entity(target, item, kind, adopted_rows)
                if not entity_key:
                    continue
                for entry in _visible_entries(item, version):
                    runner = entry.get("runner")
                    if not runner:
                        continue
                    by_entity = best.setdefault(runner, {})
                    if (entity_key not in by_entity
                            or entry["time_cs"] < by_entity[entity_key]["time_cs"]):
                        by_entity[entity_key] = entry
    return best


def runner_times(payload: dict, adopted_rows: dict, *, version: str = "us"
                 ) -> dict[str, dict[str, int]]:
    """{runner: {entity_key: best time_cs}} -- `best_entries` reduced to the
    number, the shape `rate_runners` grades and the tests pin."""
    return {runner: {key: entry["time_cs"] for key, entry in by_entity.items()}
            for runner, by_entity in best_entries(payload, adopted_rows, version=version).items()}


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
    best = best_entries(payload, adopted_rows, version=version)
    times = {runner: {key: entry["time_cs"] for key, entry in by_entity.items()}
             for runner, by_entity in best.items()}
    videos = {runner: {key: entry.get("video") or None for key, entry in by_entity.items()}
              for runner, by_entity in best.items()}
    entity_keys = {entity_key for by_entity in times.values()
                   for entity_key in by_entity}
    ladders = {entity_key: scoring.best_ladder(ranks_store.ladders(entity_key, version))
               for entity_key in entity_keys}
    scores: dict[str, dict[str, float]] = {}
    for runner, by_entity in times.items():
        runner_scores_map = {}
        for entity_key, time_cs in by_entity.items():
            ladder = ladders.get(entity_key)
            if not ladder:
                continue
            score = scoring.progress_for_time(ladder, time_cs)["score"]
            if score is not None:
                runner_scores_map[entity_key] = score
        if runner_scores_map:
            scores[runner] = runner_scores_map
    return RatedRunners(times=times, videos=videos, scores=scores)
