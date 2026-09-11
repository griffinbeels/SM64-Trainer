"""The backtest must retain actual portfolios, route slots and source evidence."""
from copy import deepcopy
import gzip
import json

import pytest

from sm64_events.core.timefmt import attainable_cs
from sm64_events.ranks import curves
from tools import compare_rank_calibrations as compare


def _curve():
    return curves.compile_curve([[1000, 95], [1800, 70], [2000, 61.3],
                                 [3000, 25], [6000, 5]])


def _route():
    return {"id": 71, "seed_key": "route:16-test", "name": "Sixteen test",
            "category": "Main Categories/16 Star/Test", "steps": [
                {"need": 1, "candidates": [{"type": "star", "course": 1, "star": 0},
                                            {"type": "star", "course": 1, "star": 1}]},
                {"need": 1, "candidates": [{"type": "star", "course": 1, "star": 2}]}]}


def _source():
    targets = []
    for index in range(3):
        entries = [{"runner": "Complete", "time_cs": 1600 + index * 100, "version": "us"},
                   {"runner": "JP only", "time_cs": 1500, "version": "jp"}]
        if index == 0:
            entries.append({"runner": "Partial", "time_cs": 2000, "version": "us"})
        targets.append({"entity_key": f"star:1:{index}", "group": "BOB", "section": "BOB",
                        "label": "Caged test" if index == 0 else f"Test {index}",
                        "subsections": [], "approaches": [{"name": "Standard",
                            "ids": [f"row-{index}"], "entries": entries}]})
    return {"sheet_revision": "2026-09-08T12:00:00", "targets": targets}


def _seed(tmp_path):
    path = tmp_path / "standards.seed.json"
    path.write_text(json.dumps({"version": 1, "entities": {}}), encoding="utf-8")
    return path


def test_actual_portfolios_keep_best_k_and_missing_slot_penalty():
    resolved = {f"star:1:{index}": _curve() for index in range(3)}
    observations = {"Complete": {"star:1:0": 2000, "star:1:1": 1800, "star:1:2": 2000},
                    "Partial": {"star:1:0": 2000},
                    "Other target": {"star:9:0": 1500}}
    resolved["star:9:0"] = _curve()
    report = compare.route_comparison(_route(), resolved, resolved, observations, observations)
    actual = {row["runner"]: row for row in report["runners"]}
    assert set(actual) == {"Complete", "Partial"}
    assert actual["Complete"]["after"]["marelo"] == pytest.approx((70 + 61.3) / 2)
    assert actual["Complete"]["portfolio"] == "complete"
    assert actual["Partial"]["after"]["marelo"] == pytest.approx(61.3 / 2)
    assert actual["Partial"]["after"]["coverage"] == .5
    assert actual["Partial"]["observed_times_cs"] == {"star:1:0": 2000}
    assert report["complete_portfolios"] == report["partial_portfolios"] == 1


def test_denominator_changes_are_separate_from_common_slot_score_changes():
    before = {"star:1:0": _curve(), "star:1:1": curves.from_ladder({}),
              "star:1:2": curves.from_ladder({})}
    after = {**before, "star:1:2": _curve()}
    times = {"Partial": {"star:1:0": 2000}}
    report = compare.route_comparison(_route(), before, after, times, times)
    runner = report["runners"][0]
    assert report["became_rankable"] == ["star:1:2"]
    assert (runner["before"]["n"], runner["after"]["n"]) == (1, 2)
    assert runner["delta"] == pytest.approx(-61.3 / 2)
    assert runner["common_slot_delta"] == 0
    assert report["losers"] == ["Partial"]


def test_empty_scope_is_not_a_complete_portfolio():
    before = {"star:1:0": _curve()}
    after = {"star:1:0": curves.from_ladder({})}
    report = compare.route_comparison(_route(), before, after,
                                     {"Prior": {"star:1:0": 2000}}, {})
    assert report["complete_portfolios"] == 0
    assert report["runners"][0]["after"]["marelo"] is None


