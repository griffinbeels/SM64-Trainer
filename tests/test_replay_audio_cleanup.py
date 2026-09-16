"""Retain partially started audio until stop proves its ownership is released.

Uses the real recorder and paired PCM handoff. Audio devices, video capture,
encoder, and machine-wide lease are injected inert boundaries.
"""
import threading

import numpy as np
import pytest

from test_replay_startup_order import Audio, HeldLock, PairedVideo, PCM, WIN, make


def fail_stop_until_allowed(rec, audio):
    allowed = threading.Event()
    attempts = []
    stop = audio.stop

    def checked_stop():
        attempts.append(audio)
        assert rec._audio_source is audio, "cleanup dropped the only audio owner"
        if not allowed.is_set():
            raise RuntimeError("fixture audio still running")
        stop()

    audio.stop = checked_stop
    return allowed, attempts


@pytest.mark.parametrize("failed_source", ["primary", "fallback"])
def test_failed_partial_cleanup_keeps_source_and_excludes_other_recorders(
    tmp_path, failed_source
):
    rec, video, audio, fallback, held, calls, fallback_calls, _ = make(
        tmp_path / "owner", fails=True,
        fallback="fail" if failed_source == "fallback" else "ok",
    )
    source = fallback if failed_source == "fallback" else audio
    allowed, attempts = fail_stop_until_allowed(rec, source)
    peer, peer_video, _, _, peer_held, _, _, _ = make(tmp_path / "peer")
    # This is the same ownership boundary as the real machine-wide lock:
    # another recorder's factory must refuse while the first still owns it.
    peer._recorder_lock_factory = lambda: peer_held if held.closed else None
    try:
        with pytest.raises(RuntimeError, match=f"{failed_source} audio cleanup failed") as error:
            rec._begin_capture(WIN)
        assert str(error.value.__cause__) == "fixture audio still running"
        assert rec._audio_source is source and len(attempts) == 2
        assert not source.stopped and not held.closed
        assert not rec._capture_closed and rec._capture_cleanup_failed
        assert not rec.status()["recording"] and video.starts == 0
        assert fallback_calls == ([WIN.pid] if failed_source == "fallback" else [])
        assert audio.stopped == (failed_source == "fallback")
        before = list(calls)
        # A surviving callback goes nowhere after the old sink is detached.
        source.on_pcm(PCM)
        assert calls == before
        rec._begin_capture(WIN)
        peer._begin_capture(WIN)
        assert rec._audio_source is source and peer_video.starts == 0
        assert peer._audio_source is None and not held.closed

        allowed.set()
        rec._teardown_capture()
        assert len(attempts) == 3 and source.stopped
        assert rec._audio_source is None and held.closed and rec._capture_closed
        # Actual closure proves this recorder can recover too. Every ownership
        # exclusion above must remain in force until that proof arrives.
        assert not rec._capture_cleanup_failed
        peer._begin_capture(WIN)
        assert peer_video.starts == 1 and peer.status()["recording"]
    finally:
        allowed.set()
        rec.stop(cleanup=False)
        peer.stop(cleanup=False)


def test_one_failed_stop_is_retried_by_owned_teardown_before_new_capture(tmp_path):
    rec, video, audio, _, held, calls, fallbacks, _ = make(
        tmp_path, fails=True, fallback="ok",
    )
    stop = audio.stop
    stops = []

    def stop_once_failed():
        stops.append(rec._audio_source)
        assert rec._audio_source is audio
        if len(stops) == 1:
            raise RuntimeError("temporary audio stop failure")
        stop()

    audio.stop = stop_once_failed
    try:
        with pytest.raises(RuntimeError, match="primary audio cleanup failed"):
            rec._begin_capture(WIN)
        assert stops == [audio, audio] and audio.stopped
        assert rec._audio_source is None and held.closed
        assert rec._capture_closed and not rec._capture_cleanup_failed
        assert video.starts == 0 and not fallbacks
        replacement = Audio(calls)
        replacement_video = PairedVideo(calls)
        replacement_held = HeldLock()
        rec._audio_factory = lambda pid: replacement
        rec._video_factory = lambda win: replacement_video
        rec._recorder_lock_factory = lambda: replacement_held
        rec._begin_capture(WIN)
        assert rec._audio_source is replacement and replacement_video.starts == 1
        assert not replacement_held.closed
    finally:
        rec.stop(cleanup=False)


@pytest.mark.parametrize("factory_fails", ["primary", "fallback"])
def test_constructor_failure_never_restops_a_previous_cleaned_source(tmp_path, factory_fails):
    rec, video, audio, fallback, held, calls, fallback_calls, _ = make(
        tmp_path, fails=True, fallback="ok",
    )

    def fail_factory(pid):
        assert pid == WIN.pid
        raise RuntimeError("fixture factory unavailable")

    if factory_fails == "primary":
        rec._audio_factory = fail_factory
    else:
        rec._fallback_audio_factory = fail_factory
    try:
        rec._begin_capture(WIN)
        assert video.starts == 1
        if factory_fails == "primary":
            assert not audio.stopped and rec._audio_source is fallback
            assert fallback_calls == [WIN.pid] and rec._audio_mode == "system"
        else:
            assert calls.count("audio-process-stop") == 1
            assert not fallback.stopped and rec._audio_source is None
            assert rec._audio_mode == "none"
    finally:
        rec.stop(cleanup=False)
    assert held.closed


