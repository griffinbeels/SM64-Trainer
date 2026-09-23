"""Every cut start inside real footage builds a playable replay.

Live 2026-09-22 (attempt 2079): saving a PB failed with "MP4 selection lacks
initial track dependency". The fragment store checked that each track (video
AND audio) had a sample at or before the REQUESTED start, but the replay is
built from the picture held at that start, which can begin earlier. When an
audio packet began between that picture and the requested start, the audio
track had nothing covering the replay's first instant and the save failed.
This sweeps every picture boundary and every point between them.
"""
from fractions import Fraction

import pytest

from test_replay_fragment_archive import recording as recording  # noqa: F401 - fixture
from test_replay_fragment_archive import units_of
from test_replay_picture_identity import encoder as encoder  # noqa: F401 - fixture
from sm64_events.replay.fragmentstore import FragmentArchive
from sm64_events.replay.media import MediaRun
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.virtualmp4 import VirtualMp4


def test_every_start_builds_a_replay_with_every_track(tmp_path, recording):  # noqa: F811
    root = tmp_path / "archive"
    archive = FragmentArchive(root, SegmentRing(None, 10**8, scratch_root=root),
                              MediaRun("sweep", 1000), extent_bytes=4096)
    units = units_of(recording)
    for unit in units:
        archive.feed(unit.data)
    archive.finish()
    assert archive.error is None
    kinds = {track.kind for track in archive.tracks.values()}
    assert kinds == {"video", "audio"}, "the sweep needs both tracks to mean anything"
    end = 150000
    failures, built = [], 0
    for start in range(0, end - 30000, 750):  # every 1/120 s: on and between pictures
        with archive.selection(start, start + 30000) as (init, tracks, selected, visible):
            try:
                VirtualMp4(init, tracks, selected, visible[0].pts, start + 30000)
                built += 1
            except ValueError as error:
                failures.append((start, visible[0].pts, str(error)))
    assert built > 100
    assert failures == [], failures[:5]


def test_audio_starting_after_the_held_picture_takes_the_previous_fragment():
    """The live shape, built exactly: the keyframe fragment's first audio packet
    begins after the keyframe picture, and the cut starts inside that picture
    but after the audio packet. Every track must cover the HELD picture's
    start, which the previous fragment's crossing audio packet does."""
    from pathlib import Path

    from sm64_events.replay.fragmentindex import Sample, Track
    from sm64_events.replay.fragmentstore import FragmentArchive, Published, _Extent

    video, audio = 1, 2
    tracks = {video: Track(video, "video", 90000, 0, 0, 0, 0), audio: Track(audio, "audio", 48000, 0, 0, 0, 0)}

    def unit(samples):
        return Published(Path("x"), "g", 0, 1, 0, tuple(samples), 0, 0)

    first = unit([Sample(video, 0, 0, 1500, 0, 1, True), Sample(video, 1500, 1500, 1500, 0, 1, False),
                  Sample(audio, 0, 0, 1024, 0, 1, False), Sample(audio, 1024, 1024, 1024, 0, 1, False)])
    second = unit([Sample(video, 3000, 3000, 1500, 0, 1, True), Sample(video, 4500, 4500, 1500, 0, 1, False),
                   Sample(video, 6000, 6000, 1500, 0, 1, False),
                   Sample(audio, 2048, 2048, 1024, 0, 1, False), Sample(audio, 3072, 3072, 1024, 0, 1, False),
                   Sample(audio, 4096, 4096, 1024, 0, 1, False)])
    archive = FragmentArchive.__new__(FragmentArchive)
    archive._init, archive.tracks = b"init", tracks
    archive._extents = [_Extent(Path("x"), "g", 0, [first, second], [(0, 0), (3000, 1)])]
    selected, visible = archive._select(4000, 6000)
    held = visible[0].pts
    assert held == 3000
    firsts = {}
    for chosen in selected:
        for sample in chosen.samples:
            firsts.setdefault(sample.track_id, sample.dts)
    for identity, dts in firsts.items():
        assert dts * 90000 <= held * tracks[identity].timescale, (identity, dts)


