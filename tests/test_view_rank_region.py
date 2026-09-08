"""Practice grades actual regional averages and each attempt's original ROM."""
import json
from dataclasses import replace

import pytest

from sm64_events.ranks.standards import RankStandards
from sm64_events.tracking.projection import Attempt
from sm64_events.tracking.views import (
    _attempt_rank, _ranked_basis, _strat_rank, entity_rank, grading_basis,
)

KEY = "star:2:4"


@pytest.fixture
def ranks(tmp_path):
    us = {"Mario": 10, "Grandmaster": 11, "Master": 12, "Diamond": 13,
          "Platinum": 14, "Gold": 15, "Silver": 17, "Bronze": 20}
    path = tmp_path / "standards.json"
    path.write_text(json.dumps({"entities": {KEY: {"strategies": {"Standard": us},
        "jp_strategies": {"Standard": {rank: time + 10 for rank, time in us.items()}}}}}))
    store = RankStandards(path)
    store.load()
    return store


def attempt(identifier, frames, version):
    return Attempt(id=identifier, session_id=1, course_id=2, star_id=4,
                   strat_tag="Standard", anchor_type="none", anchor_frame=None,
                   outcome="success", outcome_detail=None, igt_frames=frames,
                   cleared=False, cleared_reason=None,
                   rta_frames=None, started_utc="2026-09-08T00:00:00Z",
                   ended_utc="2026-09-08T00:00:01Z", game_version=version)


def test_regional_windows_do_not_create_a_time_never_performed():
    history = [attempt(1, 360, "us"), attempt(2, 630, "jp")]
    basis = grading_basis("avg10", None, history, "Standard", "igt")
    assert basis == {"frames": 360, "count": 1, "window": 10, "version": "us"}
    assert basis["frames"] != 495


@pytest.mark.parametrize("overall", [True, False])
def test_ranked_average_chooses_quality_on_its_own_rom_not_the_fastest_raw_time(ranks, overall):
    history = [attempt(1, 360, "us"), attempt(2, 630, "jp")]
    basis = _ranked_basis(ranks, KEY, "avg10", None, history, "Standard", "igt", overall=overall)
    assert basis == {"frames": 630, "count": 1, "window": 10, "version": "jp"}
    strategy = _strat_rank(ranks, KEY, "Standard", basis)
    overall_rank = entity_rank(ranks, KEY, basis["frames"], basis["version"])
    assert strategy["rank"] == overall_rank["rank"] == "Grandmaster"


def test_attempt_medal_grades_the_original_rom_even_when_current_setting_changes(ranks):
    saved = attempt(1, 630, "jp")
    before = _attempt_rank(saved, saved.igt_frames, ranks)
    assert before["rank"] == "Grandmaster"
    ranks.grading_version = "jp"
    assert _attempt_rank(saved, saved.igt_frames, ranks) == before


def test_regional_average_filters_failures_cleared_attempts_and_other_strategies(ranks):
    history = [attempt(i, 360, "us") for i in range(1, 13)]
    history.append(attempt(13, 630, "jp"))
    failed = replace(attempt(14, 30, "jp"), outcome="death")
    cleared = replace(attempt(15, 30, "jp"), cleared=True)
    other = replace(attempt(16, 30, "jp"), strat_tag="Other")
    basis = _ranked_basis(ranks, KEY, "avg10", None, [*history, failed, cleared, other],
                          "Standard", "igt", overall=True)
    assert basis["frames"] == 630 and basis["count"] == 1


def test_pb_mode_keeps_the_selected_saved_row_including_a_deliberately_slower_pb(ranks):
    saved = {"frames": 660, "game_version": "jp"}
    basis = _ranked_basis(ranks, KEY, "pb", saved, [attempt(1, 360, "us")], "Standard", "igt")
    assert basis == {"frames": 660, "version": "jp", "count": 1, "window": None}
