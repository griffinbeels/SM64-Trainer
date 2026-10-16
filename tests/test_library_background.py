"""Isolated library builds keep snapshot identity and own their processes."""
import asyncio
import gzip
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading

import pytest

from test_library_refresh import _fresh_workbook
from sm64_events.library import background as bg
from sm64_events.library.store import LibraryStore, build_and_stamp, read_snapshot


def test_real_worker_preserves_build_output_and_is_the_owned_process(tmp_path, monkeypatch):
    # The autouse test fixture hides the bundled seed in this interpreter.
    # Both builds must read the same seed to compare their vetted matches.
    from sm64_events.core import paths
    seed = Path(paths.__file__).resolve().parents[1] / "data" / "rank_standards.seed.json"
    monkeypatch.setattr(paths, "bundled_rank_standards", lambda: seed)
    data = _fresh_workbook()
    direct = build_and_stamp(data)
    store = LibraryStore(tmp_path / "library.json.gz")
    observed = {}
    original_spawn, original_wait = bg._spawn, bg._wait

    def spawn(directory):
        observed["directory"] = directory
        observed["child"] = original_spawn(directory)
        return observed["child"]

    async def wait(child, timeout_s):
        await original_wait(child, timeout_s)
        observed["receipt"] = json.loads((observed["directory"] / "result.json").read_text())

    monkeypatch.setattr(bg, "_spawn", spawn)
    monkeypatch.setattr(bg, "_wait", wait)
    result = asyncio.run(bg.refresh_at_startup(store, lambda: data))
    assert result["applied"]
    assert observed["receipt"]["worker_pid"] == observed["child"].pid != os.getpid()
    assert observed["child"].poll() == 0
    assert not observed["directory"].exists()
    persisted = read_snapshot(store.path)
    for payload in (direct, persisted, store.payload):
        payload.pop("fetched_at", None)
    assert persisted == direct == store.payload


def test_an_older_sheet_does_not_start_a_worker_or_build(tmp_path, monkeypatch):
    """Only an OLDER Sheet skips the build. A same-date Sheet still builds in
    the worker, because main's absorb applies corrected same-date data by
    content (library/store.py::absorb); it then applies nothing when unchanged."""
    data = _fresh_workbook()
    store = LibraryStore(tmp_path / "not-written.json.gz")
    store._payload = build_and_stamp(data)
    store._payload["sheet_revision"] = "2999-01-01T00:00:00"   # we already hold a newer Sheet

    def unexpected(*_args, **_kwargs):
        pytest.fail("unchanged workbook reached expensive work")

    monkeypatch.setattr(bg, "_spawn", unexpected)
    monkeypatch.setattr("sm64_events.library.store.build_and_stamp", unexpected)
    assert not store.refresh(lambda: data)["applied"]
    assert not asyncio.run(bg.refresh_at_startup(store, lambda: data))["applied"]
    assert not store.path.exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancellation_reap_actual_worker_before_cleanup(tmp_path, monkeypatch, cancel):
    data = _fresh_workbook()
    store = LibraryStore(tmp_path / "library.json.gz")
    observed = {}
    original_spawn = bg._spawn
    monkeypatch.setattr(bg, "_command", lambda _directory: [
        getattr(sys, "_base_executable", sys.executable), "-c",
        "import time; time.sleep(30)"])

    def spawn(directory):
        observed["directory"] = directory
        observed["child"] = original_spawn(directory)
        return observed["child"]

    monkeypatch.setattr(bg, "_spawn", spawn)

    async def run():
        task = asyncio.create_task(bg.refresh_at_startup(
            store, lambda: data, timeout_s=30 if cancel else 0.05))
        if cancel:
            for _ in range(200):
                if "child" in observed:
                    break
                await asyncio.sleep(0.005)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await task

    asyncio.run(run())
    assert observed["child"].poll() is not None
    assert not observed["directory"].exists()
    assert not store.path.exists()


