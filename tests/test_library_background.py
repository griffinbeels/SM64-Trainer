"""Isolated library builds keep snapshot identity and own their processes."""
import asyncio
import gzip
import importlib.util
import json
import os
from pathlib import Path
import sys

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


@pytest.mark.parametrize("worker", [False, True])
def test_frozen_runtime_hook_only_initializes_com_for_the_app(tmp_path, monkeypatch, worker):
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
    monkeypatch.setattr(sys, "argv", ["trainer.exe", bg.WORKER_FLAG] if worker else ["trainer.exe"])
    runpy.run_path(str(hook))
    assert bool(created) is not worker
    assert client.gen_dir == (None if worker else str(tmp_path))