def test_division_targets_are_attainable_and_frame_gains_use_full_nodes():
    report = compare.describe_curve(_curve(), frame_samples=9)
    assert report["invalid_division_goals"] == 0
    assert report["per_frame_gains"]["negative_gains"] == 0
    assert report["per_frame_gains"]["sample_count"] > 9
    assert all(row["time_cs"] == attainable_cs(row["time_cs"])
               for row in report["division_targets"] if row["time_cs"] is not None)
    assert report["curve"]["nodes"][2] == [2000., 61.3]
    assert compare.describe_curve(curves.from_ladder({}))["status"] == "unrankable"
    legacy = curves.from_ladder({"Mario": 1000, "Gold": 2000}, metadata={"source": "legacy"})
    assert compare.describe_curve(legacy)["status"] == "legacy_fallback"


def test_production_report_preserves_source_and_original_region(tmp_path):
    payload = _source()
    original = deepcopy(payload)
    defaults = {"segments": [], "routes": [_route()]}
    report = compare.generate_report(payload, defaults, _seed(tmp_path), frame_samples=5)
    assert payload == original and report["source_observations_unchanged"]
    assert report["comparison"] == "legacy_to_overall"
    assert report["targets"]["star:1:0"]["regions"]["us"]["sample_13_86"]["time_cs"] == 1386
    us = report["routes"]["us"][0]
    jp = report["routes"]["jp"][0]
    assert {row["runner"] for row in us["runners"]} == {"Complete", "Partial"}
    assert {row["runner"] for row in jp["runners"]} == {"JP only"}
    assert us["groups_after"] == [{"need": 1, "candidates": ["star:1:0", "star:1:1"]},
                                   {"need": 1, "candidates": ["star:1:2"]}]
    assert "Complete/partial portfolios" in compare.markdown_report(report)


def test_empty_policy_patch_is_a_real_noop_with_no_source_changes(tmp_path):
    payload = _source()
    report = compare.generate_report(payload, {"routes": [_route()]}, _seed(tmp_path),
                                     policy_patch={}, regions=("us",), frame_samples=3)
    assert report["comparison"] == "policy_patch"
    assert report["base_revision"] == report["candidate_revision"]
    for row in report["routes"]["us"][0]["runners"]:
        assert row["delta"] == row["common_slot_delta"] == 0


def test_scoped_policy_changes_eligible_portfolio_without_leaking_targets_or_region(tmp_path):
    payload = _source()
    payload["targets"][0]["approaches"][0]["entries"].append(
        {"runner": "No region", "time_cs": 1900, "version": None})
    patch = {"layers": {"overall": {"patches": [{"target_id": "star:1:0", "version": "us",
              "parameters": {"unannotated_region": "exclude"}}]}}}
    report = compare.generate_report(payload, {"routes": [_route()]}, _seed(tmp_path),
                                     policy_patch=patch, frame_samples=3)
    assert report["candidate_revision"] != report["base_revision"]
    us = next(row for row in report["routes"]["us"][0]["runners"] if row["runner"] == "No region")
    jp = next(row for row in report["routes"]["jp"][0]["runners"] if row["runner"] == "No region")
    assert us["before"]["practiced"] == 1 and us["after"]["practiced"] == 0
    assert jp["delta"] == 0
    for region in report["targets"]["star:1:2"]["regions"].values():
        assert region["before"] == region["after"]


def test_cli_handles_gzip_and_refuses_input_overwrite(tmp_path):
    sheet = tmp_path / "sheet.json.gz"
    sheet.write_bytes(gzip.compress(json.dumps(_source()).encode()))
    assert compare.load_json(sheet) == _source()
    before = sheet.read_bytes()
    with pytest.raises(SystemExit, match="2"):
        compare.main(["--sheet", str(sheet), "--output", str(sheet)])
    assert sheet.read_bytes() == before
    defaults = tmp_path / "defaults.json"
    defaults.write_text(json.dumps({"routes": [_route()]}), encoding="utf-8")
    output = tmp_path / "report.json"
    assert compare.main(["--sheet", str(sheet), "--defaults", str(defaults), "--standards",
                         str(_seed(tmp_path)), "--output", str(output), "--regions", "us",
                         "--frame-samples", "3"]) == 0
    result = json.loads(output.read_bytes())
    assert len(result["source_file_hashes"]) == 3
    assert result["source_observations_unchanged"] and sheet.read_bytes() == before


def test_report_rejects_unusable_defaults_and_sample_budget(tmp_path):
    seed = _seed(tmp_path)
    with pytest.raises(ValueError, match="at least 2"):
        compare.generate_report(_source(), {}, seed, frame_samples=1)
    with pytest.raises(ValueError, match="no supported 16 Star"):
        compare.generate_report(_source(), {}, seed)
