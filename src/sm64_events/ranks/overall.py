"""Pure community/progression fitting into a complete frame-calibrated curve.

Family p95/p10 landmarks describe observed performance progression, not measured
mechanical difficulty. Reversed landmarks share a geometric-time checkpoint.
The default blends those milestones with one best time per Sheet participant.
Changed evidence refits the elite distribution and includes a new record at the
frontier immediately; sparse provisional families have bounded wider influence.
"""
import math
from dataclasses import asdict

from sm64_events.core.timefmt import cs_of_frame, frame_at_or_after
from sm64_events.library.ladders import fit_ladder
from sm64_events.library.populations import collect_populations, quantile
from sm64_events.ranks import scoring
from sm64_events.ranks.curve_types import CompiledCurve
from sm64_events.ranks.curves import compile_curve, score_evaluator, time_for_score
from sm64_events.ranks.fit_cache import FitCache, frozen_inputs
from sm64_events.ranks.policy import RankingPolicy

OVERALL_MODEL_VERSION = 1
DIVISION_SCORES = tuple(sorted({100., *[
    low + i * (high - low) / 5
    for low, high in ((0, 10), (10, 25), (25, 45), (45, 60), (60, 70),
                      (70, 80), (80, 90), (90, 95), (95, 100))
    for i in range(5) if low + i * (high - low) / 5 > 0]}, reverse=True))


def _community_curve(times, settings):
    """Refit all empirical anchors; the current observed best bounds score100."""
    ladder = {rank: round(seconds * 100) for rank, seconds in
              fit_ladder(times, settings=settings["community_fit"]).items()}
    # Legacy's extrapolated ceiling remains a useful elite estimate, but a
    # new fastest observation must participate before it can move a quantile.
    ceiling = min(*times, scoring.time_for_score(ladder, 100))
    first = max(1, frame_at_or_after(round(ceiling)))
    nodes = [[cs_of_frame(first), 100.]]
    previous = first
    for rank, time in ladder.items():
        frame = max(previous + 1, frame_at_or_after(time))
        nodes.append([cs_of_frame(frame), scoring.SCORE_ANCHORS[rank]])
        previous = frame
    return compile_curve(nodes)


def _pool_landmarks(points):
    """Pool adjacent time-order inversions in log-time, preserving score order."""
    blocks = []
    for point in points:
        blocks.append([point])

        def logtime(block):
            return sum(math.log(item["cs"]) for item in block) / len(block)

        while len(blocks) > 1 and logtime(blocks[-2]) <= logtime(blocks[-1]):
            right = blocks.pop()
            blocks[-1].extend(right)
    return [{"cs": math.exp(sum(math.log(point["cs"]) for point in block) / len(block)),
             "score": sum(point["score"] for point in block) / len(block),
             "labels": [point["label"] for point in block]}
            for block in blocks]


def _milestone_curve(families, community, settings):
    """Accepted equal-family milestone fit, using newly fitted frontier nodes."""
    if len(families) < 2:
        return community, []
    ordered = sorted(families, key=lambda family: (-family["fast_cs"], family["id"]))
    step = 80 / (2 * len(ordered) - 2)
    points = []
    for i, family in enumerate(ordered):
        points.append({"cs": family["slow_cs"], "score": 5 + 2 * i * step,
                       "label": family["label"] + " slower landmark"})
        if i < len(ordered) - 1:
            points.append({"cs": family["fast_cs"], "score": 5 + (2 * i + 1) * step,
                           "label": family["label"] + " faster landmark"})
    landmarks = _pool_landmarks(points)
    mario = time_for_score(community, 95)
    ceiling = time_for_score(community, 100)
    landmarks = [point for point in landmarks if point["cs"] > mario]
    nodes = [[ceiling, 100.], [mario, 95.]]
    nodes.extend([point["cs"], point["score"]] for point in reversed(landmarks))
    return compile_curve(nodes), landmarks


def _threshold(evaluate, score, slow):
    """Slowest positive attainable frame earning score, with an expanding tail."""
    lo, hi = 0, max(2, frame_at_or_after(math.ceil(slow)))
    for _ in range(64):
        if evaluate(cs_of_frame(hi)) < score:
            break
        hi *= 2
    else:
        raise ValueError("Overall fit did not reach its positive slow tail")
    while lo + 1 < hi:
        middle = (lo + hi) // 2
        if evaluate(cs_of_frame(middle)) >= score:
            lo = middle
        else:
            hi = middle
    return cs_of_frame(max(1, lo))


