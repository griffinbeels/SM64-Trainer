"""Persistence and activation contracts for independent living rank layers."""
from copy import deepcopy
import json

import pytest

from sm64_events.core.timefmt import next_attainable_cs
from sm64_events.library import adoptions as ad
from sm64_events.library.audit import row_key
from sm64_events.library.build import SCHEMA_VERSION
from sm64_events.library.ladders import fit_payload
from sm64_events.library.store import LibraryStore
from sm64_events.ranks import curves
from sm64_events.ranks.policy import RankingPolicy
from sm64_events.ranks.standards import RankStandards

KEY = "star:1:0"
OTHER = "star:1:1"
LEGACY = "star:9:4"
LADDER = {"Mario": 10., "Grandmaster": 12., "Master": 14., "Diamond": 16.,
          "Platinum": 18., "Gold": 20., "Silver": 25., "Bronze": 30.}


def _payload():
    targets = []
    for key, name, offset in [(KEY, "First star", 0), (OTHER, "Other star", 500),
                              (None, "Test movement", 1000)]:
        entries = [{"runner": f"{version}-{index}", "time_cs": 1000 + offset + index * 30
                    + (100 if version == "us" else 0), "version": version}
                   for version in ("us", "jp") for index in range(40)]
        targets.append({"entity_key": key, "label": name, "group": "Test", "section": "Test",
                        "subsections": [], "approaches": [{"name": "Standard", "ids": [name],
                                                               "entries": entries}]})
    return {"schema_version": SCHEMA_VERSION, "sheet_revision": "2026-09-08T12:00:00",
            "runners": [], "targets": targets}


def _open(directory, payload=None, *, definitions=None, policy=None):
    directory.mkdir(exist_ok=True)
    seed = directory / "rank.seed.json"
    if not seed.exists():
        seed.write_text(json.dumps({"version": 1, "entities": {
            LEGACY: {"clock": "igt", "strategies": {"Standard": LADDER}}}}), encoding="utf-8")
    standards = RankStandards(directory / "ranks.json", seed_path=seed)
    standards.load()
    store = LibraryStore(directory / "library.json.gz")
    assignments = ad.Adoptions(directory / "assignments.json", store, standards,
                              segment_defs=lambda: definitions or [], policy=policy)
    if payload is None:
        store.load()
    else:
        store.absorb(fit_payload(deepcopy(payload)))
    assignments.load()
    return store, standards, assignments


def _resolved(standards, key=KEY):
    return {version: standards.overall_curve(key, version) for version in ("us", "jp")}


@pytest.mark.parametrize("key", [KEY, LEGACY])
def test_strategy_edits_remain_independent_of_overall_after_save_and_restart(tmp_path, key):
    store, standards, _ = _open(tmp_path, _payload())
    baseline = _resolved(standards, key)
    before = standards.ladder_cs(key, "Standard", "us")["Gold"]
    standards.set_threshold(key, "Standard", "Gold", (before + 20) / 100)
    assert _resolved(standards, key) == baseline
    revision = store.calibrations.active.revision
    restarted, fresh, _ = _open(tmp_path)
    assert fresh.ladder_cs(key, "Standard", "us")["Gold"] == before + 20
    assert _resolved(fresh, key) == baseline
    assert restarted.calibrations.active.revision == revision


def test_overall_pins_and_resets_are_independent_by_region_and_strategy(tmp_path):
    _, standards, _ = _open(tmp_path, _payload())
    baseline = _resolved(standards)
    strategy = standards.ladders(KEY)
    for version in ("us", "jp"):
        pin = next_attainable_cs(baseline[version]["ladder_cs"]["Gold"])
        standards.set_overall_threshold(KEY, "Gold", pin / 100, version)
        assert standards.overall_curve(KEY, version)["ladder_cs"]["Gold"] == pin
    pinned = _resolved(standards)
    assert standards.ladders(KEY) == strategy
    standards.set_threshold(KEY, "Standard", "Gold", 24.)
    standards.reset_entity(KEY)
    assert standards.ladders(KEY) == strategy
    assert _resolved(standards) == pinned
    _, fresh, _ = _open(tmp_path)
    assert _resolved(fresh) == pinned
    fresh.reset_overall(KEY, "us")
    assert fresh.overall_curve(KEY, "us") == baseline["us"]
    assert fresh.overall_curve(KEY, "jp") == pinned["jp"]
    fresh.reset_overall(KEY)
    assert _resolved(fresh) == baseline


def test_reset_after_community_refresh_restores_latest_fit_and_preserves_other_region(tmp_path):
    payload = _payload()
    store, standards, _ = _open(tmp_path, payload)
    pins = {}
    for version in ("us", "jp"):
        pins[version] = next_attainable_cs(standards.overall_curve(KEY, version)["ladder_cs"]["Gold"])
        standards.set_overall_threshold(KEY, "Gold", pins[version] / 100, version)
    other = _resolved(standards, OTHER)
    for entry in payload["targets"][0]["approaches"][0]["entries"]:
        entry["time_cs"] -= 200
    assert store.absorb(fit_payload(payload))["applied"]
    latest = store.calibrations.active.overall[KEY]["us"]
    assert latest["ladder_cs"]["Gold"] != pins["us"]
    for version in ("us", "jp"):
        assert standards.overall_curve(KEY, version)["ladder_cs"]["Gold"] == pins[version]
    strategy = standards.ladders(KEY)
    standards.reset_overall(KEY, "us")
    assert standards.overall_curve(KEY, "us") == latest
    assert standards.overall_curve(KEY, "jp")["ladder_cs"]["Gold"] == pins["jp"]
    assert standards.ladders(KEY) == strategy and _resolved(standards, OTHER) == other


