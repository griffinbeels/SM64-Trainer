"""A time is graded against the ROM that set it, not the one that is running.

Every stored time before this feature was implicitly "whatever was running",
which is fine while the only way to record one is to play it here. An imported
time breaks that: a JP time is genuinely faster than the same star on US, so
graded against a US ladder it reads as superhuman.

`StandardsStore.ladders(ek, version)` has always resolved per call — the gap
was that nothing STORED a time's version. These pin that the stored value now
reaches the ladder lookup, and that a time with no version grades exactly as it
did before.
"""
import json

from sm64_events.ranks.standards import RankStandards
from sm64_events.tracking.views import (_section_banner, _strat_rank,
                                        entity_rank, grading_basis)

# The US ladder puts Mario at 44.90 and the JP overlay at 44.00, so 44.50s is
# Mario on US and short of it on JP -- one time, two honest answers.
US_MARIO, JP_MARIO = 44.90, 44.00
TIME_CS = 4450
TIME_FRAMES = 1335          # timefmt.frame_at_or_after(4450)


def ranks(tmp_path):
    path = tmp_path / "rs.json"
    path.write_text(json.dumps({"version": 1, "entities": {
        "star:1:0": {
            "clock": "igt",
            "strategies": {"Standard": {
                "Mario": US_MARIO, "Grandmaster": 45.50, "Master": 46.70,
                "Diamond": 47.46, "Platinum": 49.10, "Gold": 50.10,
                "Silver": 59.06, "Bronze": 63.10}},
            "jp_strategies": {"Standard": {
                "Mario": JP_MARIO, "Grandmaster": 44.60, "Master": 45.46,
                "Diamond": 46.23, "Platinum": 47.86, "Gold": 48.86,
                "Silver": 57.83, "Bronze": 61.86}}}}}))
    store = RankStandards(path)
    store.load()
    store.grading_version = "us"
    return store


def pb(version=None):
    return {"course_id": 1, "star_id": 0, "segment_id": None,
            "strat_tag": "Standard", "timer_mode": "igt",
            "frames": TIME_FRAMES, "attempt_id": None,
            "game_version": version, "imported_from": "manual"}


def test_the_basis_carries_the_pbs_own_version():
    basis = grading_basis("pb", pb("jp"), [], "Standard", "igt")
    assert basis["version"] == "jp"


def test_a_basis_with_no_pb_version_carries_none():
    basis = grading_basis("pb", pb(), [], "Standard", "igt")
    assert basis["version"] is None


def test_an_average_basis_carries_no_version(tmp_path):
    """An average is over ATTEMPTS, which store no version -- so it grades on
    the running one, exactly as it always has."""
    basis = grading_basis("avg10", None, [], "Standard", "igt")
    assert basis is None or basis.get("version") is None


def test_a_jp_time_grades_on_the_jp_ladder_while_us_is_running(tmp_path):
    store = ranks(tmp_path)
    on_us = _strat_rank(store, "star:1:0", "Standard",
                        grading_basis("pb", pb(), [], "Standard", "igt"))
    on_jp = _strat_rank(store, "star:1:0", "Standard",
                        grading_basis("pb", pb("jp"), [], "Standard", "igt"))
    assert store.grading_version == "us"
    assert on_us["rank"] == "Mario"
    assert on_jp["rank"] != "Mario"


def test_the_section_banner_agrees_with_the_rank(tmp_path):
    """Two surfaces reading one time must not disagree about which ladder it
    is on."""
    store = ranks(tmp_path)
    for version in (None, "jp"):
        basis = grading_basis("pb", pb(version), [], "Standard", "igt")
        banner = _section_banner(store, "star:1:0", "Standard", basis, "pb")
        assert banner["rank"] == _strat_rank(
            store, "star:1:0", "Standard", basis)["rank"]


def test_the_entity_rank_follows_the_same_version(tmp_path):
    """entity_rank is the number MARELO aggregates, so a JP time landing there
    on a US best-possible ladder would inflate the whole rating."""
    store = ranks(tmp_path)
    assert entity_rank(store, "star:1:0", TIME_FRAMES)["rank"] == "Mario"
    assert entity_rank(store, "star:1:0", TIME_FRAMES,
                       version="jp")["rank"] != "Mario"


def test_a_versionless_time_grades_exactly_as_before(tmp_path):
    """The no-regression case: every row written before this feature has a
    NULL version, so nothing about a played best may change."""
    store = ranks(tmp_path)
    basis = grading_basis("pb", pb(), [], "Standard", "igt")
    assert _strat_rank(store, "star:1:0", "Standard", basis) == \
        _strat_rank(store, "star:1:0", "Standard",
                    {"frames": TIME_FRAMES, "count": 1, "window": None})
