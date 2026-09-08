"""Read-only Overall backtests using production fits and actual runner portfolios.

Without --policy, compare the legacy fastest-strategy envelope with Overall on
the same compatible observations. With --policy, compare shipped Overall with
that JSON policy patch. Local identities come from a disposable seeded database;
the tool never opens a user's database or refits a source file in place.

Example: python tools/compare_rank_calibrations.py --output comparison.json
"""
import argparse
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sm64_events.core.timefmt import cs_of_frame, frame_at_or_after # noqa: E402
from sm64_events.library.calibration import prepare                 # noqa: E402
from sm64_events.library.ladders import fit_payload                  # noqa: E402
from sm64_events.library.placements import automatic_rows           # noqa: E402
from sm64_events.library.practice_catalog import ensure_catalog     # noqa: E402
from sm64_events.library.ratings import rate_runners                # noqa: E402
from sm64_events.ranks import curves, scopes, scoring                # noqa: E402
from sm64_events.ranks.calibration import (                         # noqa: E402
    CalibrationRegistry, fingerprint, observation_fingerprint, resolve_curve)
from sm64_events.ranks.policy import RankingPolicy                  # noqa: E402
from sm64_events.ranks.standards import RankStandards                # noqa: E402
from sm64_events.storage.db import Database                         # noqa: E402
from sm64_events.tracking.defaults import reconcile_defaults        # noqa: E402


def load_json(path):
    data = Path(path).read_bytes()
    return json.loads(gzip.decompress(data) if str(path).endswith(".gz") else data)


def _division_targets(curve):
    if not curve["nodes"] and not curve["ladder_cs"]:
        return []
    defined = scoring.defined_tiers(curve["ladder_cs"])
    targets = []
    for tier in [*defined, "Iron"]:
        low, high = scoring.tier_band(tier, defined)
        for index, division in enumerate(scoring.DIVISION_NUMERALS):
            score = low + (high - low) * index / scoring.DIVISIONS_PER_TIER
            time = curves.time_for_score(curve, score)
            earned = curves.progress_for_time(curve, time) if time is not None else None
            targets.append({"tier": tier, "division": division, "score": score,
                            "time_cs": time,
                            "earns_target": earned["score"] + 1e-8 >= score if earned else None})
    return targets


def _frame_gains(curve, divisions, samples):
    """Consecutive-frame gains at every node/goal, plus bounded broad samples."""
    times = [point[0] for point in curve["nodes"]]
    times += [row["time_cs"] for row in divisions if row["time_cs"] is not None]
    if not times:
        return {"sampled": True, "sample_count": 0, "negative_gains": 0}
    low = max(1, frame_at_or_after(min(times)))
    high = max(low, frame_at_or_after(max(times)))
    frames = {low + round((high - low) * index / max(1, samples - 1))
              for index in range(samples)}
    for time in times:
        center = frame_at_or_after(time)
        frames.update(range(max(1, center - 2), center + 3))
    gains = []
    for frame in sorted(frames):
        slower = curves.progress_for_time(curve, cs_of_frame(frame))
        faster = curves.progress_for_time(curve, cs_of_frame(frame - 1))
        gains.append({"from_frame": frame, "time_cs": cs_of_frame(frame),
                      "gain": faster["score"] - slower["score"]})
    return {"sampled": True, "sample_count": len(gains),
            "frame_range": [min(frames), max(frames)],
            "negative_gains": sum(row["gain"] < -1e-8 for row in gains),
            "minimum_gain": min(row["gain"] for row in gains),
            "maximum_gain": max(row["gain"] for row in gains),
            "largest_gains": sorted(gains, key=lambda row: -row["gain"])[:5]}


