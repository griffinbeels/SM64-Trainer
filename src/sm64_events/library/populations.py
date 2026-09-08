"""Compatible Sheet observations, unique people and provisional family stages.

The caller resolves local entities and compatible clocks before calling here.
An entry's region is authoritative: a merged Sheet target can retain a `jp`
label while containing both regions. Unannotated observations explicitly apply
to both regions by default. Runner names remain exact identities, not fuzzy
matches. Best/ideal/related estimates never manufacture submitted runners.
"""
import math
from collections import Counter
from dataclasses import dataclass

POPULATION_MODEL_VERSION = 1


def valid_time(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value > 0)


def runner_identity(entry):
    """Explicit provider IDs outrank labels; otherwise preserve exact names."""
    identity = entry.get("runner_id")
    if identity is not None:
        return "id:" + identity if isinstance(identity, str) and identity.strip() else None
    name = entry.get("runner")
    return name if isinstance(name, str) and name.strip() else None


def _entry_exclusion(entry, version, unannotated_region):
    if not isinstance(entry, dict) or not valid_time(entry.get("time_cs")):
        return "invalid_time"
    region = entry.get("version")
    if region not in (None, "us", "jp"):
        return "invalid_region"
    if region != version and not (region is None and unannotated_region == "both"):
        return "other_region"
    if runner_identity(entry) is None:
        return "missing_runner"
    return None


def eligible_entries(row, version, unannotated_region="both"):
    """Original entry dictionaries eligible for fitting and board scoring.

    Keep video and source fields intact. Consumers may use broader helpers for
    display, but an invalid observation must never enter a strict score path.
    """
    if version not in ("us", "jp") or unannotated_region not in ("both", "exclude"):
        raise ValueError("eligibility needs us/jp and a both/exclude unannotated policy")
    return [entry for entry in row.get("entries") or ()
            if _entry_exclusion(entry, version, unannotated_region) is None]


def quantile(values, fraction):
    times = sorted(values)
    if not times:
        raise ValueError("a quantile needs at least one observation")
    position = (len(times) - 1) * fraction
    low = int(position)
    return times[low] + (position - low) * (times[min(low + 1, len(times) - 1)] - times[low])


def row_identifier(row):
    """Use the audit row key when supplied, otherwise name plus lineage IDs."""
    for field in ("row_id", "row_key", "key"):
        if isinstance(row.get(field), str) and row[field]:
            return row[field]
    name = row.get("name", "")
    ids = row.get("ids") or ()
    return str(name) + ("||" + "|".join(str(value) for value in ids) if ids else "")


def _union(populations):
    out = {}
    for population in populations:
        for runner, time in population.items():
            out[runner] = min(time, out.get(runner, time))
    return dict(sorted(out.items()))


@dataclass(frozen=True)
class FamilyPopulation:
    id: str
    label: str
    rows: tuple[str, ...]
    best_by_runner: dict[str, float]
    provisional: bool
    confidence: float


@dataclass(frozen=True)
class TargetPopulation:
    best_by_runner: dict[str, float]
    families: tuple[FamilyPopulation, ...]
    proxy_times: tuple[float, ...]
    metadata: dict


def _row_proxy(row, version, unannotated_region):
    """Only explicit source evidence; never infer observations from a ladder."""
    provenance = row.get("estimate_provenance") or row.get("ladder_estimate") or {}
    supplied = row.get("estimate_times_cs") or ()
    intended = row.get("estimate_version", row.get("ladder_version"))
    if supplied and (intended == version or (intended is None and unannotated_region == "both")):
        return [value for value in supplied if valid_time(value)], provenance
    regional = row.get("times") or {}
    if valid_time(regional.get(version)):
        return [regional[version]], {"method": "best", "source_version": version}
    if regional or (intended is not None and intended != version):
        return [], {}
    if unannotated_region == "exclude" and intended is None:
        return [], {}
    for field, method in (("best_cs", "best"), ("ideal_cs", "ideal")):
        if valid_time(row.get(field)):
            return [row[field]], {"method": method, "source_version": intended}
    return [], {}


def _overlap(left, right, settings):
    """Overlap of central execution ranges, normalized by the smaller range."""
    a, b = (quantile(left.values(), settings[key]) for key in ("fast_quantile", "slow_quantile"))
    c, d = (quantile(right.values(), settings[key]) for key in ("fast_quantile", "slow_quantile"))
    # A one-frame floor also collapses identical single observations and ties.
    half_frame = 100 / 30 / 2
    a, b, c, d = a - half_frame, b + half_frame, c - half_frame, d + half_frame
    return max(0, min(b, d) - max(a, c)) / min(b - a, d - c)


