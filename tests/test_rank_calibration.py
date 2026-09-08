"""Readers must not combine two published generations during a refresh."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from sm64_events.ranks.calibration import (
    CalibrationRegistry, build_calibration, observation_fingerprint,
)


def candidate(time=1200, *, policy="one"):
    return build_calibration(
        {"sheet_revision": "unchanged", "targets": [{"entries": [{"time_cs": time}]}]},
        {"row": "star:2:4"}, {"star:2:4": {"strategies": {"A": {"Mario": time / 100}}}},
        {}, {"star:2:4": "star:2:4"}, policy)


def test_read_pins_observations_and_generated_standards_together():
    registry = CalibrationRegistry()
    old, fresh = candidate(), candidate(1100)
    registry.publish(old)
    with registry.pin():
        with ThreadPoolExecutor(max_workers=1) as worker:
            worker.submit(registry.publish, fresh).result()
        assert registry.read is old
        assert registry.read.payload["targets"][0]["entries"][0]["time_cs"] == 1200
        assert registry.read.layers["star:2:4"]["strategies"]["A"]["Mario"] == 12
        with registry.pin(current=True):
            assert registry.read is fresh
        assert registry.read is old
    assert registry.read is fresh


def test_candidate_detaches_inputs_and_corrections_change_revision():
    old, fresh = candidate(), candidate(1100)
    assert old.data_revision != fresh.data_revision
    assert old.revision != fresh.revision
    policy_changed = candidate(policy="two")
    assert old.data_revision == policy_changed.data_revision
    assert old.revision != policy_changed.revision
    inputs = {"targets": []}
    prepared = build_calibration(inputs, {}, {}, {}, {})
    inputs["targets"].append({"name": "later"})
    assert prepared.payload["targets"] == []


def test_fetch_timestamp_and_generated_ladders_are_not_source_observations():
    old = {"targets": [{"entries": [{"time_cs": 100}], "ladder": {"Mario": 1}}]}
    new = {"fetched_at": "later", "targets": [
        {"entries": [{"time_cs": 100}], "ladder": {"Mario": 2}}]}
    assert observation_fingerprint(old) == observation_fingerprint(new)
    new["targets"][0]["entries"][0]["time_cs"] = 99
    assert observation_fingerprint(old) != observation_fingerprint(new)


def test_invalid_candidate_does_not_replace_last_valid_revision():
    registry = CalibrationRegistry()
    registry.publish(candidate())
    old = registry.active
    with pytest.raises(ValueError):
        registry.publish(candidate(float("nan")))
    assert registry.active is old