def describe_curve(curve, frame_samples=129):
    divisions = _division_targets(curve)
    metadata = curve["metadata"]
    status = ("unrankable" if not curve["nodes"] and not curve["ladder_cs"] else
              "legacy_baseline" if metadata.get("source") == "legacy_fastest_envelope" else
              "legacy_fallback" if metadata.get("source") == "legacy" else
              "estimated" if metadata.get("estimated") else "generated")
    return {"status": status, "curve": curve, "division_targets": divisions,
            "invalid_division_goals": sum(row["earns_target"] is False for row in divisions),
            "per_frame_gains": _frame_gains(curve, divisions, frame_samples)}


def _score_times(times, resolved):
    result = {}
    for runner, entries in times.items():
        scores = {}
        for entity, time in entries.items():
            progress = curves.progress_for_time(resolved[entity], time)
            if progress is not None and progress["score"] is not None:
                scores[entity] = progress["score"]
        if scores:
            result[runner] = scores
    return result


def _aggregate(scores, groups):
    value = scopes.aggregate(scores, groups)
    return {key: value[key] for key in ("marelo", "n", "practiced", "coverage", "tier", "division", "entities")}


def route_comparison(route, before_curves, after_curves, before_times, after_times,
                     excluded=()):
    """Compare actual named portfolios with each side's real required-slot groups."""
    before_rankable = set(scopes.rankable_entities(before_curves, excluded))
    after_rankable = set(scopes.rankable_entities(after_curves, excluded))

    def groups(rankable):
        return scopes.entity_groups(f"route:{route['id']}", rankable=sorted(rankable),
                                    routes=[route], segment_courses={})

    before_groups, after_groups = groups(before_rankable), groups(after_rankable)
    common = groups(before_rankable & after_rankable)
    before_scores = _score_times(before_times, before_curves)
    after_scores = _score_times(after_times, after_curves)
    route_keys = {scopes.candidate_key(candidate) for step in route["steps"]
                  for candidate in step["candidates"]} - {None}
    runners = []
    for runner in sorted(set(before_scores) | set(after_scores)):
        old, new = before_scores.get(runner, {}), after_scores.get(runner, {})
        before, after = _aggregate(old, before_groups), _aggregate(new, after_groups)
        if not before["practiced"] and not after["practiced"]:
            continue
        old_common, new_common = _aggregate(old, common), _aggregate(new, common)
        delta = (after["marelo"] - before["marelo"]
                 if after["marelo"] is not None and before["marelo"] is not None else None)
        common_delta = (new_common["marelo"] - old_common["marelo"]
                        if new_common["marelo"] is not None else None)
        runners.append({"runner": runner, "before": before, "after": after, "delta": delta,
                        "common_slot_delta": common_delta,
                        "portfolio": "complete" if after["n"] and after["practiced"] == after["n"] else "partial",
                        "observed_times_cs": {key: time for key, time in
                            after_times.get(runner, {}).items() if key in route_keys}})
    ordered = sorted((row for row in runners if row["delta"] is not None),
                     key=lambda row: row["delta"])
    return {"seed_key": route.get("seed_key"), "name": route["name"],
            "collected_star_slots": sum(step.get("need", 1) for step in route["steps"]
                                        if all(c["type"] == "star" for c in step["candidates"])),
            "target_keys": sorted(route_keys), "groups_before": before_groups,
            "groups_after": after_groups, "groups_common": common,
            "became_rankable": sorted(route_keys & (after_rankable - before_rankable)),
            "became_unrankable": sorted(route_keys & (before_rankable - after_rankable)),
            "complete_portfolios": sum(row["portfolio"] == "complete" for row in runners),
            "partial_portfolios": sum(row["portfolio"] == "partial" for row in runners),
            "winners": [row["runner"] for row in reversed(ordered) if row["delta"] > 0][:10],
            "losers": [row["runner"] for row in ordered if row["delta"] < 0][:10],
            "runners": runners}


def _calibrate(payload, assignments, definitions, directory, seed_path, policy):
    directory.mkdir()
    fitted = deepcopy(payload)
    fit_payload(fitted, policy=policy)
    standards = RankStandards(directory / "standards.json", seed_path=seed_path)
    standards.load()
    generation = prepare(fitted, assignments, definitions, standards, policy)
    registry = CalibrationRegistry()
    registry.publish(generation)
    standards.calibrations = registry
    return fitted, standards, generation


