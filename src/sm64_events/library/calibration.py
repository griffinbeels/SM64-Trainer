"""Prepare the local grading population using the same placement as imports.

Source row identity and timed stretches stay in Library; statistical fitting
and the resulting curve do not depend on a local database or route preset.
"""
from collections import defaultdict

from sm64_events.library.adoptions import _rows, library_ladders
from sm64_events.library.audit import target_key
from sm64_events.library.ladder_estimates import estimate_times
from sm64_events.library.ladders import fit_payload, row_times
from sm64_events.library.placements import row_identity, scoring_identity, scoring_rows
from sm64_events.ranks.calibration import build_calibration


def prepare(payload, assignments, definitions, standards, policy):
    """Fit both regions, keyed locally but configured by stable star/seed identity."""
    from sm64_events.ranks.overall import fit_overall
    grouped = defaultdict(list)
    identities = {f"segment:{definition['id']}": definition.get("seed_key")
                  for definition in definitions}
    grading_rows = scoring_rows(payload, assignments, definitions)

    def strategy_identity(target, item, kind):
        kind = kind[:-1] if kind.endswith("s") else kind
        placed = row_identity(target, item, kind, assignments)
        entity, strategy = placed or ("", item.get("matched_strategy") or item.get("name"))
        stable = entity if entity.startswith("star:") else identities.get(entity)
        return stable or target_key(target), strategy

    # Loaded snapshots are already fitted. Refit when a scoped policy changes
    # the effective strategy settings, including a newly resolved local seed.
    for target, item, _key, kind in _rows(payload):
        stable, strategy = strategy_identity(target, item, kind)
        version = item.get("ladder_version") or "us"
        previous = item.get("ladder_policy_revision") or policy.effective_revision(
            "", version=version, layer="strategy")
        expected = policy.effective_revision(stable, version, strategy, "strategy")
        jp_changed = policy.resolve(stable, "jp", strategy, "strategy") != policy.resolve(
            "", "jp", layer="strategy")
        if expected != previous or jp_changed:
            fit_payload(payload, policy=policy, identity_of=strategy_identity)
            break
    populations = [(target, kind, item, *row_times(item))
                   for target, item, _key, kind in _rows(payload)]
    for target, item, key, kind in _rows(payload):
        identity = scoring_identity(target, item, kind, grading_rows, standards.clock_for)
        if identity is None:
            continue
        entity, strategy = identity
        stable = (entity if entity.startswith("star:") else identities.get(entity))
        stable = stable or (target_key(target) if kind == "approach" else key)
        identities[entity] = stable
        row = {**item, "row_id": key, "target_id": stable, "strategy": strategy}
        if not item.get("entries"):
            times, version, provenance = estimate_times(target, kind, item, populations)
            row.update(estimate_times_cs=times, estimate_provenance=provenance,
                       estimate_version=version)
        grouped[entity].append(row)
    overall = {}
    for entity, rows in grouped.items():
        versions = {}
        for version in ("us", "jp"):
            curve = fit_overall(rows, policy=policy, target_id=identities[entity], version=version)
            if curve is not None:
                versions[version] = curve
        if versions:
            overall[entity] = versions
    layers = standards.sheet_layers(library_ladders(payload, assignments))
    return build_calibration(payload, assignments, layers, overall,
                             {key: value for key, value in identities.items() if value},
                             policy.revision, scoring_rows=grading_rows)
