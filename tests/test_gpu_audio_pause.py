"""A true pause (frozen video frontier) must not fail the recording run.

Real GpuAudio, AudioPacer, GpuMedia and PcmBuffer with a fake clock and an
inert mux; no GPU, no callbacks. Before round 48 the pacer padded silence to
the wall clock while the mux could only take audio up to the committed video
end, so 1.03 synthetic seconds of freeze filled 256 PCM blocks and aborted the
run (the round-46 witness). Native lifecycle and retry are not covered here.
"""

from types import SimpleNamespace

import pytest

from sm64_events.replay.gpuaudio import GpuAudio, PcmHandoff
from sm64_events.replay.gpumedia import GpuMedia
from sm64_events.replay.gpusettings import GpuSettings
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.media import MediaRun
from sm64_events.replay.pixels import SampledPicture


class InertMux:
    rate = 48000

    def __init__(self):
        self.written_samples = 0
        self.aborted = False

    def write_pcm(self, data, first_us):
        self.written_samples += len(data) // 4

    def abort(self):
        self.aborted = True


def make(settings):
    run = MediaRun.starting_at(1789344000.0)
    mux = InertMux()
    media = GpuMedia(run, PictureLedger(), mux, source_namespace="pause-witness",
                     encoder_duration=3000, packet_limit=settings.packet_bytes,
                     pending_count=settings.slots, pending_bytes=settings.pending_bytes,
                     max_age=settings.max_age, pcm_bytes=settings.pcm_bytes,
                     pcm_blocks=settings.pcm_blocks, lead_ticks=settings.lead_ticks)
    handoff = PcmHandoff(max_bytes=settings.pcm_bytes, max_blocks=settings.pcm_blocks,
                         max_age=settings.max_age)
    now = [0.0]
    audio = GpuAudio(media, handoff, monotonic=lambda: now[0],
                     wall_time=lambda: run.origin_ts + now[0])
    return run, mux, media, handoff, now, audio


def test_a_frozen_frontier_with_no_callbacks_waits_instead_of_failing():
    settings = GpuSettings()
    _run, mux, media, _handoff, now, audio = make(settings)
    media.video_end = 0  # committed video stops advancing: a true pause
    peak_blocks = 0
    for step in range(2000):  # eight synthetic seconds
        now[0] = step * settings.poll_s
        audio.drain()
        media.check_age(now[0])
        peak_blocks = max(peak_blocks, len(media.pcm.blocks))
    assert not media.closed and not mux.aborted
    # Nothing past the frontier is synthesized: the pacer is bounded, not merely capped.
    assert peak_blocks <= 1 and audio.pacer.delivered == 0


def test_silence_resumes_to_the_new_frontier_after_a_pause_without_a_burst():
    settings = GpuSettings()
    run, mux, media, _handoff, now, audio = make(settings)
    media.video_end = 0
    for step in range(500):  # two seconds frozen
        now[0] = step * settings.poll_s
        audio.drain()
        media.check_age(now[0])
    assert audio.pacer.delivered == 0
    # The picture advances by 0.5 s (a heartbeat or a real picture): silence
    # fills exactly up to that committed end, stamped there, and the mux
    # receives all of it at once; nothing is stamped at the later wall clock.
    media.video_end = 45000
    now[0] += settings.poll_s
    audio.drain()
    media.drain()
    expected = int(0.5 * 48000)
    assert abs(audio.pacer.delivered - expected) <= 48000 * settings.poll_s + 1
    assert mux.written_samples == audio.pacer.delivered
    assert not media.closed and not media.pcm.blocks
    assert audio.placement.next_pts <= round((run.origin_ts + 0.5) * 1_000_000) + 1