def _target_names(payload, definitions, routes, calibrations):
    """Include absent route candidates as well as the entire calibrated Sheet."""
    names = {target["entity_key"]: target["label"] for target in payload["targets"]
             if target.get("entity_key")}
    names.update({f"segment:{row['id']}": row["name"] for row in definitions})
    for _source, standards, generation in calibrations:
        for key in set(standards.graded_entities()) | set(generation.overall):
            names.setdefault(key, key)
    for route in routes:
        for step in route["steps"]:
            for candidate in step["candidates"]:
                key = scopes.candidate_key(candidate)
                if key:
                    names.setdefault(key, key)
    return names


def _region_report(version, calibrations, assignments, definitions, routes, targets,
                   policy_patch, frame_samples):
    (source, base, _), (candidate_source, candidate, _) = calibrations
    before = {key: (resolve_curve(base, key, version) if policy_patch is not None else
                   curves.from_ladder(scoring.best_ladder(base.ladders(key, version)),
                       metadata={"source": "legacy_fastest_envelope"})) for key in targets}
    after = {key: resolve_curve(candidate, key, version) for key in targets}
    base_times = rate_runners(source, base, assignments, version=version).times
    new_times = rate_runners(candidate_source, candidate, assignments, version=version).times
    for key, target in targets.items():
        target["regions"][version] = {"before": describe_curve(before[key], frame_samples),
                                      "after": describe_curve(after[key], frame_samples)}
        if "caged" in target["label"].casefold():
            target["regions"][version]["sample_13_86"] = {
                "time_cs": 1386, "before": curves.progress_for_time(before[key], 1386),
                "after": curves.progress_for_time(after[key], 1386)}
    return [route_comparison(route, before, after, base_times, new_times,
                             scopes.default_excluded(definitions)) for route in routes]


def generate_report(payload, defaults, standards_seed, *, policy_patch=None,
                    regions=("us", "jp"), frame_samples=129):
    if frame_samples < 2:
        raise ValueError("frame_samples must be at least 2")
    original = fingerprint(payload)
    with tempfile.TemporaryDirectory(prefix="sm64-rank-comparison-") as scratch:
        directory = Path(scratch)
        db = Database(directory / "comparison.db")
        try:
            problems = reconcile_defaults(db, deepcopy(defaults))
            if problems:
                raise ValueError("defaults could not be resolved: " + "; ".join(problems))
            assignments = ensure_catalog(payload, {}, db)
            definitions = db.segment_defs()
            assignments = automatic_rows(payload, assignments, definitions)
            routes = [route for route in db.routes()
                      if (route.get("category") or "").startswith("Main Categories/16 Star")]
            if not routes:
                raise ValueError("defaults contain no supported 16 Star presets")
            base = _calibrate(
                payload, assignments, definitions, directory / "base", standards_seed, RankingPolicy())
            if policy_patch is None:
                candidate = base
            else:
                candidate = _calibrate(
                    payload, assignments, definitions, directory / "candidate", standards_seed,
                    RankingPolicy(policy_patch))
            names = _target_names(payload, definitions, routes, (base, candidate))
            targets = {key: {"label": names.get(key, key),
                             "stable_id": candidate[2].identities.get(key, key),
                             "regions": {}} for key in sorted(names)}
            route_reports = {version: _region_report(
                version, (base, candidate), assignments, definitions, routes, targets,
                policy_patch, frame_samples) for version in regions}
            report = {"schema_version": 1,
                      "comparison": "policy_patch" if policy_patch is not None else "legacy_to_overall",
                      "meaning": "Actual Sheet runner portfolios, not timed complete-route runs; "
                                 "current compatible clocks/regions on both sides, current route rules.",
                      "observation_revision": observation_fingerprint(payload),
                      "base_revision": base[2].revision,
                      "candidate_revision": candidate[2].revision,
                      "policy_patch": policy_patch, "targets": targets, "routes": route_reports,
                      "limits": ["Per-frame gains are sampled near every node and division plus a bounded grid.",
                                 "Legacy fallback and estimated curves are labeled per target.",
                                 "Coverage and denominator changes are separate from common-slot score changes."]}
        finally:
            db.close()
    if fingerprint(payload) != original:
        raise RuntimeError("comparison modified its source observations")
    report["source_observations_unchanged"] = True
    return report


