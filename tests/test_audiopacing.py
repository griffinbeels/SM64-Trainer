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
