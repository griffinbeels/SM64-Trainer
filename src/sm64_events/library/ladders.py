"""Complete Sheet ladders, fitted independently for every row and region.

Observations anchor an elite Mario I target and the slower empirical quantiles.
Every tier remains present. Where the sample cannot separate subdivisions, the
ladder extends slower by one game frame per division (user, 2026-09-06).
These lower targets are provisional; additional submissions refine the fit.
"""
import math

from sm64_events.core.timefmt import attainable_cs, cs_of_frame, frame_at_or_after
from sm64_events.library.ladder_estimates import estimate_times
from sm64_events.library.strategy_signature import matching_profile
from sm64_events.ranks.classify import RANK_NAMES

# Mario's percentile anchors division I, not the tier's V cutoff. The interior
# positions retain the empirically measured community shape; Bronze uses the
# slowest observation, so even a real slow outlier participates in the fit.
LADDER_PERCENTILES = {
    "Mario": 6.7, "Grandmaster": 21.7, "Master": 45.0, "Diamond": 65.2,
    "Platinum": 80.4, "Gold": 89.3, "Silver": 94.0, "Bronze": 100.0,
}
MIN_ENTRIES = 1
LADDER_MODEL_VERSION = 3


def _at_percentile(times, percent):
    """Linear-interpolated quantile over sorted centiseconds."""
    position = max(0., min(100., percent)) / 100 * (len(times) - 1)
    low = int(position)
    high = min(low + 1, len(times) - 1)
    return times[low] + (position - low) * (times[high] - times[low])


def place_at_percentiles(times, percentiles):
    return {rank: _at_percentile(times, percentiles[rank])
            for rank in RANK_NAMES if rank in percentiles}


def make_attainable(raw, quantise=attainable_cs):
    """Round gently, retaining five reachable frame divisions per tier."""
    out, previous = {}, None
    for rank in RANK_NAMES:
        if rank not in raw:
            continue
        cutoff = quantise(int(round(raw[rank])))
        if previous is not None:
            cutoff = max(cutoff, cs_of_frame(frame_at_or_after(previous) + 5))
        out[rank] = cutoff
        previous = cutoff
    return out


def fit_ladder(times_cs, percentiles=None, quantise=attainable_cs):
    """Eight tier cutoffs in seconds; 40 divisions plus the derived Capless five.

    Mario I starts at the elite quantile, at least one frame slower than the
    fastest observation. Nine subdivision steps connect it to Metal V. Their
    spacing is a whole number of frames, so the top extrapolation lands exactly
    on the intended elite target. Slower tiers follow empirical quantiles, with
    a minimum five-frame separation to leave a frame for every subdivision.
    """
    times = sorted(int(t) for t in times_cs if t > 0)
    if not times:
        return {}
    raw = place_at_percentiles(times, percentiles or LADDER_PERCENTILES)
    elite = max(frame_at_or_after(times[0]) + 1,
                frame_at_or_after(round(raw["Mario"])))
    metal = frame_at_or_after(round(raw["Grandmaster"]))
    step = max(1, math.ceil((metal - elite) / 9))
    raw["Mario"] = cs_of_frame(elite + 4 * step)
    raw["Grandmaster"] = cs_of_frame(elite + 9 * step)
    return {rank: round(cutoff / 100, 2)
            for rank, cutoff in make_attainable(raw, quantise).items()}


def row_times(item):
    """(times, which ROM version they came from) — the population a ladder is
    fitted over. NEVER a mix of two versions.

    A (JP)/(US) pair merges into ONE approach carrying both populations, and on
    JRB's stone pillar those are 10.80 and 14.50: a ladder fitted across that
    pile spans a gap no single player can be on both sides of. US is preferred
    because that is the convention `tools/scrape_ranks.py` applies to the
    vetted ladders ("US where a US time exists, else JP"). Every populated JP
    companion fits separately, however small either population is. Unannotated
    times provide the combined base when only a JP companion is annotated."""
    entries = item["entries"]
    by_version = {}
    for entry in entries:
        by_version.setdefault(entry.get("version"), []).append(entry["time_cs"])
    for version in ("us", None, "jp"):
        if by_version.get(version):
            return sorted(by_version[version]), version
    return [], None


def fit_payload(payload: dict) -> dict:
    """Fit every row from observations, its own anchor, or a named related row.

    Estimates never count as submissions or become another estimate's source.
    Refitting clears their provenance as soon as real observations arrive."""
    populations = [(target, kind, item, *row_times(item))
                   for target in payload["targets"]
                   for kind in ("approaches", "subsections")
                   for item in target[kind]]
    fitted = estimated = missing = 0
    for target, kind, item, times, version in populations:
        item["ladder_samples"] = len(times)
        item.pop("ladder_estimate", None)
        item.pop("ladder_extended", None)
        if not times:
            times, version, provenance = estimate_times(
                target, kind, item, populations)
            if provenance:
                item["ladder_estimate"] = provenance
        ladder = fit_ladder(times)
        item["matching_profile"] = matching_profile(times)
        if ladder:
            item["ladder"] = ladder
            item["ladder_version"] = version
            item["ladder_extended"] = round(ladder["Bronze"] * 100) > attainable_cs(max(times))
            fitted += 1
            estimated += bool(item.get("ladder_estimate"))
        else:
            item.pop("ladder", None)
            item.pop("ladder_version", None)
            missing += 1
        jp_times = sorted(e["time_cs"] for e in item["entries"]
                          if e.get("version") == "jp")
        item.pop("ladder_jp", None)
        item.pop("ladder_jp_samples", None)
        item.pop("ladder_jp_extended", None)
        if version != "jp" and jp_times:
            item["ladder_jp"] = fit_ladder(jp_times)
            item["ladder_jp_samples"] = len(jp_times)
            item["ladder_jp_extended"] = round(item["ladder_jp"]["Bronze"] * 100) > attainable_cs(max(jp_times))
    payload["ladder_model"] = {
        "version": LADDER_MODEL_VERSION,
        "percentiles": dict(LADDER_PERCENTILES),
        "min_entries": MIN_ENTRIES,
        "mario_percentile_division": "I",
        "minimum_frames_per_division": 1,
        "source": "sheet",
        "fitted_rows": fitted,
        "estimated_rows": estimated,
        "rows_without_evidence": missing,
        "rows_too_thin": 0,
    }
    return payload
