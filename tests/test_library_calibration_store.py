"""Disk failure and interleaved readers cannot expose half a recalibration."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace

import pytest

from sm64_events.library.build import SCHEMA_VERSION
from sm64_events.library.ladders import fit_payload
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


def _write_spy(monkeypatch):
    writes = []

    def spying(path, payload):
        writes.append(path)
        write_snapshot(path, payload)

    monkeypatch.setattr("sm64_events.library.store.write_snapshot", spying)
    return writes


def _refit(policy="one"):
    def prepare(payload):
        payload["targets"][0]["approaches"][0]["ladder"] = {"Mario": 11.0}
        return _prepare(payload, policy=policy)
    return prepare


@pytest.mark.parametrize("change", ["nothing", "refit", "replaced"])
def test_a_restart_rewrites_its_local_copy_only_when_the_start_changed_it(tmp_path, monkeypatch, change):
    """Every start activates its first calibration; over an unchanged local
    copy that re-encoded and recompressed identical bytes (~0.15 s). Only that
    first activation may skip: a later one writes exactly as before."""
    previous = LibraryStore(tmp_path / "library.json.gz")  # left its local copy
    previous.prepare_calibration = _prepare
    assert previous.absorb(fit_payload(_snapshot()))["applied"]
    writes = _write_spy(monkeypatch)
    store = LibraryStore(tmp_path / "library.json.gz")
    store.load()
    store.prepare_calibration = _refit() if change == "refit" else _prepare
    if change == "replaced":  # behind the store's back, after it read the file
        write_snapshot(store.path, _snapshot(1500, "2026-01-01T12:00:00"))
    before = store.path.read_bytes()
    assert store.recalibrate()["applied"]
    assert writes == ([] if change == "nothing" else [store.path])
    assert read_snapshot(store.path) == store.payload and store.status()["source"] == "local"
    if change == "nothing":
        assert store.path.read_bytes() == before
        store.prepare_calibration = lambda payload: _prepare(payload, policy="two")
        assert store.recalibrate()["applied"] and writes == [store.path]


@pytest.mark.parametrize("local", [None, "older", "unfitted", "refit_by_bound_load"])
def test_a_local_copy_unlike_the_activated_payload_is_still_written(tmp_path, monkeypatch, local):
    """A fresh data dir writes its first local copy; a stale copy, or one this
    load refitted, is replaced. Only an untouched, equal copy is kept."""
    bundled = tmp_path / "bundled.gz"
    write_snapshot(bundled, fit_payload(_snapshot()))
    store = LibraryStore(tmp_path / "local.gz", bundled)
    if local == "older":
        write_snapshot(store.path, fit_payload(_snapshot(1500, "2026-01-01T12:00:00")))
    elif local == "unfitted":
        write_snapshot(store.path, _snapshot())  # wins the same-date tie, then refits
    elif local == "refit_by_bound_load":
        write_snapshot(store.path, fit_payload(_snapshot()))
        store.prepare_calibration = _refit()
    writes = _write_spy(monkeypatch)
    store.load()
    assert store.status()["source"] == ("bundled" if local in (None, "older") else "local")
    store.prepare_calibration = lambda payload: _prepare(payload, policy="two")
    assert store.recalibrate()["applied"] and writes == [store.path]
    assert read_snapshot(store.path) == store.payload and store.status()["source"] == "local"


# ---- a saved calibration: reused across restarts only for identical inputs ----

def _counting_prepare(monkeypatch):
    """Stand in for the fit behind `Preparation.run`, counting each call."""
    from sm64_events.library import calibration
    calls = []

    def fit(payload, assignments, definitions, standards, policy):
        calls.append(policy.revision)
        return _prepare(payload, policy=policy.revision)

    monkeypatch.setattr(calibration, "prepare", fit)
    return calls


def _restart(tmp_path, *, clocks="igt", policy="one"):
    """One app start: load the local library, then let adoptions calibrate it."""
    from types import SimpleNamespace
    from sm64_events.library.adoptions import Adoptions
    from sm64_events.library.calibration import SavedCalibration
    store = LibraryStore(tmp_path / "library.json.gz")
    store.saved_calibration = SavedCalibration(tmp_path / "rank_calibration.bin")
    store.load()
    standards = SimpleNamespace(calibration_inputs=lambda: {"clocks": {"star:2:4": clocks}})
    adoptions = Adoptions(tmp_path / "adoptions.json", store, standards,
                          policy=SimpleNamespace(revision=policy))
    adoptions.load()
    return store, adoptions


def _first_start(tmp_path, monkeypatch):
    write_snapshot(tmp_path / "library.json.gz", fit_payload(_snapshot()))
    calls = _counting_prepare(monkeypatch)
    store, _adoptions = _restart(tmp_path)
    assert calls and (tmp_path / "rank_calibration.bin").exists()
    return calls, store.calibrations.active


def _rank_reads(client):
    """What the ranks the app serves look like: a spread of Library targets
    (Strategy ladders and Overall curves) and the standards they resolve to."""
    library = client.app.state.library
    targets = library.payload["targets"]
    reads = []
    for index in range(0, len(targets), max(1, len(targets) // 12)):
        target = client.get(f"/api/library/target/{index}").json()
        reads.append(target)
        if targets[index].get("entity_key"):
            reads.append(client.get("/api/ranks/standards", params={
                "entity": targets[index]["entity_key"], "version": "us"}).json())
    return library.calibrations.active.revision, reads


def test_a_restarted_app_grades_exactly_as_a_fresh_preparation_would(tmp_path, monkeypatch):
    """Through the real app on the bundled Library: the second start serves
    the saved calibration without fitting, and every rank it serves matches
    the first start's freshly prepared one."""
    from sm64_events.library import calibration
    from import_fixture import make_client
    genuine, fitted = calibration.prepare, []
    monkeypatch.setattr(calibration, "prepare", lambda *args: fitted.append(1) or genuine(*args))
    with make_client(tmp_path) as (client, _db, _service):
        fresh = _rank_reads(client)
    assert fitted and (tmp_path / "rank_calibration.bin").exists()
    prepared = len(fitted)
    with make_client(tmp_path) as (client, _db, _service):
        restarted = _rank_reads(client)
    assert len(fitted) == prepared, "the restart fitted its calibration again"
    assert restarted == fresh


