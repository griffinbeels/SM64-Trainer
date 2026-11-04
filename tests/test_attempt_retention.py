"""Attempt-count expiry preserves active/save leases and original media bytes."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.replay.attemptretention import AttemptHistory
from sm64_events.replay.config import ReplayConfig, apply_settings_file, save_settings
from sm64_events.replay.fragmentstore import FragmentArchive
from sm64_events.replay.media import MediaRun
from sm64_events.replay.ring import SegmentRing
from sm64_events.server.replay_api import create_replay_router
from test_replay_api import FakeReplayService
from test_replay_fragment_producer import continuing_output, decoded_audio, decoded_video
from test_replay_ring import seg, T0
from test_replay_service import attempt, make_service


def row(number):
    return attempt(id=number, course_id=1 + number % 15,
                   started_utc=(T0 + timedelta(seconds=number * 10)).isoformat(),
                   ended_utc=(T0 + timedelta(seconds=number * 10 + 8)).isoformat())


def test_window_counts_completions_not_course_and_handles_replayed_order():
    history = AttemptHistory(10)
    rows = [row(i) for i in range(11)]
    history.observe(rows[3:], None)
    history.observe(rows[:3], None)  # delayed correction must not become newest
    assert not history.allows(0)
    assert all(history.allows(i) for i in range(1, 11))
    assert history.cutoff(3) == T0 + timedelta(seconds=7)
    history.observe([rows[0]], (T0 - timedelta(seconds=5)).isoformat())
    assert history.cutoff(3) == T0 - timedelta(seconds=8)
    history.observe([], None)
    assert history.cutoff(3) == T0 + timedelta(seconds=7)
    history.configure(None)
    assert history.cutoff(3) is None and history.allows(0)
    history.configure(2)
    assert history.cutoff(3) == T0 + timedelta(seconds=87)
    history.clear()
    assert history.cutoff(3) is None


def test_live_projection_commits_before_history_and_reprojection_is_idempotent(tmp_path):
    import asyncio
    from test_tracker_service import make, ev, star

    db, tracker = make(tmp_path)
    history = AttemptHistory(2)
    observed = []

    def committed(completed, active, **options):
        ids = {a.id for a in db.attempts()}
        assert all(a.id in ids for a in completed)
        history.observe(completed, active, **options)
        observed.extend(a.id for a in completed)

    tracker.on_replay_history = committed

    async def exercise():
        for index in range(3):
            await tracker.publish(ev("practice_reset", index * 1000, {"igt_frames_before": 0}))
            await tracker.publish(star(index * 1000 + 350))
        before = [history.allows(a.id) for a in db.attempts()]
        await tracker._reproject()
        assert [history.allows(a.id) for a in db.attempts()] == before
        return before

    try:
        assert asyncio.run(exercise()) == [False, True, True]
        assert len(set(observed)) == 3
    finally:
        db.close()


def test_trimmed_history_never_reopens_old_ids_but_accepts_late_long_completion():
    history = AttemptHistory(10)
    history.observe([row(i) for i in range(1001)], T0.isoformat())
    assert not history.allows(0) and not history.allows(990)
    late = replace(row(0), ended_utc=row(1002).ended_utc)
    history.observe([late], None)
    assert history.allows(0)  # known late completion overrides the ID watermark
    history.clear()
    assert history.allows(0)


def test_authoritative_reprojection_removes_deleted_completion():
    history = AttemptHistory(2)
    history.observe([row(i) for i in range(3)], None)
    assert not history.allows(0)
    history.observe([row(0), row(1)], None, replace=True)
    assert history.allows(0) and history.allows(1)
    assert history.cutoff(0) == T0


def test_expiry_releases_bytes_after_save_span_and_keeps_future_tail(tmp_path):
    ring = SegmentRing(None, 10**8, scratch_root=tmp_path)
    old = seg(tmp_path, 0)
    ring.add(old)
    fragment = tmp_path / "fragment.bin"
    fragment.write_bytes(b"original media")
    start, end = T0, T0 + timedelta(seconds=5)
    with ring.pin("video", start, end):
        ring.register_temp("fragments:run:0", [fragment], start, end)
        ring.expire_before(end)
        ring.forget_temp("fragments:run:0", delete=True)
        assert old.path.exists() and fragment.exists()
        assert fragment.resolve() in ring.protected_paths()
    assert not old.path.exists() and not fragment.exists()
    assert ring.total_bytes == 0


def test_failed_deletion_keeps_accounting_reports_real_error_and_retries(tmp_path, monkeypatch, caplog):
    from pathlib import Path

    ring = SegmentRing(None, 10**8, scratch_root=tmp_path)
    old = seg(tmp_path, 0)
    ring.add(old)
    original = Path.unlink
    blocked = True

    def unlink(path, **kwargs):
        if path == old.path and blocked:
            raise PermissionError(13, "sharing violation", str(path))
        return original(path, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    ring.expire_before(T0 + timedelta(seconds=10))
    ring.maintain()
    assert old.path.exists() and ring.total_bytes == old.size_bytes
    assert sum("replay deletion deferred:" in message for message in caplog.messages) == 1
    assert "errno=13" in caplog.text and "sharing violation" in caplog.text
    blocked = False
    ring.maintain()
    assert not old.path.exists() and ring.total_bytes == 0


def test_service_window_saved_and_pb_replays_remain_available(tmp_path):
    rows = [row(i) for i in range(12)]
    service = make_service(tmp_path, rows, cov=(T0 - timedelta(seconds=20), T0 + timedelta(seconds=150)))
    service.history.configure(10)
    service.save(0)  # manual save
    service.save(1)  # same preserve path used by verified PB action
    service.tracker.on_replay_history(rows, None)
    assert set(service.available_attempt_ids()) == set(range(12))
    assert service.view(0)["clip_url"] == "/api/replay/saved/0"
    assert service.view(1)["clip_url"] == "/api/replay/saved/1"
    unsaved = make_service(tmp_path / "unsaved", rows, cov=(T0, T0 + timedelta(seconds=150)))
    unsaved.history.configure(10)
    unsaved.tracker.on_replay_history(rows, None)
    assert set(unsaved.available_attempt_ids()) == set(range(2, 12))
    with pytest.raises(LookupError, match="expired"):
        unsaved.view(0)


@pytest.mark.parametrize("count", [1, 10, 1000, None])
def test_setting_survives_restart(tmp_path, count):
    cfg = ReplayConfig(scratch_dir=tmp_path, settings_path=tmp_path / "settings.json")
    save_settings(cfg.settings_path, None, 1024**3, 3, 2, count)
    restored = apply_settings_file(cfg)
    assert restored.retention_attempts == count


@pytest.mark.parametrize("count", [True, 1.5, "10"])
def test_api_rejects_coerced_attempt_count(tmp_path, count):
    app = FastAPI()
    app.include_router(create_replay_router(FakeReplayService(tmp_path)))
    with TestClient(app) as client:
        assert client.put("/api/replay/settings", json={
            "retention_attempts": count, "max_buffer_bytes": 1024**3}).status_code == 422


def test_closed_archive_expiry_and_retained_packet_identity(tmp_path):
    ffmpeg = bundled_ffmpeg() or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg required")
    continuing_output(tmp_path, ffmpeg, "libx264")  # CPU fixture only; encode once
    data = (tmp_path / "live.mp4").read_bytes()
    root = tmp_path / "scratch"
    ring = SegmentRing(None, 10**8, scratch_root=root)
    archives = []
    for number in range(12):
        archive = FragmentArchive(root / str(number), ring, MediaRun(str(number), 1000 + 10 * number))
        archive.feed(data)
        archive.finish()
        archives.append(archive)
    before = ring.total_bytes
    history = AttemptHistory(10)
    history.observe([attempt(id=i, started_utc=datetime.fromtimestamp(1000 + 10*i, timezone.utc).isoformat(),
                             ended_utc=datetime.fromtimestamp(1002 + 10*i, timezone.utc).isoformat())
                     for i in range(12)], None)
    for archive in archives:
        archive.expire_before(history.cutoff(0))
    assert ring.total_bytes < before
    assert not list((root / "0").glob("*.bin"))
    assert archives[0].coverage() is None and archives[1].coverage() is None
    with archives[2].read(0, 150000) as (_, chunks):
        retained = tmp_path / "retained.mp4"
        retained.write_bytes(b"".join(chunks))
    assert decoded_video(retained) == decoded_video(tmp_path / "live.mp4")[:len(decoded_video(retained))]
    actual_start, actual_audio = decoded_audio(retained)
    start, audio = decoded_audio(tmp_path / "live.mp4")
    assert actual_start == start
    assert (actual_audio == audio[:, :actual_audio.shape[1]]).all()


def span(number, start_s, seconds):
    return attempt(id=number, started_utc=(T0 + timedelta(seconds=start_s)).isoformat(),
                   ended_utc=(T0 + timedelta(seconds=start_s + seconds)).isoformat())


def kept(history, rows):
    return [row.id for row in rows if history.allows(row.id)]


def agrees(history, rows, pad=0):
    """cutoff and allows describe one window: footage for a kept row survives."""
    cutoff = history.cutoff(pad)
    for row in rows:
        if history.allows(row.id):
            assert datetime.fromisoformat(row.started_utc.replace("Z", "+00:00")) >= cutoff


def test_footage_limit_expires_old_attempts_before_the_count_limit():
    rows = [span(i, i * 300, 240) for i in range(4)]  # four minutes each
    history = AttemptHistory(5, 600)
    history.observe(rows, None)
    assert kept(history, rows) == [2, 3]  # a third would make twelve minutes
    assert history.cutoff(0) == T0 + timedelta(seconds=600)
    agrees(history, rows)


def test_count_limit_expires_old_attempts_before_the_footage_limit():
    rows = [span(i, i * 6, 5) for i in range(8)]  # rapid resets stay small
    history = AttemptHistory(5, 600)
    history.observe(rows, None)
    assert kept(history, rows) == [3, 4, 5, 6, 7]
    agrees(history, rows)


def test_exactly_the_footage_limit_is_still_kept():
    rows = [span(i, i * 310, 300) for i in range(3)]
    history = AttemptHistory(5, 600)
    history.observe(rows, None)
    assert kept(history, rows) == [1, 2]


def test_newest_attempt_stays_even_when_it_alone_is_over_the_limit():
    rows = [span(0, 0, 30), span(1, 40, 900)]
    history = AttemptHistory(5, 600)
    history.observe(rows, None)
    assert kept(history, rows) == [1]
    assert history.cutoff(0) == T0 + timedelta(seconds=40)


def test_attempt_in_progress_is_never_expired_by_the_footage_limit():
    rows = [span(i, 1000 + i * 300, 290) for i in range(3)]
    history = AttemptHistory(5, 600)
    active = T0 + timedelta(seconds=100)  # opened before everything that closed
    history.observe(rows, active.isoformat())
    assert history.cutoff(0) == active
    assert history.allows(99)  # no completion row yet


def test_a_star_and_segment_over_one_play_count_once():
    # Saved clips 35266 and 710000035266 are this shape: one grab closes both.
    star, segment = span(1, 1000, 360), span(2, 1000, 360)
    older = span(0, 500, 240)
    rows = [older, star, segment]
    history = AttemptHistory(5, 600)
    history.observe(rows, None)
    assert kept(history, rows) == [0, 1, 2]  # ten minutes of footage, not sixteen
    history.configure(1)
    assert kept(history, rows) == [1, 2]


def test_a_long_newest_play_keeps_both_of_its_attempts():
    rows = [span(1, 1000, 900), span(2, 1000, 900)]
    history = AttemptHistory(5, 600)
    history.observe(rows, None)
    assert kept(history, rows) == [1, 2]


def test_other_modes_ignore_the_footage_limit_and_it_survives_them():
    rows = [span(i, i * 900, 800) for i in range(3)]
    history = AttemptHistory(5, 600)
    history.observe(rows, None)
    assert kept(history, rows) == [2]
    history.configure(None)
    assert history.cutoff(0) is None and kept(history, rows) == [0, 1, 2]
    assert history.footage_s == 600
    history.configure(5, 1700)
    assert kept(history, rows) == [1, 2]


def test_stored_attempt_count_is_kept_and_gains_the_footage_default(tmp_path):
    import json
    cfg = ReplayConfig(scratch_dir=tmp_path, settings_path=tmp_path / "settings.json")
    # What a Stop or Start press stored before this limit existed.
    cfg.settings_path.write_text(json.dumps({
        "retention_s": None, "retention_attempts": 10, "max_buffer_bytes": 2 * 1024**3,
        "pre_pad_s": 0.5, "post_pad_s": 2.0, "recording_enabled": True}), encoding="utf-8")
    restored = apply_settings_file(cfg)
    assert restored.retention_attempts == 10
    assert restored.retention_footage_s == cfg.retention_footage_s


def test_footage_limit_survives_restart_and_bad_values_lose_to_defaults(tmp_path):
    import json
    cfg = ReplayConfig(scratch_dir=tmp_path, settings_path=tmp_path / "settings.json")
    save_settings(cfg.settings_path, None, 1024**3, 3, 2, 7, retention_footage_s=900)
    assert apply_settings_file(cfg).retention_footage_s == 900
    assert apply_settings_file(cfg).retention_attempts == 7
    for bad in (1, 10**9, "600", [600], True, float("nan")):
        raw = json.loads(cfg.settings_path.read_text())
        cfg.settings_path.write_text(json.dumps({**raw, "retention_footage_s": bad}))
        assert apply_settings_file(cfg) is cfg


def real_service(tmp_path):
    from test_replay_recording_control import make as make_recorder, service as owned_service
    rec, *_ = make_recorder(tmp_path)
    return owned_service(tmp_path, rec)


def test_service_applies_persists_and_keeps_footage_limit_through_the_recording_switch(tmp_path):
    rows = [span(i, i * 300, 240) for i in range(4)]
    service = real_service(tmp_path)
    service.history.configure(5, 600)
    service.history.observe(rows, None)
    limit = 2 * 1024**3
    assert kept(service.history, rows) == [2, 3]
    settings = service.update_settings(None, limit, retention_footage_s=900.0)
    assert settings["retention_footage_s"] == 900.0
    assert kept(service.history, rows) == [1, 2, 3]
    assert service.status()["retention_footage_s"] == 900.0
    with pytest.raises(ValueError, match="retention_footage_s"):
        service.update_settings(None, limit, retention_footage_s=1.0)
    assert service.settings()["retention_footage_s"] == 900.0
    service.set_recording_enabled(False)
    assert apply_settings_file(service.cfg).retention_footage_s == 900.0
    # Omitting it keeps it, and another mode leaves it for the way back.
    service.update_settings(None, limit, retention_attempts=None)
    assert service.settings()["retention_footage_s"] == 900.0
    assert apply_settings_file(service.cfg).retention_footage_s == 900.0


def test_api_carries_the_footage_limit_and_refuses_out_of_range(tmp_path):
    app = FastAPI()
    app.include_router(create_replay_router(real_service(tmp_path)))
    with TestClient(app) as client:
        body = {"retention_attempts": 5, "max_buffer_bytes": 1024**3}
        routes = [route.path for route in app.routes if route.path.endswith("/replay/settings")]
        path = routes[0]
        assert client.put(path, json={**body, "retention_footage_s": 840}).json()[
            "retention_footage_s"] == 840
        assert client.put(path, json=body).json()["retention_footage_s"] == 840
        assert client.put(path, json={**body, "retention_footage_s": 5}).status_code == 409
        assert client.get(path).json()["retention_footage_s"] == 840


def test_a_save_may_use_every_byte_the_recorder_may_record_into():
    # One reserve: a lower ring margin alone records attempts Save then refuses.
    import inspect
    from sm64_events.replay import publication
    reserve = inspect.signature(publication.publish).parameters["reserve_bytes"].default
    assert reserve == SegmentRing(None, 1).disk_reserve_bytes