def markdown_report(report):
    lines = [f"# Rank comparison: {report['comparison']}", "", report["meaning"], "",
             f"Targets: {len(report['targets'])}. Source observations unchanged: "
             f"{report['source_observations_unchanged']}.", "",
             "| Region | Route | Ranked slots before/after | Complete/partial portfolios |",
             "| --- | --- | --- | --- |"]
    for version, routes in report["routes"].items():
        for route in routes:
            old_n = sum(group["need"] for group in route["groups_before"])
            new_n = sum(group["need"] for group in route["groups_after"])
            lines.append(f"| {version} | {route['name']} | {old_n}/{new_n} | "
                         f"{route['complete_portfolios']}/{route['partial_portfolios']} |")
    lines += ["", "The JSON report contains full curves, family/count provenance, division goals, "
              "frame-gain samples, and each runner's selected route slots and scores."]
    for version in report["routes"]:
        statuses = {}
        for target in report["targets"].values():
            region = target["regions"][version]
            status = region["after"]["status"]
            statuses[status] = statuses.get(status, 0) + 1
            if sample := region.get("sample_13_86"):
                old, new = sample["before"], sample["after"]
                if old and new:
                    lines.append(f"- {version} {target['label']} at 13.86: "
                                 f"{old['score']:.2f} -> {new['score']:.2f} / 100 "
                                 f"({new['tier']} {new['division']}).")
        lines.append(f"- {version} target coverage: " + ", ".join(
            f"{count} {status}" for status, count in sorted(statuses.items())) + ".")
        for route in report["routes"][version]:
            ordered = sorted((row for row in route["runners"] if row["delta"] is not None),
                             key=lambda row: row["delta"])
            if ordered:
                low, high = ordered[0], ordered[-1]
                lines.append(f"- {version} {route['name']}: runner delta range "
                             f"{low['runner']} {low['delta']:+.2f} to {high['runner']} {high['delta']:+.2f}.")
    lines += ["", *[f"- {limit}" for limit in report["limits"]]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    data = REPO / "src" / "sm64_events" / "data"
    parser.add_argument("--sheet", type=Path, default=data / "sheet_library.seed.json.gz")
    parser.add_argument("--defaults", type=Path, default=data / "defaults.seed.json")
    parser.add_argument("--standards", type=Path, default=data / "rank_standards.seed.json")
    parser.add_argument("--policy", type=Path, help="JSON patch merged with shipped ranking policy")
    parser.add_argument("--output", type=Path, help="JSON or Markdown output; source paths cannot be overwritten")
    parser.add_argument("--format", choices=("json", "md"), default="json")
    parser.add_argument("--regions", nargs="+", choices=("us", "jp"), default=["us", "jp"])
    parser.add_argument("--frame-samples", type=int, default=129)
    args = parser.parse_args(argv)
    inputs = [args.sheet, args.defaults, args.standards, *([args.policy] if args.policy else [])]
    if args.output and args.output.resolve() in {path.resolve() for path in inputs}:
        parser.error("--output must not overwrite an input file")
    hashes = {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    report = generate_report(load_json(args.sheet), load_json(args.defaults), args.standards,
                             policy_patch=load_json(args.policy) if args.policy else None,
                             regions=args.regions, frame_samples=args.frame_samples)
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest for path, digest in hashes.items()):
        raise RuntimeError("an input file changed during comparison")
    report["source_file_hashes"] = hashes
    output = markdown_report(report) if args.format == "md" else json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote {len(report['targets'])} targets to {args.output}")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
