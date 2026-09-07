"""Sparse coverage through the real bundled rows, without rewriting the seed."""
import gzip
import json
from pathlib import Path

import pytest

from sm64_events.core.timefmt import attainable_cs
from sm64_events.library.audit import row_key
from sm64_events.library.ladders import fit_ladder, fit_payload


@pytest.fixture
def payload():
    path = (Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "data"
            / "sheet_library.seed.json.gz")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _rows(payload):
    return [(target, kind, item) for target in payload["targets"]
            for kind in ("approaches", "subsections") for item in target[kind]]


def test_every_bundled_approach_and_subsection_gets_standards(payload):
    fit_payload(payload)
    rows = _rows(payload)
    assert len(rows) >= 634
    assert not [(t["label"], i["name"]) for t, _, i in rows if not i.get("ladder")]
    assert payload["ladder_model"]["rows_without_evidence"] == 0
    for _, _, item in rows:
        if item["entries"]:
            assert "ladder_estimate" not in item
        else:
            assert item["ladder_samples"] == 0
            assert item["ladder_estimate"]["note"]


def test_an_own_anchor_makes_a_complete_ladder_and_no_fake_submissions():
    row = {"name": "A", "ids": ["1"], "entries": [], "ideal_cs": 501}
    target = {"section": "Course", "label": "Target", "approaches": [row],
              "subsections": []}
    fit_payload({"targets": [target]})
    assert row["ladder"] == fit_ladder([501])
    assert row["ladder_estimate"]["method"] == "ideal"
    assert row["ladder_estimate"]["source_rows"] == [row_key(target, "A", ["1"])]
    assert row["ladder_samples"] == 0 and row["entries"] == []
    row["best_cs"] = 530
    fit_payload({"targets": [target]})
    assert row["ladder"] == fit_ladder([530])
    assert row["ladder_estimate"]["method"] == "best"


def test_a_best_anchor_respects_the_published_region_instead_of_the_faster_best():
    row = {"name": "A", "ids": ["1", "2"], "entries": [], "best_cs": 1000,
           "times": {"us": 1500, "jp": 1000}}
    target = {"section": "Course", "label": "Target", "approaches": [row],
              "subsections": []}
    fit_payload({"targets": [target]})
    assert row["ladder"] == fit_ladder([1500])
    assert row["ladder_version"] == "us"
    assert row["ladder_estimate"]["source_version"] == "us"


def test_the_three_related_estimates_name_real_sources_of_the_same_timing_scope(payload):
    fit_payload(payload)
    rows = _rows(payload)
    by_key = {row_key(t, i["name"], i["ids"]): (kind, i) for t, kind, i in rows}
    estimates = [(kind, i) for _, kind, i in rows
                 if i.get("ladder_estimate", {}).get("method") == "related_row"]
    assert len(estimates) == 3
    for kind, item in estimates:
        provenance = item["ladder_estimate"]
        source_kind, source = by_key[provenance["source_rows"][0]]
        assert source_kind == kind
        assert source["entries"] and "ladder_estimate" not in source
        assert item["ladder"] == source["ladder"]
        assert item["ladder_samples"] == 0
        assert provenance["source_samples"] == source["ladder_samples"]
    us = next(i for _, i in estimates if i["name"].startswith("Post igloo"))
    assert us["ladder_version"] == "us"
    assert us["ladder_estimate"]["source_version"] == "jp"


def test_an_estimate_refreshes_from_its_source_and_yields_to_the_first_submission(payload):
    fit_payload(payload)
    rows = _rows(payload)
    item = next(i for _, _, i in rows if i["name"] == "Inside the slide (75c)")
    key = item["ladder_estimate"]["source_rows"][0]
    source = next(i for t, _, i in rows if row_key(t, i["name"], i["ids"]) == key)
    source["entries"] = [{"time_cs": 3100}, {"time_cs": 3103}]
    fit_payload(payload)
    assert item["ladder"] == fit_ladder([3100, 3103])
    assert item["ladder_estimate"]["source_samples"] == 2
    item["entries"] = [{"time_cs": 2950}]
    fit_payload(payload)
    assert item["ladder"] == fit_ladder([2950])
    assert item["ladder_samples"] == 1 and "ladder_estimate" not in item


def test_an_unknown_empty_subsection_does_not_inherit_its_whole_target_time():
    subsection = {"name": "New piece", "ids": ["1"], "entries": []}
    payload = {"targets": [{"section": "Course", "label": "Target",
        "approaches": [{"name": "Whole", "entries": [{"time_cs": 10000}]}],
        "subsections": [subsection]}]}
    fit_payload(payload)
    assert "ladder" not in subsection
    assert payload["ladder_model"]["rows_without_evidence"] == 1