def _group_families(rows, settings):
    curated = {}
    labels = {}
    for family in settings["families"]:
        labels[family["id"]] = family["label"]
        for selector in family["rows"]:
            curated[selector] = family["id"]
    groups = {}
    for row_id, name, population in rows:
        known = curated.get(row_id, curated.get(name))
        identity = known or "provisional:" + row_id
        group = groups.setdefault(identity, {"id": identity, "label": labels.get(known, name or row_id),
                                             "rows": set(), "populations": [], "provisional": known is None})
        group["rows"].add(row_id)
        group["populations"].append(population)
    groups = [{**group, "population": _union(group["populations"])} for group in groups.values()]
    # Deterministic complete-pair choice, with curated groups preferred on ties.
    # Two reviewed mechanical families remain separate; their reversed
    # checkpoints are pooled by the fitter instead of deleting their identity.
    groups.sort(key=lambda group: (group["provisional"], group["id"]))
    while True:
        candidates = []
        for i, left in enumerate(groups):
            for j in range(i + 1, len(groups)):
                right = groups[j]
                if not (left["provisional"] or right["provisional"]):
                    continue
                overlap = _overlap(left["population"], right["population"], settings)
                if overlap >= settings["overlap_ratio"]:
                    candidates.append((-overlap, i, j))
        if not candidates:
            break
        _, i, j = min(candidates)
        left, right = groups[i], groups.pop(j)
        left["population"] = _union([left["population"], right["population"]])
        left["rows"].update(right["rows"])
        left["provisional"] = left["provisional"] and right["provisional"]
    return tuple(FamilyPopulation(
        group["id"], group["label"], tuple(sorted(group["rows"])), group["population"],
        group["provisional"], min(1., len(group["population"]) / settings["confidence_runners"]))
        for group in groups)


def collect_populations(rows, *, settings, target_id="", version="us"):
    """Normalize already-compatible source rows without mutating the snapshot.

    Optional row ``target_id`` / ``clock_id`` must match the requested stable
    target. These guards catch accidentally concatenated full-clock groups;
    absence never grants permission to combine arbitrary subsections.
    """
    if version not in ("us", "jp"):
        raise ValueError("population version must be us or jp")
    by_row = {}
    exclusions = Counter()
    proxies = {}
    eligible_count = unannotated_count = 0
    for row in rows:
        if not isinstance(row, dict):
            exclusions["malformed_row"] += 1
            continue
        if any(row.get(key) and target_id and row[key] != target_id for key in ("target_id", "clock_id")):
            exclusions["incompatible_target"] += 1
            continue
        identity = row_identifier(row)
        population = {}
        for entry in row.get("entries") or ():
            reason = _entry_exclusion(entry, version, settings["unannotated_region"])
            if reason:
                exclusions[reason] += 1
                continue
            runner = runner_identity(entry)
            time = entry["time_cs"]
            eligible_count += 1
            unannotated_count += entry.get("version") is None
            population[runner] = min(time, population.get(runner, time))
        if population:
            prior = by_row.get(identity, (row.get("name", ""), {}))
            by_row[identity] = (prior[0], _union([prior[1], population]))
        else:
            times, provenance = _row_proxy(row, version, settings["unannotated_region"])
            if times:
                proxies[identity] = {"times": sorted(set(times)), "source_rows": [identity],
                                     "source_samples": 0, **provenance}
    normalized = [(identity, name, population) for identity, (name, population) in sorted(by_row.items())]
    best = _union(population for _, _, population in normalized)
    families = _group_families(normalized, settings)
    # Estimates are a fallback for an empty target, never additional people or
    # a slower milestone beside measured submissions from another strategy.
    proxies = {} if best else dict(sorted(proxies.items()))
    proxy_times = tuple(sorted({time for proxy in proxies.values() for time in proxy["times"]}))
    metadata = {
        "population_model_version": POPULATION_MODEL_VERSION,
        "target_id": target_id, "version": version,
        "population_scope": "eligible Sheet participants; best per exact runner identity",
        "population_count": len(best), "eligible_entries": eligible_count,
        "unannotated_entries": unannotated_count,
        "unannotated_region": settings["unannotated_region"],
        "excluded_entries": dict(sorted(exclusions.items())),
        "source_rows": sorted(by_row.keys() | proxies.keys()),
        "estimated": bool(proxies), "estimates": list(proxies.values()),
    }
    return TargetPopulation(best, families, proxy_times, metadata)