def _encoded_pictures(count=90, gop=15):
    """Real H.264 pictures, 30 fps, a keyframe every `gop` (no reordering)."""
    import av
    import numpy as np

    from sm64_events.replay.packetmux import EncodedPicture

    codec = av.CodecContext.create("libx264", "w")
    codec.width, codec.height, codec.pix_fmt = 160, 120, "yuv420p"
    codec.time_base = Fraction(1, 30)
    codec.options = {"g": str(gop), "bf": "0", "preset": "ultrafast", "tune": "zerolatency"}
    packets = []
    for index in range(count):
        frame = av.VideoFrame.from_ndarray(
            np.full((120, 160, 3), index * 2 % 256, dtype=np.uint8), format="rgb24").reformat(format="yuv420p")
        frame.pts = index
        packets += codec.encode(frame)
    packets += codec.encode(None)
    return [EncodedPicture(index + 1, index * 3000, 3000, packet.is_keyframe, bytes(packet))
            for index, packet in enumerate(packets)]


@pytest.mark.parametrize("hole_s", [0.14, 0.5])
def test_a_cut_starting_inside_an_audio_hole_still_builds(tmp_path, hole_s):
    """A stall of the audio callback leaves a hole in the PCM (the forced gen-2
    collection, 2026-09-23). Every cut start around and inside that hole must
    still build a replay with both tracks, never "MP4 selection lacks initial
    track dependency"."""
    from test_packetmux import RATE, Output, pcm

    from sm64_events.replay.packetmux import NativeFormat, PacketFragmentMux

    run = MediaRun("audio-hole", 1000.0)
    out = Output(tmp_path, run)
    mux = PacketFragmentMux(out, NativeFormat("h264", 160, 120, 30), run, audio_rate=RATE,
                            audio_bitrate=160000, packet_limit=8 * 1024 * 1024, pcm_limit=384000)
    pictures = _encoded_pictures()
    end_tick = pictures[-1].pts + pictures[-1].duration
    hole = (round(1.5 * RATE), round((1.5 + hole_s) * RATE))  # inside the second GOP
    events = [(picture.pts / 90000, "video", picture) for picture in pictures]
    events += [(first / RATE, "audio", first) for first in range(0, round(end_tick / 90000 * RATE), 960)
               if not hole[0] <= first < hole[1]]
    for _, kind, item in sorted(events, key=lambda event: (event[0], event[1] == "video")):
        if kind == "video":
            mux.write_video(item)
        else:
            mux.write_pcm(pcm(item), round(run.origin_ts * 1e6 + item / RATE * 1e6))
    mux.close()
    out.finish()
    # Why every start can build: the encoded audio track covers the hole
    # contiguously, as silence at its true time, so there is no gap for a cut
    # to start inside. (Measured, not assumed: this also holds with the
    # muxer's aresample async compensation removed.)
    from test_packetmux import decode_audio

    first, audio = decode_audio(out.path)  # asserts the decoded frames are contiguous
    assert first <= 0 and audio.shape[1] >= hole[1]
    inside = audio[:, hole[0] - first + 2048:hole[1] - first - 2048]  # clear of codec ringing
    before = audio[:, hole[0] - first - 9600:hole[0] - first - 2048]
    assert abs(inside).max() < 0.002 < 0.1 < abs(before).max()  # decoded float PCM: silence, then tone
    archive = out.archive
    assert archive.error is None and {t.kind for t in archive.tracks.values()} == {"video", "audio"}
    failures, built = [], 0
    for start in range(0, end_tick - 30000, 750):  # every 1/120 s, the hole included
        with archive.selection(start, start + 30000) as (init, tracks, selected, visible):
            try:
                VirtualMp4(init, tracks, selected, visible[0].pts, start + 30000)
                built += 1
            except ValueError as error:
                failures.append((start, visible[0].pts, str(error)))
    assert built > 300
    assert failures == [], failures[:5]
