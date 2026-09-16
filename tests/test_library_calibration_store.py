"""Disk failure and interleaved readers cannot expose half a recalibration."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace

import pytest

from sm64_events.library.build import SCHEMA_VERSION
from sm64_events.library.store import LibraryStore, read_snapshot, write_snapshot
from sm64_events.ranks.calibration import build_calibration
from sm64_events.ranks.policy import RankingPolicy


def _snapshot(time=1200, revision="2026-09-08T12:00:00"):
    return {"schema_version": SCHEMA_VERSION, "sheet_revision": revision,
            "fetched_at": "first fetch", "runners": ["runner"], "targets": [
                {"entity_key": "star:2:4", "group": "WF", "section": "Whomp's Fortress",
                 "label": "Caged Island", "approaches": [{"name": "Standard", "ids": ["one"],
                     "entries": [{"runner": "runner", "time_cs": time, "version": "us"}]}],
                 "subsections": []}]}


def _prepare(payload, policy="one"):
    time = payload["targets"][0]["approaches"][0]["entries"][0]["time_cs"]
    return build_calibration(payload, {"source": {"target_id": "star:2:4"}},
                             {"star:2:4": {"strategies": {"Standard": {"Mario": time / 100}}}},
                             {}, {"star:2:4": "star:2:4"}, policy)


def _store(tmp_path):
    store = LibraryStore(tmp_path / "library.json.gz")
    store.prepare_calibration = _prepare
    assert store.absorb(_snapshot())["applied"]
    return store


def test_same_date_correction_activates_once_and_identical_fetch_does_not_churn(tmp_path):
    store = _store(tmp_path)
    old = store.calibrations.active
    fresh = _snapshot(1100)
    result = store.absorb(fresh)
    assert result["applied"] and store.calibrations.active is not old
    assert result["data_revision"] != old.data_revision
    assert result["calibration_revision"] == store.status()["calibration_revision"]
    assert read_snapshot(store.path) == store.payload
    before, active = store.path.read_bytes(), store.calibrations.active
    same = deepcopy(fresh)
    same["fetched_at"] = "later fetch"
    assert not store.absorb(same)["applied"]
    assert store.calibrations.active is active and store.path.read_bytes() == before
    fresh["targets"].clear()
    assert len(store.payload["targets"]) == 1


def test_source_only_embedder_accepts_same_date_changes_without_a_callback(tmp_path):
    store = LibraryStore(tmp_path / "library.gz")
    assert store.absorb(_snapshot())["applied"]
    before = store.path.read_bytes()
    assert not store.absorb(_snapshot())["applied"]
    assert store.path.read_bytes() == before
    assert store.absorb(_snapshot(1100))["applied"]
    assert store.status()["data_revision"]
    assert store.status()["calibration_revision"] is None


def test_older_source_is_refused_before_preparation(tmp_path):
    store = _store(tmp_path)
    previous, before = store.calibrations.active, store.path.read_bytes()
    store.prepare_calibration = lambda payload: pytest.fail("older data must not prepare")
    result = store.absorb(_snapshot(900, "2026-01-01T12:00:00"))
    assert not result["applied"]
    assert store.calibrations.active is previous and store.path.read_bytes() == before


@pytest.mark.parametrize("change", [
    lambda payload: payload.update(schema_version=-1),
    lambda payload: payload.update(sheet_revision="not a date"),
    lambda payload: payload.update(targets={}),
    lambda payload: payload.update(bad_number=float("nan")),
    lambda payload: payload["targets"][0]["approaches"][0].update(ladder={"Mario": float("inf")}),
])
def test_invalid_input_preserves_generation_and_disk(tmp_path, change):
    store = _store(tmp_path)
    previous, before = store.calibrations.active, store.path.read_bytes()
    fresh = _snapshot(1100)
    change(fresh)
    with pytest.raises(ValueError):
        store.absorb(fresh)
    assert store.calibrations.active is previous and store.path.read_bytes() == before


def test_failed_preparation_cannot_mutate_the_old_snapshot(tmp_path):
    store = _store(tmp_path)
    previous, before = store.calibrations.active, store.path.read_bytes()

    def fail(payload):
        payload["targets"].clear()
        raise ValueError("fit failed")

    store.prepare_calibration = fail
    with pytest.raises(ValueError, match="fit failed"):
        store.recalibrate()
    assert len(store.payload["targets"]) == 1
    assert store.calibrations.active is previous and store.path.read_bytes() == before


@pytest.mark.parametrize("prepare", [lambda payload: payload,
                                     lambda payload: replace(_prepare(payload), data_revision="wrong")])
def test_incomplete_or_mismatched_calibration_is_refused_before_disk(tmp_path, prepare):
    store = _store(tmp_path)
    previous, before = store.calibrations.active, store.path.read_bytes()
    store.prepare_calibration = prepare
    with pytest.raises((TypeError, ValueError)):
        store.absorb(_snapshot(1100))
    assert store.calibrations.active is previous and store.path.read_bytes() == before


def test_failed_atomic_replace_keeps_old_file_generation_and_no_temp(tmp_path, monkeypatch):
    store = _store(tmp_path)
    previous, before = store.calibrations.active, store.path.read_bytes()

    def fail_replace(source, destination):
        assert source.parent == destination.parent == tmp_path
        assert read_snapshot(store.path)["targets"][0]["approaches"][0]["entries"][0]["time_cs"] == 1200
        raise OSError("replacement refused")

    monkeypatch.setattr("sm64_events.library.store.os.replace", fail_replace)
    with pytest.raises(OSError, match="replacement refused"):
        store.absorb(_snapshot(1100))
    assert store.calibrations.active is previous and store.path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [store.path]


def test_standalone_atomic_writer_refuses_nan_without_touching_old_bytes(tmp_path):
    path = tmp_path / "library.gz"
    write_snapshot(path, _snapshot())
    before = path.read_bytes()
    with pytest.raises(ValueError):
        write_snapshot(path, _snapshot(float("nan")))
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]


def test_preparation_and_disk_write_finish_before_one_pointer_is_published(tmp_path, monkeypatch):
    store = _store(tmp_path)
    previous = store.calibrations.active
    seen = []

    def preparing(payload):
        assert store.calibrations.active is previous
        seen.append("prepare")
        return _prepare(payload)

    def writing(path, payload):
        assert store.calibrations.active is previous
        assert seen == ["prepare"]
        seen.append("write")
        write_snapshot(path, payload)

    store.prepare_calibration = preparing
    monkeypatch.setattr("sm64_events.library.store.write_snapshot", writing)
    assert store.absorb(_snapshot(1100))["applied"]
    assert seen == ["prepare", "write"] and store.calibrations.active is not previous


def test_pinned_reader_sees_old_payload_and_standards_while_refresh_activates(tmp_path):
    store = _store(tmp_path)
    old = store.calibrations.active
    with store.calibrations.pin():
        with ThreadPoolExecutor(max_workers=1) as worker:
            result = worker.submit(store.absorb, _snapshot(1100)).result()
        assert result["applied"] and store.calibrations.active is not old
        assert store.payload is old.payload
        assert store.calibrations.read.layers["star:2:4"]["strategies"]["Standard"]["Mario"] == 12
        assert store.status()["calibration_revision"] == old.revision
        with store.calibrations.pin(current=True):
            assert store.payload is store.calibrations.active.payload
    assert store.payload["targets"][0]["approaches"][0]["entries"][0]["time_cs"] == 1100


def test_policy_only_recalibration_uses_latest_unpinned_payload(tmp_path):
    store = _store(tmp_path)
    first = store.calibrations.active
    with store.calibrations.pin():
        assert store.absorb(_snapshot(1100))["applied"]
        current = store.calibrations.active
        store.prepare_calibration = lambda payload: _prepare(payload, policy="two")
        result = store.recalibrate()
        assert result["applied"] and result["calibration_revision"] != current.revision
        assert store.calibrations.active.data_revision == current.data_revision
        assert store.calibrations.active.payload["targets"][0]["approaches"][0]["entries"][0]["time_cs"] == 1100
        assert store.payload is first.payload
    active = store.calibrations.active
    before = store.path.read_bytes()
    assert not store.recalibrate()["applied"]
    assert store.calibrations.active is active and store.path.read_bytes() == before


def test_callback_bound_load_prepares_offline_without_rewriting_sources(tmp_path):
    local, bundled = tmp_path / "local.gz", tmp_path / "bundled.gz"
    write_snapshot(local, _snapshot(1100))
    write_snapshot(bundled, _snapshot(1200, "2026-01-01T12:00:00"))
    before = local.read_bytes(), bundled.read_bytes()
    store = LibraryStore(local, bundled)
    store.prepare_calibration = _prepare
    store.load()
    assert store.calibrations.active is not None
    assert store.payload is store.calibrations.active.payload
    assert store.status()["source"] == "local"
    assert (local.read_bytes(), bundled.read_bytes()) == before


def test_equal_date_load_preserves_local_correction_over_bundled_snapshot(tmp_path):
    local, bundled = tmp_path / "local.gz", tmp_path / "bundled.gz"
    write_snapshot(local, _snapshot(1100))
    write_snapshot(bundled, _snapshot(1200))
    store = LibraryStore(local, bundled)
    store.prepare_calibration = _prepare
    store.load()
    assert store.payload["targets"][0]["approaches"][0]["entries"][0]["time_cs"] == 1100
    assert store.status()["source"] == "local"


def test_reload_cannot_replace_a_known_newer_active_revision(tmp_path):
    store = _store(tmp_path)
    active = store.calibrations.active
    write_snapshot(store.path, _snapshot(1500, "2026-01-01T12:00:00"))
    store.load()
    assert store.calibrations.active is active
    assert store.payload["targets"][0]["approaches"][0]["entries"][0]["time_cs"] == 1200


@pytest.mark.parametrize("bound", [False, True])
def test_offline_fit_metadata_does_not_make_unchanged_source_look_new(tmp_path, bound):
    path = tmp_path / "library.gz"
    original = _snapshot()
    write_snapshot(path, original)
    store = LibraryStore(path)
    if bound:
        store.prepare_calibration = _prepare
    store.load()
    assert store.payload["targets"][0]["approaches"][0]["ladder_policy_revision"]
    before, active = path.read_bytes(), store.calibrations.active
    assert not store.absorb(original)["applied"]
    assert path.read_bytes() == before and store.calibrations.active is active


def test_policy_revision_refits_offline_even_when_model_version_matches(tmp_path):
    from sm64_events.library.ladders import LADDER_MODEL_VERSION

    path = tmp_path / "library.gz"
    payload = _snapshot()
    payload["ladder_model"] = {"version": LADDER_MODEL_VERSION, "policy_revision": "obsolete"}
    payload["targets"][0]["approaches"][0]["ladder"] = {"Mario": 999}
    write_snapshot(path, payload)
    before = path.read_bytes()
    store = LibraryStore(path)
    store.load()
    assert store.payload["ladder_model"]["policy_revision"] == RankingPolicy().revision
    assert store.payload["targets"][0]["approaches"][0]["ladder"]["Mario"] < 999
    assert path.read_bytes() == before