@pytest.mark.parametrize("reason", ["opposite_region", "excluded_unannotated", "incompatible_clock"])
def test_excluded_or_incompatible_population_cannot_resurrect_a_strategy_fallback(tmp_path, reason):
    payload = _payload()
    row = payload["targets"][0]["approaches"][0]
    policy = None
    if reason == "opposite_region":
        row["entries"] = [entry for entry in row["entries"] if entry["version"] == "jp"]
    elif reason == "excluded_unannotated":
        for entry in row["entries"]:
            entry["version"] = None
        policy = RankingPolicy({"layers": {"overall": {"patches": [{"target_id": KEY,
            "parameters": {"unannotated_region": "exclude"}}]}}})
    else:
        row["name"] = "[N64 REAL TIME] Standard"
        row["matched_strategy"] = "Standard"
    _, standards, _ = _open(tmp_path, payload, policy=policy)
    assert standards.ladders(KEY, "us"), "The misleading strategy fallback must actually exist."
    assert curves.progress_for_time(standards.overall_curve(KEY, "us"), 1600) is None
    assert curves.progress_for_time(standards.overall_curve(OTHER, "us"), 2100) is not None
    if reason == "opposite_region":
        assert curves.progress_for_time(standards.overall_curve(KEY, "jp"), 1600) is not None


def test_jp_strategy_policy_patch_can_be_added_then_removed_without_touching_overall(tmp_path):
    policy = [RankingPolicy()]
    store, standards, _ = _open(tmp_path, _payload(), policy=lambda: policy[0])
    original = {version: standards.ladders(KEY, version) for version in ("us", "jp")}
    overall = _resolved(standards)
    observation_revision = store.calibrations.active.data_revision
    policy[0] = RankingPolicy({"layers": {"strategy": {"patches": [{"target_id": KEY,
        "version": "jp", "strategy": "Standard", "parameters": {"percentiles": {"Mario": 15.}}}]}}})
    assert store.recalibrate()["applied"]
    assert standards.ladders(KEY, "us") == original["us"]
    assert standards.ladders(KEY, "jp") != original["jp"]
    assert _resolved(standards) == overall
    assert store.calibrations.active.data_revision == observation_revision
    policy[0] = RankingPolicy()
    assert store.recalibrate()["applied"]
    assert standards.ladders(KEY, "jp") == original["jp"]
    assert _resolved(standards) == overall
    assert store.calibrations.active.data_revision == observation_revision


@pytest.mark.parametrize("boundary", ["fit", "assignment_write", "snapshot_write"])
def test_failed_assignment_restores_saved_bytes_and_complete_published_state(tmp_path, monkeypatch, boundary):
    store, standards, assignments = _open(tmp_path, _payload())
    target = store.payload["targets"][2]
    item = target["approaches"][0]
    key = row_key(target, item["name"], item["ids"])
    assignments.adopt(key, "segment:42")
    before = (assignments.path.read_bytes(), store.path.read_bytes(), assignments.rows(),
              _resolved(standards, "segment:42"), store.calibrations.active)

    def fail(*args, **kwargs):
        raise OSError(f"injected {boundary} failure")

    target_function = {"fit": "sm64_events.library.calibration.prepare",
                       "assignment_write": "sm64_events.library.adoptions.atomic_bytes",
                       "snapshot_write": "sm64_events.library.store.write_snapshot"}[boundary]
    monkeypatch.setattr(target_function, fail)
    with pytest.raises(OSError, match=f"injected {boundary}"):
        assignments.adopt(key, "segment:43")
    assert assignments.path.read_bytes() == before[0]
    assert store.path.read_bytes() == before[1]
    assert assignments.rows() == before[2]
    assert _resolved(standards, "segment:42") == before[3]
    assert store.calibrations.active is before[4]
    assert not standards.ladders("segment:43")


def test_old_read_holds_observations_rows_and_both_rank_layers_across_refresh(tmp_path):
    payload = _payload()
    definitions = [{"id": 42, "name": "Test movement", "seed_key": "test:movement"}]
    store, standards, assignments = _open(tmp_path, payload, definitions=definitions)
    old_payload, old_rows = deepcopy(store.payload), assignments.rows()
    old_strategy, old_overall = standards.ladders(KEY), _resolved(standards)
    old_revision = standards.calibration_revision
    for entry in payload["targets"][0]["approaches"][0]["entries"]:
        entry["time_cs"] -= 200
    definitions[0]["id"] = 43
    with standards.read_context():
        assert store.absorb(fit_payload(payload))["applied"]
        assert store.payload == old_payload and assignments.rows() == old_rows
        assert standards.ladders(KEY) == old_strategy
        assert _resolved(standards) == old_overall
        assert standards.calibration_revision == old_revision
    assert store.payload != old_payload
    assert assignments.rows() != old_rows
    assert "segment:43" in assignments.rows().values()
    assert standards.ladders(KEY) != old_strategy
    assert _resolved(standards) != old_overall
    assert standards.calibration_revision != old_revision


def test_definition_changes_reach_assignments_only_through_real_sync(tmp_path):
    definitions = []
    _, standards, assignments = _open(tmp_path, _payload(), definitions=definitions)
    before = assignments.rows()
    definitions.append({"id": 42, "name": "Test movement", "seed_key": "test:movement"})
    assert assignments.rows() == before
    assignments.load()
    assert "segment:42" in assignments.rows().values()
    assert standards.ladders("segment:42")
    definitions.clear()
    assignments.load()
    assert assignments.rows() == before
    assert not standards.ladders("segment:42")
