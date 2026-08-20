"""Reading one runner's sheet column as importable times.

Every count here was measured against the bundled snapshot on 2026-08-20
(revision 2026-08-10T15:14:43). A re-scrape moves them; re-measure rather than
loosening the assertion, because a fixture that stops naming a real number
stops being evidence.
"""
import gzip
import json
from pathlib import Path

import sm64_events
from sm64_events.library.import_runner import candidates_for


def payload():
    seed = (Path(sm64_events.__file__).parent / "data"
            / "sheet_library.seed.json.gz")
    return json.loads(gzip.decompress(seed.read_bytes()).decode("utf-8"))


def test_dentoriousred_maps_to_fourteen_times_over_ten_stars():
    candidates, rejected = candidates_for(payload(), "DentoriousRed")
    assert len(candidates) == 14
    assert len({c.entity_key for c in candidates}) == 10
    assert rejected == {"subsections": 0, "no_entity": 1, "segments": 2}


def test_a_segment_row_is_never_imported():
    """Six of the snapshot's targets map to a segment, and a segment id is
    LOCAL to each database -- landing one would attribute a time to whatever
    that id happens to name here. They are also RTA-only while every sheet
    approach time is an IGT star time."""
    candidates, rejected = candidates_for(payload(), "DentoriousRed")
    assert rejected["segments"] == 2
    assert not any(c.entity_key.startswith("segment:") for c in candidates)


def test_every_candidate_names_a_star_a_strategy_and_the_igt_clock():
    candidates, _ = candidates_for(payload(), "DentoriousRed")
    assert all(c.entity_key.startswith("star:") for c in candidates)
    assert all(c.strat_tag for c in candidates)
    assert all(c.time_cs > 0 for c in candidates)
    assert all(c.timer_mode == "igt" for c in candidates)


def test_the_jp_rows_carry_their_version():
    """A JP time is genuinely faster than the same star on US, so graded
    against a US ladder it reads as superhuman. The version has to ride with
    the time."""
    candidates, _ = candidates_for(payload(), "DentoriousRed")
    assert sum(1 for c in candidates if c.game_version == "jp") == 2


def test_a_matched_strategy_wins_over_the_sheet_approach_name():
    candidates, _ = candidates_for(payload(), "DentoriousRed")
    blast = {c.strat_tag for c in candidates if c.entity_key == "star:2:5"}
    assert blast == {"Texture", "OG", "LJ"}


def test_a_subsection_never_becomes_a_personal_best():
    """A subsection times a stretch INSIDE a target. Imported as a best it
    would publish a 15.90s way of doing a 43s star."""
    runner = "Raisn"          # 93 subsection rows on the snapshot
    candidates, rejected = candidates_for(payload(), runner)
    assert rejected["subsections"] > 0
    every_approach_name = {
        approach["name"]
        for target in payload()["targets"]
        for approach in target["approaches"]}
    subsection_names = {
        piece["name"]
        for target in payload()["targets"]
        for piece in target["subsections"]} - every_approach_name
    assert not ({c.strat_tag for c in candidates} & subsection_names)


def test_an_unknown_runner_yields_nothing_rather_than_raising():
    candidates, rejected = candidates_for(payload(), "NobodyAtAll")
    assert candidates == []
    assert rejected == {"subsections": 0, "no_entity": 0, "segments": 0}


def test_an_empty_payload_is_not_an_error():
    assert candidates_for({}, "DentoriousRed") == (
        [], {"subsections": 0, "no_entity": 0, "segments": 0})