def test_stop_racing_failed_audio_start_retains_failure_and_never_starts_fallback(tmp_path):
    rec, video, audio, _, held, _, fallbacks, _ = make(
        tmp_path, blocking=True, fails=True, fallback="ok",
    )
    allowed, attempts = fail_stop_until_allowed(rec, audio)
    errors = []

    def begin():
        try:
            rec._begin_capture(WIN)
        except RuntimeError as error:
            errors.append(error)

    starter = threading.Thread(target=begin, name="r33-owned-audio-start", daemon=True)
    stopper = threading.Thread(target=lambda: rec.stop(cleanup=False),
                               name="r33-owned-audio-stop", daemon=True)
    starter.start()
    try:
        assert audio.entered.wait(3)
        assert rec._audio_source is audio
        stopper.start()
        assert video.revoked.wait(3), "stop failed to revoke demand before joining audio startup"
        audio.release.set()
        starter.join(3)
        stopper.join(3)
        assert not starter.is_alive() and not stopper.is_alive()
        assert len(errors) == 1 and "primary audio cleanup failed" in str(errors[0])
        assert not fallbacks and video.starts == 0
        assert rec._audio_source is audio and len(attempts) >= 2
        assert not rec._capture_closed and not held.closed
    finally:
        audio.release.set()
        allowed.set()
        starter.join(3)
        if stopper.ident is not None:
            stopper.join(3)
        assert not starter.is_alive() and not stopper.is_alive()
        rec.stop(cleanup=False)
    assert rec._audio_source is None and held.closed


def guard_ledger_detach(rec, allowed):
    """Expose the ledger dependency without weakening the owner's close guard."""
    closed = []
    detach_ledger = rec.ledger.detach

    def close_ledger():
        assert allowed.is_set(), "ledger detached while a producer remained owned"
        closed.append(True)
        detach_ledger()

    rec.ledger.detach = close_ledger
    return closed


@pytest.mark.parametrize("kind,method", [
    ("video", "stop"), ("audio", "stop"), ("sink", "stop"),
    ("writer", "close"), ("ledger", "detach"),
])
def test_each_failed_capture_owner_is_retained_off_route_until_real_close(
    tmp_path, kind, method
):
    rec, video, audio, _, held, calls, _, _ = make(tmp_path / "owner")
    peer, peer_video, _, _, peer_held, _, _, _ = make(tmp_path / "peer")
    peer._recorder_lock_factory = lambda: peer_held if held.closed else None
    allowed = threading.Event()
    attempts = []
    rec._begin_capture(WIN)
    ledger_closed = guard_ledger_detach(rec, allowed)
    objects = {"video": video, "audio": audio, "sink": rec._video_sink,
               "ledger": rec.ledger}
    if kind == "writer":
        class Writer:
            def close(self):
                calls.append("writer-closed")

            def write_audio(self, data):
                pytest.fail("audio callback reached a retained writer")

            def write_video(self, pixels, index):
                pytest.fail("video callback reached a retained writer")

        rec._video_sink.stop()
        rec._video_sink = None
        objects["writer"] = rec._writer = Writer()
    owner = objects[kind]
    original = getattr(owner, method)

    def close_owner():
        attempts.append(owner)
        if not allowed.is_set():
            raise RuntimeError(f"fixture {kind} still owns resources")
        original()

    setattr(owner, method, close_owner)
    retained = {kind: owner, "ledger": rec.ledger}
    try:
        rec._teardown_capture()
        assert rec._capture_retained == retained and not ledger_closed
        assert len(attempts) == 1 and not rec._capture_closed and not held.closed
        assert rec._capture_cleanup_failed and not rec._recording
        assert rec._video_source is rec._video_sink is rec._writer is None
        assert rec._audio_source is (audio if kind == "audio" else None)
        before = list(calls)
        rec._on_pcm(PCM)
        rec._on_frame(np.zeros((2, 2, 4), np.uint8), 0)
        assert calls == before
        peer._begin_capture(WIN)
        assert peer_video.starts == 0 and not held.closed
        rec._teardown_capture()
        assert len(attempts) == 2 and rec._capture_retained == retained
        assert not ledger_closed
        allowed.set()
        rec._teardown_capture()
        assert len(attempts) == 3 and not rec._capture_retained
        assert rec._capture_closed and held.closed and ledger_closed == [True]
        peer._begin_capture(WIN)
        assert peer_video.starts == 1
    finally:
        allowed.set()
        rec.stop(cleanup=False)
        peer.stop(cleanup=False)


@pytest.mark.parametrize("boundary", ["mapping", "lease"])
def test_failed_ownership_release_never_allows_competing_recorder(tmp_path, boundary):
    rec, _, _, _, held, _, _, _ = make(tmp_path / "owner")
    peer, peer_video, _, _, peer_held, _, _, _ = make(tmp_path / "peer")
    peer._recorder_lock_factory = lambda: peer_held if held.closed else None
    allowed = [False]
    original_close = held.close

    def guarded():
        if not allowed[0]:
            raise RuntimeError("ownership release failed")
        if boundary == "lease":
            original_close()

    if boundary == "mapping":
        rec._release_capture = guarded
    else:
        held.close = guarded
    try:
        rec._begin_capture(WIN)
        rec._teardown_capture()
        assert rec._rec_lock is held and not held.closed and not rec._capture_closed
        assert not rec.can_collect()
        peer._begin_capture(WIN)
        assert peer_video.starts == 0
        allowed[0] = True
        rec._teardown_capture()
        assert held.closed and rec._rec_lock is None and rec._capture_closed
        peer._begin_capture(WIN)
        assert peer_video.starts == 1
    finally:
        allowed[0] = True
        rec.stop(cleanup=False)
        peer.stop(cleanup=False)
