"""Which sheet approaches become practiceable strategies, and which are the
ones we already have under another name.

Auto-adopting every star approach would duplicate half the strategy list: the
sheet's "Mario Wings to the Sky" and our vetted "Skyjump" are one strategy
written down twice. Nothing in either name says so, but their TIMES do — a
vetted ladder's Mario cutoff sits within a few centiseconds of its approach's
sheet best, which is exactly how the fit measurement paired 204 of them at a
median 0.33s apart.

So: match by that proximity, let the vetted ladder win wherever it matches, and
adopt only what is left. Subsections are never adopted (they would clutter the
segment list) and Castle Movements wait for a segment to exist — the user's
ruling, 2026-08-05.
"""
from sm64_events.library.ladders import LADDER_PERCENTILES

# Two ladders are the same strategy when their tiers agree this closely, taken
# as the MEDIAN relative difference across every tier they share.
MATCH_DISTANCE = 0.05

# A single number cannot do this job, and both ways it fails were measured on
# the real data (2026-08-05):
#   * comparing a vetted Mario cutoff against the approach's `best_cs` compares
#     a US-effective time against a JP one -- JRB's "Blast to the Stone Pillar"
#     is 10.80 JP / 14.50 US against a vetted 14.63, so the true pair looks
#     3.8 s apart while the sheet best looks like a different strategy.
#   * where a star has four strategies within a second of each other (RR's
#     "Somewhere Over the Rainbow"), a one-number match with any workable
#     tolerance pairs them essentially at random.
# Comparing the whole fitted ladder against the whole vetted one fixes both:
# eight points on the same scale, and both are derived the same way.


def _distance(vetted: dict, fitted: dict) -> float | None:
    """Median relative gap between two ladders, or None if they barely
    overlap."""
    shared = [rank for rank in vetted if rank in fitted and vetted[rank]]
    if len(shared) < 4:
        return None
    gaps = sorted(abs(fitted[rank] - vetted[rank]) / vetted[rank]
                  for rank in shared)
    middle = len(gaps) // 2
    return gaps[middle] if len(gaps) % 2 else (gaps[middle - 1] + gaps[middle]) / 2


def match_vetted(vetted: dict, approaches: list) -> dict:
    """{approach index: vetted strategy name} for the pairs that are the same
    strategy under two names.

    Assignment is greedy over the GLOBALLY closest pair rather than in ladder
    order, so the most confident pairing claims its partner first and a looser
    one cannot steal it on the way past."""
    scored = []
    for strategy, ladder in vetted.items():
        for index, approach in enumerate(approaches):
            fitted = approach.get("ladder")
            if not fitted:
                continue
            distance = _distance(ladder, fitted)
            if distance is not None and distance <= MATCH_DISTANCE:
                scored.append((distance, index, strategy))
    taken_rows, taken_strategies, out = set(), set(), {}
    for distance, index, strategy in sorted(scored):
        if index in taken_rows or strategy in taken_strategies:
            continue
        taken_rows.add(index)
        taken_strategies.add(strategy)
        out[index] = strategy
    return out


def adoptable(payload: dict, vetted_by_entity: dict, qualified: set = ()) -> dict:
    """{entity_key: {strategy name: fitted ladder}} for approaches that are
    genuinely new — a star we practice, an approach with a ladder, and no
    vetted strategy already describing it.

    Only APPROACHES on entities we map, and only where a fitted ladder exists:
    a strategy in the picker with no ladder grades against nothing, which is
    worse than not offering it."""
    out = {}
    for target in payload["targets"]:
        entity = target.get("entity_key")
        if not entity:
            continue                       # Castle Movements wait for a segment
        if entity in qualified:
            # A 100-coin star's strategies are VARIANT-QUALIFIED ("100c + Race
            # · Open") because CCM publishes two exit-star variants that both
            # define "Standard" AND "Open", so a bare name cannot identify a
            # ladder there. Adopting "100 coin star Xcam" unqualified breaks
            # that invariant, and the sheet row does not say which exit star it
            # ran -- so these wait rather than guess.
            continue
        approaches = target["approaches"]
        matched = match_vetted(vetted_by_entity.get(entity, {}), approaches)
        for index, approach in enumerate(approaches):
            if index in matched or not approach.get("ladder"):
                continue
            name = approach["name"]
            existing = out.setdefault(entity, {})
            if name in existing or name in vetted_by_entity.get(entity, {}):
                continue                   # same name, already covered
            existing[name] = approach["ladder"]
    return {entity: strategies for entity, strategies in out.items() if strategies}


def summary(adopted: dict, payload: dict) -> dict:
    total = sum(len(s) for s in adopted.values())
    with_ladder = sum(1 for t in payload["targets"] for a in t["approaches"]
                      if a.get("ladder"))
    return {"entities": len(adopted), "strategies": total,
            "approaches_with_ladders": with_ladder,
            "percentiles": dict(LADDER_PERCENTILES)}
