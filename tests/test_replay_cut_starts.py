"""Every cut start inside real footage builds a playable replay.

Live 2026-09-22 (attempt 2079): saving a PB failed with "MP4 selection lacks
initial track dependency". The fragment store checked that each track (video
AND audio) had a sample at or before the REQUESTED start, but the replay is
built from the picture held at that start, which can begin earlier. When an
audio packet began between that picture and the requested start, the audio
track had nothing covering the replay's first instant and the save failed.
This sweeps every picture boundary and every point between them.
"""
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
