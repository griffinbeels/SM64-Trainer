"""The section payload's entity-level rank -- the star/segment number that is
graded on the best-possible ladder rather than the active strategy's -- plus
the strat-level score the section banner now carries (spec
2026-07-24-marelo-rank-system-design section 4)."""
from sm64_events.ranks import scoring
from sm64_events.tracking import views


class FakeRanks:
    def __init__(self, data):
        self._data = data

    def ladders(self, key):
        return self._data.get(key, {})

    def ladder_cs(self, key, strat):
        return {rank: int(round(seconds * 100))
                for rank, seconds in self._data.get(key, {}).get(strat, {}).items()}

    def clock_for(self, key):
        return "rta" if key.startswith("segment:") else "igt"

    def strategies(self, key):
        return list(self._data.get(key, {}).keys())


RANKS = FakeRanks({"star:1:0": {"Fast": {"Mario": 45.0, "Gold": 60.0},
                                "Slow": {"Mario": 50.0, "Gold": 65.0}}})


def test_grading_basis_and_valid_frames_are_public():
    assert callable(views.grading_basis)
    assert callable(views.valid_frames)


def test_entity_rank_grades_the_best_possible_ladder():
    out = views.entity_rank(RANKS, "star:1:0", 1350)     # 45.00s
    assert out["tier"] == "Mario" and out["score"] == 95.0
    assert out["division"]


def test_entity_rank_of_a_slow_strat_time_is_below_mario():
    out = views.entity_rank(RANKS, "star:1:0", 1500)     # 50.00s
    assert out["tier"] != "Mario"


def test_entity_rank_is_none_without_standards_or_without_a_time():
    assert views.entity_rank(RANKS, "star:9:9", 1350) is None
    assert views.entity_rank(RANKS, "star:1:0", None) is None


def test_entity_rank_reports_the_strategy_that_owns_the_best_ladder():
    """Fast is faster at BOTH tiers, exactly the live user report (Sign Clip
    dominating WK Over Wall) -- the number should explain itself with a name,
    not just a lower tier than the strategy banner next to it."""
    out = views.entity_rank(RANKS, "star:1:0", 1350)
    assert out["fastest_strat"] == "Fast"


def test_fastest_strategy_is_well_defined_on_ragged_ladders():
    """A strategy that only defines the hardest tier still wins it, even
    though it has no entry at the easier tier at all."""
    ranks = FakeRanks({"star:2:0": {
        "OnlyMario": {"Mario": 10.0},                  # no Gold entry
        "Both": {"Mario": 12.0, "Gold": 20.0}}})
    ladder = scoring.best_ladder(ranks.ladders("star:2:0"))
    assert views._fastest_strategy(ranks, "star:2:0", ladder) == "OnlyMario"


def test_fastest_strategy_tiebreaks_on_the_next_shared_tier_then_alphabetically():
    ranks = FakeRanks({"star:3:0": {
        "TiesAtMario": {"Mario": 10.0, "Gold": 25.0},
        "AlsoTiesAtMario": {"Mario": 10.0, "Gold": 18.0}}})  # wins Gold outright
    ladder = scoring.best_ladder(ranks.ladders("star:3:0"))
    assert views._fastest_strategy(ranks, "star:3:0", ladder) == "AlsoTiesAtMario"

    fully_tied = FakeRanks({"star:4:0": {
        "Zebra": {"Mario": 10.0}, "Ant": {"Mario": 10.0}}})
    tied_ladder = scoring.best_ladder(fully_tied.ladders("star:4:0"))
    assert views._fastest_strategy(fully_tied, "star:4:0", tied_ladder) == "Ant"


def test_section_banner_carries_the_active_strategys_own_score():
    """_section_banner's score is the ACTIVE strategy's OWN ladder run through
    scoring.score_for -- the column the standards table actually renders, so
    ui/components/standards.js never has to re-implement the curve in JS."""
    basis = {"frames": 1350, "count": 1, "window": None}
    out = views._section_banner(RANKS, "star:1:0", "Fast", basis, "pb")
    ladder = RANKS.ladder_cs("star:1:0", "Fast")
    assert out["score"] == scoring.score_for(ladder, views.classify.display_cs(1350))


def test_section_banner_carries_the_active_strategys_division():
    """The strategy banner's division must come from the SAME
    scoring.division_for path as EntityRankTag's -- never computed in JS."""
    basis = {"frames": 1350, "count": 1, "window": None}
    out = views._section_banner(RANKS, "star:1:0", "Fast", basis, "pb")
    ladder = RANKS.ladder_cs("star:1:0", "Fast")
    expected_tier, expected_division = scoring.division_for(
        out["score"], scoring.defined_tiers(ladder))
    assert out["rank"] == expected_tier
    assert out["division"] == expected_division


def test_star_and_segment_sections_both_carry_entity_rank(tmp_path):
    """Star and segment section payloads both carry 'entity_rank' next to
    'rank' -- rule 11 (star/segment parity). The star's ladder is graded
    (a real dict); the segment has no standards, so its entity_rank is the
    honest None -- the key must still be PRESENT, not merely absent-safe."""
    import asyncio
    import json

    from sm64_events.ranks.standards import RankStandards
    from test_views import make, seed, seg_section

    db, svc = make(tmp_path)
    seed(svc)
    p = tmp_path / "rs.json"
    p.write_text(json.dumps({"version": 1, "entities": {
        "star:2:2": {"clock": "igt", "strategies": {
            "fast": {"Mario": 11.0, "Diamond": 12.0, "Silver": 13.0}}}}}))
    svc.ranks = RankStandards(p)
    svc.ranks.load()
    asyncio.run(svc.set_strat(2, 2, "fast"))
    # A PB ranks ONLY the strat it was achieved with (per-strategy ranking) --
    # tag the seeded attempts 'fast' before saving, then save the faster one.
    db._conn.execute("UPDATE attempts SET strat_tag='fast' WHERE course_id=2")
    db._conn.commit()
    best_aid = next(a.id for a in db.attempts() if a.igt_frames == 343)
    asyncio.run(svc.save_pb(best_aid, "igt"))
    asyncio.run(svc.set_target_segment(1))
    view = views.build_session_view(db, svc, clock="igt")
    [star_sec] = view["stars"]
    assert "entity_rank" in star_sec
    assert star_sec["entity_rank"]["tier"] == "Diamond"     # 343f -> 11.43s

    seg_sec = seg_section(view, 1)
    assert "entity_rank" in seg_sec
    assert seg_sec["entity_rank"] is None                   # no standards for it
