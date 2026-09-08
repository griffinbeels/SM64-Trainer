"""Sheet example clips and best times under the same identity as standards.

placements.row_identity owns the local entity and canonical strategy.
A piece never contributes to its whole target's clock. The existing
rank classifier chooses a clip for each effective threshold; this module
only supplies the published observations and their links.
"""
from sm64_events.library.adoptions import _rows
from sm64_events.library.placements import row_identity
from sm64_events.library.sheet import entry_version


def strategy_entries(payload: dict, adoption_rows: dict, entity: str,
                     has_jp_ladder, version: str = "us"):
    """Yield `(strategy, entry)` for every sheet entry that grades on one of
    this entity's ladders — THE walk both readers below share.

    Extracted 2026-08-20 when the Sheet Best row arrived: two resolutions of
    "which sheet rows belong to this strategy" is the divergent-duplication
    class, and a strategy's fastest recorded time has to come from the same
    rows as its example videos or the table contradicts itself.

    `adoption_rows` is `Adoptions.rows()` ({row_key: entity}); pass {} when no
    adoptions store exists (a broadcast-only instance). `has_jp_ladder` is
    `RankStandards.has_jp_ladder`.
    """
    for target, item, key, kind in _rows(payload):
        identity = row_identity(target, item, kind, adoption_rows)
        if identity is None or identity[0] != entity:
            continue
        _, strat = identity
        jp_split = has_jp_ladder(entity, strat)
        for entry in item["entries"]:
            if entry.get("time_cs") is None:
                continue
            if jp_split and entry_version(entry) not in (None, version):
                continue
            yield strat, entry


def example_clips(payload: dict, adoption_rows: dict, entity: str,
                  has_jp_ladder, version: str = "us") -> dict:
    """{strategy: [[time_cs, url], ...]} for one entity — the extra clips
    `RankStandards.cutoff_videos` merges beside the vetted xcams ones."""
    out: dict[str, list] = {}
    for strat, entry in strategy_entries(payload, adoption_rows, entity,
                                         has_jp_ladder, version):
        if entry.get("video"):
            out.setdefault(strat, []).append([entry["time_cs"], entry["video"]])
    return out


def sheet_best(payload: dict, adoption_rows: dict, entity: str,
               has_jp_ladder, dead_urls: frozenset | set = frozenset(),
               version: str = "us") -> dict:
    """{strategy: {"time_cs", "runner", "video"}} — the fastest time anybody
    has recorded on the Ultimate Sheet for each of this entity's strategies.

    The standards table's bottom row. It exists because the top of a ladder is
    not the top of the sport: he expanded Mario into its five divisions, read
    Mario 1, and said "there actually ARE faster times than this"
    (2026-08-15). Naming it "Sheet Best" rather than "WR" is deliberate —
    what we know is that it is the fastest row on the sheet, and calling it a
    world record asserts more than that.

    Differs from `example_clips` in ONE way, and it is the point: an entry
    with no video still counts. The question is what the fastest recorded time
    IS, so a videoless faster run beats a slower filmed one and `video` is
    simply None there.

    Ties keep the sheet's own answer rather than inventing a winner — its
    own `best_runner` field reads "Multiple [3]" where three runners share the
    time, and a per-entry tie here resolves to whichever the sheet lists
    first.

    `dead_urls` (the liveness sweep's verdicts, tools/check_videos.py) costs
    a row its LINK, never the row: this row asserts a TIME, so dropping the
    fastest run because its clip rotted would make the number wrong in order
    to protect a link. That is the opposite trade from `cutoff_videos`, where
    the link IS the payload — same verdict set, different consequence.
    """
    out: dict[str, dict] = {}
    for strat, entry in strategy_entries(payload, adoption_rows, entity,
                                         has_jp_ladder, version):
        best = out.get(strat)
        if best is None or entry["time_cs"] < best["time_cs"]:
            video = entry.get("video")
            out[strat] = {"time_cs": entry["time_cs"],
                          "runner": entry.get("runner"),
                          "video": None if video in dead_urls else video}
    return out
