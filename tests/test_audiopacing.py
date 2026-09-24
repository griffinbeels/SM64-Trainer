"""Arrival clocks survive shared pacing; a stalled worker cannot allocate silence unboundedly."""

import pytest

from sm64_events.replay.audiopacing import AudioBacklog, AudioPacer, AudioPlacement
from sm64_events.replay.ffmpeg_sink import AudioPacer as LegacyPacer


def test_pcm_arrival_time_places_gaps_and_only_clamps_overlaps():
    writes = []
    place = AudioPlacement(48000, lambda data, pts: writes.append((data, pts)))
    first, other = b"\x01\x02\x03\x04" * 48000, b"\x05\x06\x07\x08" * 480
    place.put_at(first, 1001)
    place.put_at(other, 1001.005)  # overlaps the already delivered first packet
    place.put_at(other, 1001.040)  # genuine arrival-clock gap stays a gap
    assert [pts for _, pts in writes] == [1000000000, 1001000000, 1001030000]
    assert writes[0][0] is first and writes[1][0] is other
    assert place.next_pts == 1001040000


@pytest.mark.parametrize("stall_s", [0.0, 0.14, 0.5])
def test_a_callback_stall_does_not_leave_audio_late(stall_s):
    """10 ms callbacks stamped at arrival; the callback thread stalls (the
    forced gen-2 collection, 54-139 ms live), then the device's buffered
    chunks arrive together. Before 2026-09-23 audio stayed stamped late by the
    stall for the rest of the run (144 ms after 140 ms, 504 ms after 500 ms)."""
    writes = []
    place = AudioPlacement(48000, lambda data, pts: writes.append((pts, len(data) // 4)))
    t, due, backlog = 1000.0, 1000.01, 0
    while t < 1010.0:
        if 1005.0 <= t < 1005.0 + stall_s:
            while due <= t:
                backlog, due = backlog + 1, due + 0.01
        else:
            for _ in range(backlog):
                place.put_at(b"\0" * 4 * 480, t)
            backlog = 0
            while due <= t:
                place.put_at(b"\0" * 4 * 480, t)
                due += 0.01
        t += 0.004
    device_end_us = (due - 0.01) * 1_000_000
    assert abs(place.next_pts - device_end_us) <= 10_001  # within one callback (µs rounding)
    assert place.dropped_samples <= stall_s * 48000 + 480  # only the stall's own audio
    # Never overlapped or reordered: each chunk starts at or after the last end.
    for (pts, samples), (following, _) in zip(writes, writes[1:], strict=False):
        assert following >= pts + round(samples * 1_000_000 / 48000)


def test_ordinary_callback_jitter_is_never_trimmed():
    import random

    jitter = random.Random(7)
    writes = []
    place = AudioPlacement(48000, lambda data, pts: writes.append(len(data) // 4))
    for n in range(1, 3001):  # 30 s of 10 ms chunks, each delivered 0-20 ms late
        place.put_at(b"\0" * 4 * 480, 1000 + n * 0.01 + jitter.uniform(0, 0.02))
    assert place.dropped_samples == 0 and sum(writes) == 3000 * 480


def test_failed_delivery_never_advances_pcm_clock():
    writes = []

    def write(data, pts):
        if not writes:
            writes.append("failure")
            raise OSError("fixture writer failed")
        writes.append(pts)

    place = AudioPlacement(44100, write)
    with pytest.raises(OSError):
        place.put_at(b"\0" * 4 * 441, 2000.01)
    assert place.next_pts is None
    place.put_at(b"\0" * 4 * 441, 2000.01)
    assert writes == ["failure", 2000000000]
    assert place.next_pts == 2000010000


def test_stalled_silence_padding_is_refused_before_allocating_or_advancing():
    now, writes = [0.0], []
    pacer = AudioPacer(48000, lambda: now[0], writes.append, max_pad_samples=48000)
    assert pacer.tick() == 0
    now[0] = 10000  # would otherwise allocate 1.92 GB of silence
    with pytest.raises(AudioBacklog, match="budget"):
        pacer.tick()
    assert writes == [] and pacer.delivered == 0


def test_padding_at_budget_retains_original_bytes_and_legacy_owner():
    assert LegacyPacer is AudioPacer
    now, writes = [0.0], []
    pacer = AudioPacer(48000, lambda: now[0], writes.append, max_pad_samples=480)
    pacer.tick()
    now[0] = 0.01
    assert pacer.tick() == 480
    assert writes == [b"\0" * (480 * 4)]


@pytest.mark.parametrize("stalls", [(0.12, 0.08), (0.3, 0.06, 0.2)])
def test_silence_after_a_pull_back_closes_the_stamps_exactly(stalls):
    """The loaded-machine shape (2026-09-24): the producer stalls past the
    idle grace, the pacer pads silence to now, then the stalled chunk arrives
    covering that time and the pull-back drops its covered head. The pacer
    counted the dropped samples as delivered, so its next silence fell short
    and left a hole in the stamps; ffmpeg's aresample closes holes under
    100 ms by laying the next audio early. Every write must start where the
    last ended."""
    now, writes = [1000.0], []
    placement = AudioPlacement(48000, lambda data, pts: writes.append((pts, len(data) // 4)))
    pacer = AudioPacer(48000, lambda: now[0], write=lambda buf: placement.pad_at(buf, now[0]),
                       write_at=placement.put_at, idle_grace_s=0.05,
                       missing=lambda bound: placement.missing(bound))
    produced = now[0]

    def advance(seconds, *, producing):
        nonlocal produced
        end = now[0] + seconds
        while now[0] < end - 1e-9:
            now[0] = round(now[0] + 0.01, 6)
            if producing:
                samples = round((now[0] - produced) * 48000)
                pacer.feed(b"\1\0\1\0" * samples, ends_at=now[0])
                produced = now[0]
            pacer.tick()

    advance(0.5, producing=True)
    for stall in stalls:
        advance(stall, producing=False)   # the pacer pads after its grace
        advance(0.3, producing=True)      # the late chunk covers the stall
    assert placement.dropped_samples > 0, "the pull-back must have dropped something"
    ends = [pts + round(n * 1_000_000 / 48000) for pts, n in writes]
    holes = [start - end for (start, _), end in zip(writes[1:], ends, strict=False) if start - end > 21]
    assert holes == [], f"{len(holes)} holes, up to {max(holes) / 1000:.1f} ms"
    assert abs(placement.next_pts / 1e6 - now[0]) < 0.001  # and the stamps reach now


def test_real_audio_never_waits_behind_silence_the_pacer_invented():
    """The producer stalls 80 ms: after the 50 ms grace the pacer pads 30 ms of
    silence to now, then the stalled chunk arrives covering all 80 ms. Only
    30 ms late, under the pull-back's tolerance, it used to be placed after the
    silence and leave everything after it 30 ms late (up to +75 ms under
    load, 2026-09-24). The overlap is our own silence, so it goes at once and
    the chunk ends at its arrival."""
    writes = []
    placement = AudioPlacement(48000, lambda data, pts: writes.append((pts, len(data) // 4)))
    placement.put_at(b"\1\0\1\0" * 480, 1000.010)             # real audio to 1000.010
    placement.pad_at(b"\0" * 4 * 1440, 1000.060)               # the pacer's silence, 30 ms to 1000.060
    placement.put_at(b"\1\0\1\0" * 3840, 1000.090)             # the stalled 80 ms, delivered at 1000.090
    assert placement.next_pts == 1_000_090_000                 # it ends at its arrival, not 30 ms later
    assert writes[-1] == (1_000_060_000, 1440) and placement.dropped_samples == 2400
    placement.put_at(b"\1\0\1\0" * 480, 1000.099)              # 1 ms late after real audio: left alone
    assert placement.next_pts == 1_000_100_000
