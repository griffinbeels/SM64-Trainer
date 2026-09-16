"""Bowser reds star grabs and complete pipe entries are different score targets."""
from copy import deepcopy

import pytest

from sm64_events.library import placements, ratings
from sm64_events.library.audit import row_key
from sm64_events.ranks import curves


def target(course=16):
    return {"group": "Bowser Courses", "section": "Bowser Courses",
            "label": "Bowser Red Coins", "entity_key": f"star:{course}:0",
            "version": None, "subsections": [], "approaches": [
                {"name": "Bowser Red Coins", "ids": ["1"],
                 "matched_strategy": "Known (Pipe)",
                 "entries": [{"runner": "Runner", "time_cs": 6000}]},
                {"name": "Red coin star Xcam", "ids": ["1"],
                 "matched_strategy": "Ultimate Cycle (Pipe)",
                 "entries": [{"runner": "Runner", "time_cs": 4500}]},
                {"name": "New route pipe entry", "ids": ["2"],
                 "matched_strategy": None,
                 "entries": [{"runner": "Runner", "time_cs": 5800}]}]}


def definitions(course=16):
    abbrev, level = {16: ("bitdw", 17), 17: ("bitfs", 19), 18: ("bits", 21)}[course]
    return [{"id": 71, "seed_key": f"seg:{abbrev}-pipe",
             "start_triggers": [{"type": "level_enter", "to": level}]},
            {"id": 92, "seed_key": f"seg:reds->pipe:{abbrev}",
             "start_triggers": [{"type": "level_enter", "to": level}]}]


def key(source, item):
    return row_key(source, item["name"], item["ids"])


@pytest.mark.parametrize("course", [16, 17, 18])
def test_xcam_wording_wins_and_pipe_rows_use_the_reds_inclusive_local_segment(course):
    source = target(course)
    original = deepcopy(source)
    rows = placements.scoring_rows({"targets": [source]}, {}, definitions(course))
    assert [rows[key(source, item)] for item in source["approaches"]] == [
        "segment:92", f"star:{course}:0", "segment:92"]
    assert source == original
    # The ordinary strategy/import placement keeps its historical star home.
    assert {placements.row_identity(source, item, "approach", {})[0]
            for item in source["approaches"]} == {f"star:{course}:0"}


def test_missing_or_deleted_pair_never_falls_back_to_no_reds_or_star():
    source = target()
    pipe, star, other_pipe = source["approaches"]
    rows = placements.scoring_rows({"targets": [source]}, {}, definitions()[:1])
    assert rows[key(source, pipe)] == rows[key(source, other_pipe)] == ""
    assert rows[key(source, star)] == "star:16:0"
    assert placements.scoring_identity(source, pipe, "approach", rows) is None


def test_deliberate_other_entity_assignments_and_unlinks_survive():
    source = target()
    first, star, other = source["approaches"]
    assigned = {key(source, first): "segment:777", key(source, star): "",
                key(source, other): "star:2:0", "unrelated": "segment:5"}
    assert placements.scoring_rows({"targets": [source]}, assigned, definitions()) == assigned
    assert placements.scoring_identity(source, first, "approach", assigned)[0] == "segment:777"


def test_legacy_scoring_without_generation_still_rejects_pipe_times_on_star():
    source = target()
    pipe, star, _ = source["approaches"]
    assert placements.scoring_identity(source, pipe, "approach", {}) is None
    assert placements.scoring_identity(source, star, "approach", {})[0] == "star:16:0"


def test_board_reads_the_published_scoring_map_without_moving_display_rows():
    source = target()
    payload = {"targets": [source]}

    class Standards:
        scoring_rows = placements.scoring_rows(payload, {}, definitions())

        def clock_for(self, entity):
            return "rta" if entity.startswith("segment:") else "igt"

        def overall_curve(self, entity, version=None):
            cutoff = 4500 if entity == "star:16:0" else 5800
            return curves.from_ladder({"Mario": cutoff, "Gold": cutoff + 2000})

    rated = ratings.rate_runners(payload, Standards(), {})
    assert rated.times["Runner"] == {"star:16:0": 4500, "segment:92": 5800}
    assert rated.scores["Runner"] == {"star:16:0": 95., "segment:92": 95.}
    assert ratings.runner_times(payload, {})["Runner"] == {"star:16:0": 4500}
