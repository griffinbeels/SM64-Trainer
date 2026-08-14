"""Library entries as example CLIPS for the rank-standards table.

Task 0098: "hyperlinking every rank standard threshold with an example video
(if it exists) ... choose the fastest example within each threshold". The
SELECTION rule already exists — `ranks/classify.py::resolve_cutoff_videos`
bands clips by `rank_for` and keeps the fastest per tier, which IS "closest to
the next highest tier" — so this module only widens the clip SOURCES: every
library entry that carries both a time and a video, filed under the strategy
its row grades as. Measured against the bundled snapshot + seed (2026-08-14),
this lifts linked cells from 330/3069 to 1500/3069; the residue is 802 cells
whose strategy has no sheet row at all and 767 whose band holds no published
video — the "(if it exists)" case.

Three doors resolve a row to a (entity, strategy), the SAME doors that give a
strategy its ladder — so a strategy's example videos come from exactly the row
that grades it:

  1. an explicit adoption (`adoptions.rows()`): the user's entity, named by
     `adoptions.strategy_name` — approaches and subsections both;
  2. an unadopted APPROACH on an entity-keyed target: the target's own entity,
     under `matched_strategy` (the vetted name `library/adopt.py` stamped) or
     the approach's own name (the auto-adopted case);
  3. an unadopted SUBSECTION never contributes to its target's entity — the
     row times a PIECE, and a piece's time on the whole target's ladder would
     file every clip several tiers too fast.

JP entries are excluded only where a JP difference is ANNOTATED
(`has_jp_ladder`) — everywhere else the ladder is combined by the user's
2026-08-07 rule and a JP entry bands honestly.
"""
from sm64_events.library.adoptions import _rows, strategy_name


def example_clips(payload: dict, adoption_rows: dict, entity: str,
                  has_jp_ladder) -> dict:
    """{strategy: [[time_cs, url], ...]} for one entity — the extra clips
    `RankStandards.cutoff_videos` merges beside the vetted xcams ones.

    `adoption_rows` is `Adoptions.rows()` ({row_key: entity}); pass {} when no
    adoptions store exists (a broadcast-only instance). `has_jp_ladder` is
    `RankStandards.has_jp_ladder`.
    """
    out: dict[str, list] = {}
    for target, item, key, kind in _rows(payload):
        adopted_to = adoption_rows.get(key)
        if adopted_to == entity:
            strat = strategy_name(target["label"], item["name"], kind=kind)
        elif adopted_to is None and kind == "approach" \
                and target["entity_key"] == entity:
            strat = item.get("matched_strategy") or item["name"]
        else:
            continue
        clips = [[entry["time_cs"], entry["video"]]
                 for entry in item["entries"]
                 if entry.get("video") and entry.get("time_cs") is not None
                 and not (entry.get("version") == "JP"
                          and has_jp_ladder(entity, strat))]
        if clips:
            out.setdefault(strat, []).extend(clips)
    return out