def test_frozen_worker_dispatch_bypasses_desktop_startup(monkeypatch, tmp_path):
    entry = Path(__file__).resolve().parents[1] / "gui_entry.py"
    spec = importlib.util.spec_from_file_location("library_gui_dispatch", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    received = []
    monkeypatch.setattr(sys, "argv", ["trainer.exe", bg.WORKER_FLAG, str(tmp_path), "123"])
    monkeypatch.setattr(bg, "worker_main", lambda args: received.append(args) or 7)
    monkeypatch.setitem(sys.modules, "sm64_events.desktop.app", None)
    assert module.main() == 7
    assert received == [[str(tmp_path), "123"]]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert bg._command(tmp_path)[:2] == [sys.executable, bg.WORKER_FLAG]


def test_worker_snapshot_is_bounded_and_revision_checked(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.json.gz"
    path.write_bytes(gzip.compress(b" " * 100))
    monkeypatch.setattr(bg, "MAX_SNAPSHOT_BYTES", 50)
    with pytest.raises(ValueError, match="size limit"):
        bg._read_prepared(path, {"sheet_revision": "x"})
    monkeypatch.setattr(bg, "MAX_SNAPSHOT_BYTES", 10000)
    path.write_bytes(gzip.compress(json.dumps({"schema_version": bg.SCHEMA_VERSION,
        "sheet_revision": "old", "targets": []}).encode()))
    with pytest.raises(ValueError, match="identity"):
        bg._read_prepared(path, {"sheet_revision": "new"})


@pytest.mark.parametrize("flag", [None, bg.WORKER_FLAG, "--beta-self-check"])
def test_frozen_runtime_hook_only_initializes_com_for_the_app(tmp_path, monkeypatch, flag):
    import runpy
    import tempfile
    from types import SimpleNamespace
    hook = Path(__file__).resolve().parents[1] / "tools" / "rthook_comtypes.py"
    created = []

    def make_temp(**kwargs):
        created.append(kwargs)
        return str(tmp_path)

    client = SimpleNamespace(gen_dir=None)
    monkeypatch.setitem(sys.modules, "comtypes", SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules, "comtypes.client", client)
    monkeypatch.delenv("COMTYPES_GEN_DIR", raising=False)
    monkeypatch.setattr(tempfile, "mkdtemp", make_temp)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["trainer.exe", flag] if flag else ["trainer.exe"])
    runpy.run_path(str(hook))
    assert bool(created) is (flag is None)
    assert client.gen_dir == (str(tmp_path) if flag is None else None)


# ---- the parent's apply: the fit off the loop, staging and publishing on it ----

class _FinishedWorker:
    returncode = 0

    def poll(self):
        return 0

    def wait(self):
        return 0

    def kill(self):
        pass


def _worker_building(monkeypatch, payload):
    """A worker that has already written this payload as its snapshot."""
    from sm64_events.library.store import write_snapshot
    monkeypatch.setattr(bg, "log_revision", lambda _data: payload["sheet_revision"])

    def spawn(directory):
        write_snapshot(directory / "snapshot.json.gz", payload)
        (directory / "result.json").write_text(json.dumps({
            "version": 1, "sheet_revision": payload["sheet_revision"],
            "cpu_s": 0.0, "wall_s": 0.0}))
        return _FinishedWorker()

    monkeypatch.setattr(bg, "_spawn", spawn)


def _sheet(time=1200):
    from sm64_events.library.ladders import fit_payload
    return fit_payload({"schema_version": bg.SCHEMA_VERSION, "sheet_revision": "2026-09-08T12:00:00",
                        "runners": ["runner"], "targets": [
                            {"entity_key": "star:2:4", "group": "WF", "section": "Whomp's Fortress",
                             "label": "Caged Island", "subsections": [], "approaches": [
                                 {"name": "Standard", "ids": ["one"], "entries": [
                                     {"runner": "runner", "time_cs": time, "version": "us"}]}]}]})


def _planned_store(tmp_path, monkeypatch, fit, generation=None):
    """A store whose preparation is split the way adoptions split it, with
    adoptions' change count when `generation` ({"value": n}) is given."""
    from types import SimpleNamespace
    from sm64_events.library import calibration
    store = LibraryStore(tmp_path / "library.json.gz")
    inputs = {"key": "one", "plan_threads": []}

    def plan(payload):
        inputs["plan_threads"].append(threading.get_ident())
        return calibration.Preparation(inputs["key"], {}, [], None,
                                       SimpleNamespace(revision=inputs["key"]),
                                       generation["value"] if generation else 0)

    monkeypatch.setattr(calibration, "prepare", fit)
    store.plan_calibration = plan
    if generation is not None:
        store.plan_generation = lambda: generation["value"]
    return store, inputs


def _calibrated(payload, policy):
    from sm64_events.ranks.calibration import build_calibration
    return build_calibration(payload, {}, {}, {}, {}, policy.revision)


def test_the_startup_apply_fits_off_the_loop_and_publishes_on_it(tmp_path, monkeypatch):
    """The loop also runs the 250 Hz poller. Provisioning (the plan) and the
    publication stay on it; the fit -- up to ~2 s -- must not."""
    fit_threads, commit_threads = [], []

    def fit(payload, _assignments, _definitions, _standards, policy):
        fit_threads.append(threading.get_ident())
        return _calibrated(payload, policy)

    store, inputs = _planned_store(tmp_path, monkeypatch, fit)
    commit = store.commit_absorb
    monkeypatch.setattr(store, "commit_absorb", lambda *args: (
        commit_threads.append(threading.get_ident()), commit(*args))[1])
    _worker_building(monkeypatch, _sheet())

    async def run():
        return threading.get_ident(), await bg.refresh_at_startup(store, lambda: b"workbook")

    loop_thread, result = asyncio.run(run())
    assert result["applied"] and store.calibrations.active.policy_revision == "one"
    assert fit_threads and loop_thread not in fit_threads
    # Planned when staged, and again when the commit rechecks its inputs.
    assert set(inputs["plan_threads"]) == set(commit_threads) == {loop_thread}


@pytest.mark.parametrize("counted", [False, True])
def test_an_input_that_moves_during_the_fit_is_staged_again(tmp_path, monkeypatch, counted):
    """Nothing holds the update lock while the fit runs, so an assignment
    edit can land meanwhile; the commit must not publish a calibration of
    the inputs it replaced -- whether it learns so from the gatherer's
    change count (adoptions) or, without one, by gathering again."""
    fitted, generation = [], {"value": 0}

    def fit(payload, _assignments, _definitions, _standards, policy):
        fitted.append(policy.revision)
        if len(fitted) == 1:
            inputs["key"] = "two"        # e.g. a row linked while this fit ran
            generation["value"] += 1
        return _calibrated(payload, policy)

    store, inputs = _planned_store(tmp_path, monkeypatch, fit, generation if counted else None)
    _worker_building(monkeypatch, _sheet())
    result = asyncio.run(bg.refresh_at_startup(store, lambda: b"workbook"))
    assert result["applied"] and fitted == ["one", "two"]
    assert store.calibrations.active.policy_revision == "two"
    assert store.prepared_from == (store.calibrations.active.revision, "two")


def test_an_unmoved_change_count_commits_without_gathering_again(tmp_path, monkeypatch):
    """Gathering provisions and costs ~30 ms of the loop; with the change
    count unmoved, the commit confirms the staged inputs without it."""
    store, inputs = _planned_store(tmp_path, monkeypatch,
                                   lambda payload, *args: _calibrated(payload, args[-1]),
                                   {"value": 0})
    _worker_building(monkeypatch, _sheet())
    assert asyncio.run(bg.refresh_at_startup(store, lambda: b"workbook"))["applied"]
    assert len(inputs["plan_threads"]) == 1


@pytest.mark.parametrize("refits", [False, True])
def test_the_installed_local_copy_is_the_payload_the_apply_activated(tmp_path, monkeypatch, refits):
    """The worker's file is installed as is when the fit left the payload as
    built; a fit that refits ladders gets a file of its own result, encoded
    off the loop."""
    def fit(payload, _assignments, _definitions, _standards, policy):
        if refits:
            payload["targets"][0]["approaches"][0]["ladder"] = {"Mario": 11.0}
        return _calibrated(payload, policy)

    store, _inputs = _planned_store(tmp_path, monkeypatch, fit)
    built = _sheet()
    _worker_building(monkeypatch, built)
    assert asyncio.run(bg.refresh_at_startup(store, lambda: b"workbook"))["applied"]
    assert read_snapshot(store.path) == store.payload
    assert (store.payload == built) is not refits


def test_the_apply_logs_its_fit_and_loop_time_apart(tmp_path, monkeypatch, caplog):
    store, _inputs = _planned_store(tmp_path, monkeypatch, lambda payload, *args: _calibrated(payload, args[-1]))
    _worker_building(monkeypatch, _sheet())
    with caplog.at_level("INFO", logger="sm64.library"):
        asyncio.run(bg.refresh_at_startup(store, lambda: b"workbook"))
    line = next(r.getMessage() for r in caplog.records if "library startup worker" in r.getMessage())
    assert "compute_ms=" in line and "loop_apply_ms=" in line and "applied=True" in line