def test_a_restart_with_the_same_inputs_publishes_the_saved_calibration(tmp_path, monkeypatch):
    calls, first = _first_start(tmp_path, monkeypatch)
    prepared = len(calls)
    store, adoptions = _restart(tmp_path)
    active = store.calibrations.active
    assert len(calls) == prepared, "a restart with identical inputs fitted again"
    assert (active.revision, active.overall, active.layers, active.rows, active.payload) == (
        first.revision, first.overall, first.layers, first.rows, first.payload)
    monkeypatch.setattr(store, "recalibrate", lambda: pytest.fail("the second sync recalibrated"))
    adoptions._sync()   # the service start's second sync: current, so not even a reuse


def test_a_refetch_of_the_same_sheet_reuses_the_saved_calibration(tmp_path, monkeypatch):
    """A startup refresh of an unedited Sheet differs only in its fetch time,
    which no preparation reads: it reuses the saved fit and publishes nothing,
    and a reuse carries the fetch time it was asked about."""
    from sm64_events.library.calibration import SavedCalibration
    calls, first = _first_start(tmp_path, monkeypatch)
    store, _adoptions = _restart(tmp_path)
    refetched = fit_payload(_snapshot())
    refetched["fetched_at"] = "a later fetch"
    assert not store.absorb(refetched)["applied"] and len(calls) == 1
    other = LibraryStore(tmp_path / "other.json.gz")
    other.saved_calibration = SavedCalibration(tmp_path / "rank_calibration.bin")
    other.plan_calibration = store.plan_calibration
    assert other._prepare(refetched).payload == refetched and len(calls) == 1


MOVED_INPUTS = {
    "payload": lambda tmp_path, monkeypatch: write_snapshot(
        tmp_path / "library.json.gz", fit_payload(_snapshot(1100))),
    "other inputs": lambda tmp_path, monkeypatch: {"clocks": "rta"},
    "policy": lambda tmp_path, monkeypatch: {"policy": "two"},
    "overall model": lambda tmp_path, monkeypatch: monkeypatch.setattr(
        "sm64_events.ranks.overall.OVERALL_MODEL_VERSION", 999),
    "code": lambda tmp_path, monkeypatch: monkeypatch.setattr(
        "sm64_events.library.calibration.code_revision", lambda: "edited source"),
    "interpreter": lambda tmp_path, monkeypatch: monkeypatch.setattr(
        "sm64_events.library.calibration.sys.version_info", (3, 99, 0)),
}


@pytest.mark.parametrize("moved", MOVED_INPUTS)
def test_a_saved_calibration_is_never_reused_once_an_input_moved(tmp_path, monkeypatch, moved):
    """The key names the payload by content, every other input `prepare`
    receives, the Overall model, and the code and interpreter that fit it;
    moving any one of them prepares afresh, and saves the fresh result."""
    calls, _first = _first_start(tmp_path, monkeypatch)
    prepared = len(calls)
    inputs = MOVED_INPUTS[moved](tmp_path, monkeypatch) or {}
    store, _adoptions = _restart(tmp_path, **inputs)
    assert len(calls) == prepared + 1, f"a moved {moved} reused the saved calibration"
    again, _adoptions = _restart(tmp_path, **inputs)
    assert len(calls) == prepared + 1
    assert again.calibrations.active.revision == store.calibrations.active.revision


