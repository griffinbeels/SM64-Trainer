"""Best-ever identities survive lost clips and stay separate from saved PB slots."""
from dataclasses import replace

import pytest

from sm64_events.ghost.library_bests import comparison, historical_bests
from sm64_events.storage.db import Database
from sm64_events.tracking.projection import Attempt
from tests.test_ghost_sharing import Store, provider, segment


def run(number, frames, **values):
    return Attempt(id=number, session_id=1, course_id=2, star_id=1, strat_tag="Standard",
                   anchor_type="practice_reset", anchor_frame=0, outcome="success", outcome_detail=None,
                   igt_frames=frames, rta_frames=frames, started_utc="2026-10-01T00:00:00+00:00",
                   ended_utc="2026-10-01T00:00:10+00:00", cleared=False, cleared_reason=None,
                   valid=None, **values)


def test_missing_historical_winner_is_not_replaced_by_surviving_slower_recording(tmp_path):
    first, slow = run(1, 180, game_version="us"), run(2, 240, game_version="us")
    store = Store(attempts=[first, slow])
    available = [{"attempt_id": 2, "saved": True}]
    best, = historical_bests(store, available)
    assert (best["attempt_id"], best["frames"], best["available"]) == (1, 180, False)
    assert "no longer available" in best["reason"]
    assert store.writes == []


@pytest.mark.parametrize("invalid", [
    {"valid": False}, {"cleared": True}, {"anchor_type": "state_loaded"},
    {"state_loads": 1}, {"invalid_reason": "save_state"}, {"outcome": "death"},
    {"timed_at": "grab"}, {"igt_frames": 0}, {"igt_frames": -1},
])
def test_proven_invalid_runs_cannot_win_but_unknown_legacy_validity_can(invalid):
    legal = run(2, 200)
    bad = replace(run(1, 100), **invalid)
    best, = historical_bests(Store(attempts=[bad, legal]))
    assert best["attempt_id"] == 2


def test_strategy_clock_version_and_segment_winners_remain_distinct():
    runs = [run(1, 200, game_version="us"), run(2, 190, game_version="jp"),
            replace(run(3, 180), strat_tag="Slower route"),
            replace(run(4, 100), igt_frames=None, rta_frames=300),
            replace(run(5, 150), course_id=None, star_id=None, segment_id=3)]
    bests = historical_bests(Store(attempts=runs, definitions=[segment(3)]))
    assert len(bests) == 5
    assert {row["clock"] for row in bests} == {"igt", "rta"}
    assert next(row for row in bests if row["kind"] == "segment")["entity_key"] == "3"


def test_equal_times_keep_stable_first_identity_regardless_of_disk():
    best, = historical_bests(Store(attempts=[run(1, 200), run(2, 200)]), [{"attempt_id": 2}])
    assert best["attempt_id"] == 1 and not best["available"]


def test_saved_pb_latest_behavior_is_unchanged_and_imported_fastest_time_is_visible(tmp_path):
    database = Database(tmp_path / "history.db")
    try:
        database.insert_pb(2, 1, "Standard", "igt", 100, None, "2026-10-01T00:00:00Z", game_version="us")
        latest = database.insert_pb(2, 1, "Standard", "igt", 200, None, "2026-10-02T00:00:00Z", game_version="us")
        assert database.current_pb(2, 1, "igt", strat_tag="Standard")["id"] == latest
        best, = historical_bests(database)
        assert best["frames"] == 100 and best["id"].startswith("time:") and not best["available"]
        assert best["attempt_id"] is None and best["import_id"] is None
    finally:
        database.close()


def test_sql_history_beats_later_slower_saved_slot_and_excludes_proven_invalid(tmp_path):
    database = Database(tmp_path / "recorded.db")
    try:
        fastest, slow = run(1, 180, game_version="us"), run(2, 240, game_version="us")
        database.replace_attempts([fastest, slow, replace(run(3, 100), valid=False),
                                   replace(run(4, 90), timed_at="grab"),
                                   replace(run(5, 80), anchor_type="state_loaded")])
        database.insert_pb(2, 1, "Standard", "igt", 180, 1, "2026-10-01T00:00:00Z", game_version="us")
        latest = database.insert_pb(2, 1, "Standard", "igt", 240, 2, "2026-10-02T00:00:00Z", game_version="us")
        bests = historical_bests(database, [{"attempt_id": 2, "saved": True}])
        best, = bests
        assert best["attempt_id"] == 1 and best["frames"] == 180 and not best["available"]
        assert database.current_pb(2, 1, "igt", strat_tag="Standard")["id"] == latest
    finally:
        database.close()


def test_faster_comparison_requires_same_strategy_clock_and_explicit_version():
    bests = historical_bests(Store(attempts=[run(1, 200, game_version="us")]))
    incoming = {**bests[0], "frames": 190, "source": "import"}
    assert comparison(incoming, bests) == ("faster", 200)
    assert comparison({**incoming, "frames": 200}, bests) == ("not_faster", 200)
    for values in ({"game_version": None}, {"practice": True}, {"outcome": "reset"}, {"entity_key": None}):
        assert comparison({**incoming, **values}, bests) == ("unknown", None)
    for values in ({"game_version": "jp"}, {"strat": "Other"}, {"clock": "rta"}):
        assert comparison({**incoming, **values}, bests) == ("no_best", None)


def test_catalog_has_real_best_and_shortcuts_without_mutating_history(tmp_path):
    store = Store(attempts=[run(1, 200)])
    catalog = provider(tmp_path, store).catalog()
    assert catalog["ghosts"] == [] and catalog["bests"][0]["attempt_id"] == 1
    assert catalog["shortcuts"] == [] and store.writes == []
