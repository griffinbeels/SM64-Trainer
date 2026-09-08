"""Replay-lifetime review preferences through the real service and API."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import queue
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from sm64_events.replay.reviewstate import empty_state, state_path
from sm64_events.server.replay_api import create_replay_router
from test_replay_service import attempt, make_service

KEY = "7:" + "a" * 64
STATE = {"template_offsets": {KEY: -17}, "zoom": {"start": 8, "end": 29.5},
         "loop": {"start": .125, "end": 1.500011, "enabled": True}}


def client_for(service):
    app = FastAPI()
    app.include_router(create_replay_router(service))
    return TestClient(app)


def test_api_rejects_expired_edits_and_retains_newest_edit_in_the_current_session(tmp_path):
    service = make_service(tmp_path, [attempt()])
    client = client_for(service)
    path = "/api/attempts/42/replay/review-state"
    token = client.get(path).headers["X-Replay-Review-Session"]
    writer = "a" * 32
    def header(sequence):
        return {"X-Replay-Review-Edit": f"{token}/{writer}/{sequence}"}
    assert client.put(path, json=STATE, headers=header(2)).json() == STATE
    assert client.put(path, json=empty_state(), headers=header(1)).json() == STATE
    restarted = client_for(make_service(tmp_path, [attempt()]))
    refused = restarted.put(path, json=STATE, headers=header(3))
    assert refused.status_code == 409
    assert refused.json()["detail"] == "review session changed; reopen the replay"
    assert restarted.get(path).json() == empty_state()


def test_review_before_extraction_survives_clients_but_not_service_restart(tmp_path):
    service = make_service(tmp_path, [attempt()])
    path = "/api/attempts/42/replay/review-state"
    assert client_for(service).get(path).json() == empty_state()
    assert client_for(service).put(path, json=STATE).json() == STATE
    assert client_for(service).get(path).json() == STATE
    assert service.extractor.calls == []
    assert not service.cfg.scratch_dir.exists()
    assert make_service(tmp_path, [attempt()]).review_state(42) == empty_state()


def test_save_promotes_and_later_edits_persist_without_touching_media(tmp_path):
    service = make_service(tmp_path, [attempt()])
    service.update_review_state(42, STATE)
    saved = Path(service.save(42)["path"])
    original = (saved.read_bytes(), saved.with_suffix(".json").read_bytes())
    assert make_service(tmp_path, [attempt()]).review_state(42) == STATE
    updated = {**STATE, "template_offsets": {KEY: 31}}
    service.update_review_state(42, updated)
    assert service.save(42)["path"] == str(saved)
    assert len(service.extractor.calls) == 1
    assert (saved.read_bytes(), saved.with_suffix(".json").read_bytes()) == original
    assert make_service(tmp_path, [attempt()]).review_state(42) == updated
    assert json.loads(state_path(saved).read_text())["version"] == 1


def test_saved_state_is_found_after_file_reorganization(tmp_path):
    service = make_service(tmp_path, [attempt()])
    service.update_review_state(42, STATE)
    saved = Path(service.save(42)["path"])
    destination = service.cfg.save_root / "favorites" / saved.name
    destination.parent.mkdir()
    saved.rename(destination)
    state_path(saved).rename(state_path(destination))
    assert make_service(tmp_path, [attempt()]).review_state(42) == STATE


def test_clear_is_durable_and_failed_write_keeps_the_previous_saved_state(tmp_path, monkeypatch):
    from sm64_events.replay import reviewstate

    service = make_service(tmp_path, [attempt()])
    service.update_review_state(42, STATE)
    saved = Path(service.save(42)["path"])
    previous = state_path(saved).read_bytes()

    def cannot_publish(*args):
        raise OSError("disk temporarily unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(reviewstate.os, "replace", cannot_publish)
        with pytest.raises(OSError, match="disk temporarily unavailable"):
            service.update_review_state(42, {})
    assert state_path(saved).read_bytes() == previous
    assert service.review_state(42) == STATE
    assert not list(saved.parent.glob(".review-*.tmp"))
    assert service.update_review_state(42, {}) == empty_state()
    assert make_service(tmp_path, [attempt()]).review_state(42) == empty_state()


def test_shutdown_discards_only_temporary_review_state_even_if_recorder_stop_fails(tmp_path):
    service = make_service(tmp_path, [attempt(), attempt(id=43)])
    service.update_review_state(42, STATE)
    saved = Path(service.save(42)["path"])
    service.update_review_state(43, STATE)

    def stopped():
        raise RuntimeError("inert recorder teardown failed")

    service.recorder.stop = stopped
    with pytest.raises(RuntimeError):
        service.lifecycle_stop()
    assert service.review_state(43) == empty_state()
    assert service.review_state(42) == STATE
    assert saved.exists() and len(service.tracker.db.attempts()) == 2


@pytest.mark.parametrize("body", [
    {"extra": 1}, {"template_offsets": []}, {"template_offsets": {"../bad": 1}},
    {"template_offsets": {KEY: True}}, {"template_offsets": {KEY: .5}},
    {"template_offsets": {KEY: 1_000_001}},
    {"template_offsets": {f"{n}:" + "a" * 64: 0 for n in range(1, 130)}},
    {"zoom": {"start": 5, "end": 5}}, {"zoom": {"start": -1, "end": 5}},
    {"zoom": {"start": False, "end": 5}},
    {"zoom": {"start": 0, "end": 10**500}},
    {"loop": {"start": 0, "end": 1}},
    {"loop": {"start": 0, "end": 90_000, "enabled": True}},
    {"loop": {"start": 0, "end": 1, "enabled": 1}},
    {"loop": {"start": 0, "end": float("nan"), "enabled": True}},
    {"loop": {"start": 0, "end": float("inf"), "enabled": True}},
])
def test_invalid_body_is_refused_without_replacing_previous_preferences(tmp_path, body):
    service = make_service(tmp_path, [attempt()])
    service.update_review_state(42, STATE)
    response = client_for(service).put("/api/attempts/42/replay/review-state",
                                      content=json.dumps(body),
                                      headers={"content-type": "application/json"})
    assert response.status_code == 409
    assert service.review_state(42) == STATE


def test_endpoint_preserves_missing_attempt_and_database_errors(tmp_path):
    service = make_service(tmp_path, [])
    client = client_for(service)
    path = "/api/attempts/42/replay/review-state"
    assert client.get(path).status_code == 404
    assert client.put(path, json=STATE).status_code == 404
    service.tracker.db = None
    assert client.get(path).status_code == 503
    assert client.put(path, json=STATE).status_code == 503


@pytest.mark.parametrize("raw", [b"torn", b"{}", b"x" * 40_000,
                                b'{"version":2,"state":{}}'],
                         ids=["torn", "empty", "oversized", "unknown-version"])
def test_corrupt_saved_preferences_degrade_to_default_without_rewriting(tmp_path, raw):
    service = make_service(tmp_path, [attempt()])
    saved = Path(service.save(42)["path"])
    state_path(saved).write_bytes(raw)
    assert service.review_state(42) == empty_state()
    assert state_path(saved).read_bytes() == raw
    assert service.view(42)["saved_path"] == str(saved)


def test_edit_racing_save_is_durable_after_publication(tmp_path, monkeypatch):
    from sm64_events.replay import service as module

    service = make_service(tmp_path, [attempt()])
    service.update_review_state(42, STATE)
    copy_started, finish_copy, edit_started = (threading.Event() for _ in range(3))
    copy = module.shutil.copy2

    def paused_copy(source, destination):
        copy_started.set()
        assert finish_copy.wait(5)
        return copy(source, destination)

    updated = {**STATE, "template_offsets": {KEY: 42}}

    def edit():
        edit_started.set()
        return service.update_review_state(42, updated)

    monkeypatch.setattr(module.shutil, "copy2", paused_copy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        save = pool.submit(service.save, 42)
        assert copy_started.wait(5)
        put = pool.submit(edit)
        assert edit_started.wait(5)
        finish_copy.set()
        assert Path(save.result(timeout=5)["path"]).exists()
        assert put.result(timeout=5) == updated
    assert make_service(tmp_path, [attempt()]).review_state(42) == updated


def test_read_racing_save_never_loses_state_between_destination_lookup_and_read(tmp_path, monkeypatch):
    service = make_service(tmp_path, [attempt()])
    service.update_review_state(42, STATE)
    get_started, finish_get = threading.Event(), threading.Event()
    writer_progress = queue.Queue()
    original_get = service._review_state.get

    class ObservedLock:
        """Keep real mutex behavior; report when the writer reaches contention."""
        def __init__(self):
            self.lock = threading.Lock()

        def __enter__(self):
            if not self.lock.acquire(blocking=False):
                writer_progress.put("waiting for reader")
                self.lock.acquire()

        def __exit__(self, *_):
            self.lock.release()

    lock = ObservedLock()
    monkeypatch.setattr(service, "_cut_lock", lambda _: lock)

    def paused_get(attempt_id, saved):
        # The service already resolved saved=None. Pause exactly before it
        # reads temporary state: promotion must not slip between these steps.
        assert saved is None
        get_started.set()
        assert finish_get.wait(5)
        return original_get(attempt_id, saved)

    def save():
        result = service.save(42)
        writer_progress.put("published")
        return result

    monkeypatch.setattr(service._review_state, "get", paused_get)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(service.review_state, 42)
        assert get_started.wait(5)
        saving = pool.submit(save)
        # Wait for actual progress, not a sleep-based guess about scheduling.
        # Without the GET lock, Save finishes here and erases temporary state.
        writer_progress.get(timeout=5)
        finish_get.set()
        assert reading.result(timeout=5) == STATE
        assert Path(saving.result(timeout=5)["path"]).exists()
    assert make_service(tmp_path, [attempt()]).review_state(42) == STATE