def _calibrate(evaluate, frontier, slow):
    """Retain every division with at least one frame between calibrated nodes."""
    nodes, previous = [], 0
    for score in DIVISION_SCORES:
        desired = (time_for_score(frontier, score) if score >= 90
                   else _threshold(evaluate, score, slow))
        frame = max(previous + 1, frame_at_or_after(desired))
        nodes.append([cs_of_frame(frame), score])
        previous = frame
    return nodes


def _family_summaries(population, settings):
    return [{"id": family.id, "label": family.label, "rows": list(family.rows),
             "population_count": len(family.best_by_runner),
             "provisional": family.provisional, "confidence": family.confidence,
             "fast_cs": quantile(family.best_by_runner.values(), settings["fast_quantile"]),
             "slow_cs": quantile(family.best_by_runner.values(), settings["slow_quantile"]),
             "best_cs": min(family.best_by_runner.values())}
            for family in population.families]


def _community_model(population, community, settings, families):
    return score_evaluator(community), {"milestones": [], "provisional_influence": 0.}


def _family_model(population, community, settings, families):
    candidate, landmarks = _milestone_curve(families, community, settings)
    sparse_ids = {family["id"] for family in families if family["confidence"] < 1}
    established = [family for family in families if family["id"] not in sparse_ids]
    baseline, _ = _milestone_curve(established, community, settings)
    sparse_people = {runner for family in population.families if family.id in sparse_ids
                     for runner in family.best_by_runner}
    influence = min(1., len(sparse_people) / settings["confidence_runners"]) if sparse_ids else 1.
    weight = settings["milestone_weight"] if len(families) > 1 else 0.
    candidate_score = score_evaluator(candidate)
    baseline_score = score_evaluator(baseline)
    community_score = score_evaluator(community)

    def evaluate(time):
        milestone = influence * candidate_score(time) + (1 - influence) * baseline_score(time)
        return weight * milestone + (1 - weight) * community_score(time)

    return evaluate, {"milestones": landmarks, "provisional_influence": influence,
                      "effective_milestone_weight": weight}


MODEL_BUILDERS = {"community": _community_model, "family_milestones": _family_model}
_FIT_CACHE = FitCache()


def fit_overall(rows, *, policy=None, target_id="", version="us") -> CompiledCurve | None:
    """Fit compatible canonical Sheet rows; return None only without evidence.

    Personal strategy cutoffs and selected labels are not inputs. A one-family
    target uses exactly the community model. Metadata records population facts,
    provisional choices and the effective policy so a recalibration is explainable.
    """
    policy = policy or RankingPolicy()
    settings = policy.resolve(target_id, version=version, layer="overall")
    population = collect_populations(rows, settings=settings, target_id=target_id, version=version)
    revision = policy.effective_revision(target_id, version=version)
    builder = MODEL_BUILDERS[settings["model"]]
    # Normalize and validate on EVERY call, including hits. The population owns
    # all fitting inputs, including exclusions, source identities and estimates.
    # Keep callable identities in the namespace so replaced/instrumented fitters
    # cannot silently reuse results produced by another implementation.
    namespace = (builder, score_evaluator, fit_ladder, compile_curve, _fit_population)
    inputs = (asdict(population), settings, revision, OVERALL_MODEL_VERSION, DIVISION_SCORES)
    try:
        key = (*namespace, frozen_inputs(inputs))
        hash(key)
    except (TypeError, ValueError):
        # Custom provenance remains supported without coercing its types/values.
        return _fit_population(population, settings, builder, revision)
    return _FIT_CACHE.get_or_compute(
        key, lambda: _fit_population(population, settings, builder, revision))


def _fit_population(population, settings, builder, policy_revision):
    """Uncached numeric fit from the complete validated population."""
    times = list(population.best_by_runner.values()) or list(population.proxy_times)
    if not times:
        return None
    community = _community_curve(times, settings)
    families = _family_summaries(population, settings)
    evaluate, details = builder(population, community, settings, families)
    nodes = _calibrate(evaluate, community, max(12000, max(times) * 3))
    metadata = {**population.metadata, "model": settings["model"],
                "model_version": OVERALL_MODEL_VERSION,
                "policy_revision": policy_revision,
                "family_count": len(families), "families": families, **details,
                "single_family_community": len(families) <= 1,
                "frontier": {"method": ("estimated_source_evidence" if population.metadata["estimated"]
                                         else "refitted_community_with_observed_record"),
                             "best_observed_cs": min(times) if population.best_by_runner else None,
                             "best_evidence_cs": min(times),
                             "ceiling_cs": nodes[0][0],
                             "mario_cs": time_for_score(community, 95),
                             "metal_cs": time_for_score(community, 90)},
                "minimum_frames_per_division": 1}
    return compile_curve(nodes, metadata=metadata)