def test_real_pcm_ahead_of_a_frozen_frontier_waits_and_stays_bounded():
    settings = GpuSettings()
    run, mux, media, handoff, now, audio = make(settings)
    media.video_end = 90000  # one second of committed video, then frozen
    # Real callbacks keep arriving during the pause (proctap delivers zeros).
    block = b"\x00" * (4800 * 4)  # 0.1 s
    for i in range(30):
        now[0] = 1.0 + i * 0.1
        assert handoff.submit(block, run.origin_ts + 1.1 + i * 0.1, now=now[0])
        audio.drain()
        media.check_age(now[0])
    # Nothing muxed past the frontier, nothing failed, everything retained.
    assert not media.closed and mux.written_samples == 0
    assert len(media.pcm.blocks) == 30 and media.pcm.bytes == 30 * len(block)
    # The frontier reaches 2 s: exactly the first ten blocks drain, in order.
    media.video_end = 180000
    media.drain()
    assert mux.written_samples == 10 * 4800 and len(media.pcm.blocks) == 20


def play_through_a_stall(stall_s, *, seconds=8.0, stall_at=3.0):
    """Steady 30 fps pictures and 10 ms loopback callbacks (arrival-stamped, as
    GpuCapture.submit_audio does), through a stop-the-world interpreter stall:
    no callbacks and no ticks, then the device's buffered PCM arrives at once.
    Returns (failure or None, pictures muxed, seconds of PCM muxed)."""
    settings = GpuSettings()
    run, mux, media, handoff, now, audio = make(settings)
    pictures = []
    mux.write_video = pictures.append
    t, next_audio, next_picture, occurrence, backlog = 0.0, 0.01, 0.0, 0, 0
    try:
        while t < seconds:
            now[0] = t
            if stall_at <= t < stall_at + stall_s:
                while next_audio <= t:
                    backlog, next_audio = backlog + 1, next_audio + 0.01
                t += settings.poll_s
                continue
            for _ in range(backlog):
                handoff.submit(bytes(480 * 4), run.origin_ts + t, now=t)
            backlog = 0
            while next_audio <= t:
                handoff.submit(bytes(480 * 4), run.origin_ts + t, now=t)
                next_audio += 0.01
            audio.drain()
            # Pictures reach the media worker two frames after their capture.
            while next_picture <= t - 0.066 and media.can_offer:
                occurrence += 1
                decision = media.offer(
                    SampledPicture(16, 16, bytes([occurrence % 256, 1, 2, 255]) * 4, 8),
                    SimpleNamespace(frame=occurrence, extras=lambda: {"exact": True}),
                    occurrence=occurrence, capture_ts=run.origin_ts + next_picture, now=t)
                if decision.kind == "selected":
                    request = decision.request
                    media.complete(request.ticket, b"packet", pts=request.pts,
                                   duration=request.encoder_duration, keyframe=occurrence == 1)
                media.frontier(run.origin_ts + next_picture)
                next_picture += 1 / 30
            media.drain()
            media.check_age(t)
            t += settings.poll_s
    except Exception as error:  # noqa: BLE001 - the witness reports the run's own failure
        return str(error), len(pictures), mux.written_samples / 48000
    return None, len(pictures), mux.written_samples / 48000


@pytest.mark.parametrize("stall_s", [0.05, 0.14, 0.3, 0.5])
def test_an_interpreter_stall_does_not_deadlock_video_against_audio(stall_s):
    """The live failure, 2026-09-19..23: every "pending age limit" came 1-3 s
    after the recorder's forced gen-2 collection (54-139 ms stop-the-world).
    Callbacks stamped after the stall leave a gap in the PCM timeline. Audio
    could only be written up to the committed video end, and video only up to
    the written audio end plus the 100 ms lead, so a gap of about the lead or
    more blocked both until the head picture aged out and the run failed."""
    failure, pictures, pcm_s = play_through_a_stall(stall_s)
    assert failure is None
    # Both streams carry on to the end: every picture after the stall is muxed
    # and audio reaches within the interleave lead of the last one.
    assert pictures >= int((8.0 - 0.2) * 30) - 2
    assert pcm_s >= 8.0 - 0.3 - stall_s