DAMAGE = {
    "flipped body byte": lambda data: data[:-10] + bytes([data[-10] ^ 1]) + data[-9:],
    "truncated": lambda data: data[:len(data) // 2],
    "other format": lambda data: data.replace(b'"format": 1', b'"format": 9', 1),
    "not a saved calibration": lambda data: b"PK" + data,
    "empty": lambda data: b"",
}


@pytest.mark.parametrize("damage", DAMAGE)
def test_a_damaged_saved_calibration_is_prepared_afresh_and_replaced(tmp_path, monkeypatch, damage):
    calls, first = _first_start(tmp_path, monkeypatch)
    saved = tmp_path / "rank_calibration.bin"
    saved.write_bytes(DAMAGE[damage](saved.read_bytes()))
    store, _adoptions = _restart(tmp_path)
    assert len(calls) == 2 and store.calibrations.active.revision == first.revision
    _restart(tmp_path)
    assert len(calls) == 2, "the fresh preparation did not replace the damaged file"


BUILT = []


def _built(value):
    BUILT.append(value)
    return value


class _Named:
    """Unpickles by calling `_built`: loading it is observable."""

    def __reduce__(self):
        return _built, ("built",)


def test_a_saved_file_naming_another_callable_is_refused_uncalled(tmp_path):
    """Only a `Calibration` of plain data loads: a checksummed file naming
    any other callable is a miss, and that callable never runs."""
    import hashlib
    import json
    import pickle
    from sm64_events.library import calibration
    saved = calibration.SavedCalibration(tmp_path / "rank_calibration.bin")
    body = pickle.dumps(_Named())
    saved.path.write_bytes(calibration._MAGIC + json.dumps({
        "format": calibration.SAVED_FORMAT, "key": "k", "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest()}).encode() + b"\n" + body)
    BUILT.clear()
    assert saved.load("k") is None and BUILT == []
    good = _prepare(_snapshot())
    saved.save("k", good)
    assert saved.load("k") == good and saved.load("another key") is None


def test_the_code_revision_follows_every_source_it_names(tmp_path, monkeypatch):
    import sys
    from sm64_events.library import calibration

    def source(name):
        return tmp_path / name if name.endswith(".py") else tmp_path / name / "module.py"

    for name in calibration._CODE_SOURCES:
        source(name).parent.mkdir(parents=True, exist_ok=True)
        source(name).write_text("VALUE = 1\n")
    before = calibration.source_revision(tmp_path)
    (tmp_path / "library" / "__pycache__").mkdir()
    (tmp_path / "library" / "__pycache__" / "module.pyc").write_bytes(b"cache")
    (tmp_path / "unrelated.py").write_text("VALUE = 2\n")
    assert calibration.source_revision(tmp_path) == before
    for name in calibration._CODE_SOURCES:
        source(name).write_text("VALUE = 2\n")
        after = calibration.source_revision(tmp_path)
        assert after != before, f"an edit under {name} kept the code revision"
        before = after
    executable = tmp_path / "trainer.exe"
    executable.write_bytes(b"one build")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    calibration.code_revision.cache_clear()
    try:
        frozen = calibration.code_revision()
        executable.write_bytes(b"the next build")
        calibration.code_revision.cache_clear()
        assert calibration.code_revision() != frozen
    finally:
        calibration.code_revision.cache_clear()


def test_an_adoption_edit_moves_the_change_count(tmp_path, monkeypatch):
    """A background apply trusts an unmoved count to mean unmoved inputs, so
    every door that changes them -- an edit, a definition change (the
    service calls `_sync`) -- must move it, and the plan must carry it."""
    write_snapshot(tmp_path / "library.json.gz", fit_payload(_snapshot()))
    _counting_prepare(monkeypatch)
    store, adoptions = _restart(tmp_path)
    counts = [store.plan_generation()]
    adoptions.unadopt("some row")
    counts.append(store.plan_generation())
    adoptions._sync()
    counts.append(store.plan_generation())
    assert counts[0] < counts[1] < counts[2]
    assert store.plan_calibration(store.payload).generation == counts[2]


def test_a_preparation_identical_to_the_active_one_still_counts_as_current(tmp_path, monkeypatch):
    """A recalibration that comes out identical publishes nothing; the next
    sync must still see its inputs as current rather than fit a third time."""
    write_snapshot(tmp_path / "library.json.gz", fit_payload(_snapshot()))
    calls = _counting_prepare(monkeypatch)
    store, adoptions = _restart(tmp_path)
    store.saved_calibration = None          # count real preparations only
    assert not store.recalibrate()["applied"] and len(calls) == 2
    adoptions._sync()
    assert len(calls) == 2
