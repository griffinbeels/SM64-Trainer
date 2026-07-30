# tests/test_ranks_scoring_seed.py
"""The invariant, against every ladder the app actually ships.

Hand-written ladders in test_ranks_scoring.py are well-formed by construction.
The seed has 278 of them, ragged (missing tiers) and occasionally odd, which is
where a curve/medal disagreement would really appear."""
import json
from pathlib import Path

import pytest

from sm64_events.ranks.classify import rank_for
from sm64_events.ranks.scoring import (
    DIVISIONS_PER_TIER, best_ladder, defined_tiers, progress_for_time,
    score_for, tier_band, tier_from_score, time_for_score)

SEED = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "data" / \
    "rank_standards.seed.json"


def _ladders():
    entities = json.loads(SEED.read_text())["entities"]
    for key, entity in entities.items():
        for strat, ladder in entity.get("strategies", {}).items():
            cs = {rank: int(round(seconds * 100)) for rank, seconds in ladder.items()}
            if cs:
                yield f"{key}/{strat}", cs


def test_seed_has_ladders_to_check():
    assert sum(1 for _ in _ladders()) > 200


@pytest.mark.parametrize("name,ladder", list(_ladders()))
def test_score_and_medal_agree_at_every_boundary(name, ladder):
    """At each cutoff, one cs either side of it, and across the whole span."""
    probes = set()
    for cutoff in ladder.values():
        probes.update({cutoff - 1, cutoff, cutoff + 1})
    lo, hi = min(ladder.values()), max(ladder.values())
    probes.update(range(max(1, lo - 500), hi + 500, 13))
    defined = defined_tiers(ladder)
    for time_cs in sorted(probes):
        if time_cs <= 0:
            continue
        assert tier_from_score(score_for(ladder, time_cs), defined) == \
            rank_for(ladder, time_cs), f"{name} @ {time_cs}cs"


def test_every_seeded_entity_yields_a_usable_best_ladder():
    entities = json.loads(SEED.read_text())["entities"]
    for key, entity in entities.items():
        strategies = entity.get("strategies", {})
        if not strategies:
            continue
        best = best_ladder(strategies)
        assert best, key
        times = [best[r] for r in defined_tiers(best)]
        assert times == sorted(times), f"{key} best ladder is not monotone"


def _edge_probes(ladder):
    """Every division edge on this ladder, at its own displayed centisecond
    and one either side. These are exactly the times a hand-written fixture
    never picks and a player lands on constantly."""
    defined = defined_tiers(ladder)
    for tier in defined:
        low, high = tier_band(tier, defined)
        width = (high - low) / DIVISIONS_PER_TIER
        for index in range(DIVISIONS_PER_TIER + 1):
            edge_cs = time_for_score(ladder, low + index * width)
            if edge_cs is None:
                continue
            for probe in (edge_cs - 1, edge_cs, edge_cs + 1):
                if probe > 0:
                    yield probe


@pytest.mark.parametrize("name,ladder", list(_ladders()))
def test_no_shipped_ladder_can_owe_zero_centiseconds(name, ladder):
    """"We should never be displaying 0.00s to rank up -- we should just rank
    up" (user, 2026-07-29).

    A division edge falls at a FRACTIONAL centisecond and `time_for_score`
    rounds it to report it, so a run within half a centisecond of one used to
    be told it owed exactly 0.00s. Measured before the fix: 3,656 (ladder,
    time) pairs across these 278 ladders hit it -- it was not an edge case,
    it was every ladder, several times over."""
    for time_cs in _edge_probes(ladder):
        gap = progress_for_time(ladder, time_cs)["next_gap_cs"]
        assert gap is None or gap >= 1, \
            f"{name} at {time_cs}cs owes {gap}cs to its next rank"
