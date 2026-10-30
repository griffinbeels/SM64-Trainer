"""What a media file holds, read in-process with PyAV, never by spawning ffprobe.

The packaged app ships ffmpeg.exe but not ffprobe.exe (209 MB; bundling it was
rejected for download size). Every read that spawned ffprobe therefore failed
quietly in every installed copy while dev and CI passed with ffprobe on PATH:
no saved replay was ever shrunk ("replay kept as saved: ... (no ffprobe beside
ffmpeg)" in a tester's log, 2026-10-25), a cached clip's first pts read as 0.0,
and a malformed stored clock could not be recovered. PyAV is bundled, links the
same libav, and `tests/test_single_source.py` forbids spawning ffprobe again.

Each read keeps the meaning ffprobe gave it, compared against ffprobe itself
on real clips in `tests/test_media_probe.py`:

- a time is the value ffprobe printed: ticks * num/den, to six decimals;
- `codec_name` is libav's codec id ("av1"), never the decoder's ("libdav1d");
- the decoded clock decodes on one thread and skips a picture that fails to
  decode, as ffprobe did, so a damaged clip yields the same pictures;
- a file that cannot be opened reads as None.

One ffprobe refusal has no in-process twin. The native packet index refused any
clip on which ffprobe printed an error. PyAV discards libav's log; seeing it
needs a process-wide callback that takes the GIL inside libav, which PyAV
itself warns against in threaded programs such as the recorder. The two
errors that mattered are now read from the structure instead: a read error
refuses, and so does a sample the mov demuxer indexed but never delivered
(ffprobe's "partial file": a truncated MP4). The known narrowing: a damaged
picture payload that only the stream-info decode logged (the first seconds)
no longer refuses, so a corrupt picture inside our own encoder's output keeps
its slot at its packet time. Our encoder never writes one, and disk
corruption is out of scope; the decoded clock still drops it, as ffprobe did.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av

from sm64_events.core.profiling import measured
from sm64_events.replay.media import MEDIA_TIME_BASE
from sm64_events.replay.packetmux import NATIVE_CODECS

_UNREADABLE = (av.error.FFmpegError, OSError, ValueError)


@dataclass(frozen=True)
class VideoStream:
    """The first video stream's own facts, in its own time base."""
    codec_name: str | None
    has_b_frames: bool
    time_base: Fraction
    start: int | None         # None: the container states no start
    duration: int | None
    frames: int               # the container's count; 0 when it states none
    width: int
    height: int
    rate: Fraction | None     # r_frame_rate

    def seconds(self, ticks: int | None) -> float | None:
        return None if ticks is None else _seconds(ticks, self.time_base)

    @property
    def start_s(self) -> float | None:
        return self.seconds(self.start)

    @property
    def duration_s(self) -> float | None:
        # ffprobe printed a zero duration as N/A, like a missing one.
        return self.seconds(self.duration) if self.duration else None


@dataclass(frozen=True)
class Media:
    video: VideoStream | None
    has_audio: bool


@dataclass(frozen=True)
class Packet:
    """One demuxed video packet: ffprobe's pts, dts and K/D/C flag letters."""
    pts: int | None
    dts: int | None
    keyframe: bool
    discard: bool
    corrupt: bool


def _seconds(ticks: int, time_base: Fraction) -> float:
    # ffprobe: ts * av_q2d(time_base), printed with "%f". round() gives the
    # same correctly rounded six decimals.
    return round(ticks * (time_base.numerator / time_base.denominator), 6)


def _open(clip: Path, input_format: str | None):
    # ffprobe sets scan_all_pmts for every input; only MPEG-TS reads it.
    return av.open(str(clip), format=input_format,
                   container_options={"scan_all_pmts": "1"})


def _video(stream) -> VideoStream:
    # Read inside the `with`: a stream outlives its closed container only as
    # a crash (PyAV segfaults on it).
    context = stream.codec_context
    try:
        codec_name = context.codec.canonical_name
    except (AttributeError, ValueError):
        codec_name = None
    return VideoStream(codec_name=codec_name, has_b_frames=bool(context.has_b_frames),
                       time_base=stream.time_base, start=stream.start_time,
                       duration=stream.duration, frames=stream.frames or 0,
                       width=context.width, height=context.height, rate=stream.base_rate)


def probe(clip: Path, *, input_format: str | None = None) -> Media | None:
    """The first video stream and whether any audio rides with it; None when
    the file cannot be opened."""
    try:
        with _open(clip, input_format) as container:
            video = container.streams.video
            return Media(video=_video(video[0]) if video else None,
                         has_audio=bool(container.streams.audio))
    except _UNREADABLE:
        return None


def video_start_of(clip: Path) -> float:
    """The video stream's first pts, in seconds; 0.0 when it cannot be read
    (an unreadable file, no start) -- the pre-2026-09-01 assumption."""
    media = probe(clip)
    start = media.video.start_s if media and media.video else None
    return start if start is not None and start >= 0 else 0.0


@measured("replay.probe_frames")
def frame_times_of(clip: Path, *, input_format: str | None = None,
                   native_packets: bool = False,
                   native_preroll: bool = False) -> list[float] | None:
    """Every video frame's PTS; None when it cannot be read.

    Native encoder callers can use guarded packet timestamps. Other media
    retains decoded-frame semantics, including presentation reordering.
    """
    if native_packets:
        packets = native_packet_index(clip, input_format=input_format,
                                      allow_preroll=native_preroll)
        times = [_seconds(p.pts, MEDIA_TIME_BASE) for p in packets or () if not p.discard]
        if times:
            return times
    return _decoded_times(clip, input_format)


def _decoded_times(clip: Path, input_format: str | None) -> list[float] | None:
    try:
        with _open(clip, input_format) as container:
            if not container.streams.video:
                return None
            stream = container.streams.video[0]
            decoder = stream.codec_context
            decoder.thread_count = 1   # ffprobe's decoder: libavcodec's default
            times = []
            for packet in _until_unreadable(container.demux(stream)):
                try:
                    frames = decoder.decode(packet)
                except av.error.FFmpegError:
                    continue           # ffprobe drops a picture that fails to decode
                for frame in frames:
                    if frame.pts is None:
                        return None    # ffprobe printed N/A, which never parsed
                    times.append(_seconds(frame.pts, stream.time_base))
            return times or None
    except _UNREADABLE:
        return None


def _until_unreadable(packets):
    """ffprobe stops reading at a read error and still flushes its decoder.
    PyAV's own flush packet only follows a clean end, so send one here."""
    while True:
        try:
            packet = next(packets)
        except StopIteration:
            return
        except av.error.FFmpegError:
            yield None
            return
        yield packet


@measured("replay.probe_native_packets")
def native_packet_index(clip: Path, *, input_format: str | None = None,
                        allow_preroll: bool = False) -> list[Packet] | None:
    """Read timestamps without decoding pixels, only for our native encoder.

    Our mux writes one picture per packet with no B frames, in H264 or in AV1
    depending on what the recording GPU can encode. Arbitrary downloads do not
    have that contract and must keep the decoded-frame path. Refuse reordered,
    discarded, corrupt or incomplete packets rather than sorting them into an
    apparently plausible picture map.
    """
    read = _read_packets(clip, input_format)
    if read is None:
        return None
    video, packets, indexed = read
    return _admit(video, packets, indexed, allow_preroll=allow_preroll)


def _read_packets(clip: Path, input_format: str | None
                  ) -> tuple[VideoStream, list[Packet], int] | None:
    """The first video stream, every packet it demuxes to, and how many
    samples the demuxer indexed (0 when it keeps no index, like MPEG-TS)."""
    try:
        with _open(clip, input_format) as container:
            if not container.streams.video:
                return None
            stream = container.streams.video[0]
            video = _video(stream)
            rows = [(Packet(p.pts, p.dts, p.is_keyframe, p.is_discard, p.is_corrupt), p.size)
                    for p in container.demux(stream)]
            indexed = len(stream.index_entries)
    except _UNREADABLE:
        return None
    # A clean read ends with PyAV's empty flush packet; it is not a picture.
    if not rows or rows[-1][1]:
        return None
    return video, [packet for packet, _ in rows[:-1]], indexed


def _admit(video: VideoStream, packets: list[Packet], indexed: int, *,
           allow_preroll: bool = False) -> list[Packet] | None:
    """The native contract over what was read; None refuses all of it."""
    if (video.codec_name not in NATIVE_CODECS or video.has_b_frames
            or video.time_base != MEDIA_TIME_BASE):
        return None
    if indexed and indexed != len(packets):
        # The mov demuxer indexed a sample it never delivered: a truncated
        # file, which ffprobe reported as "partial file".
        return None
    ticks = []
    for packet in packets:
        pts = packet.pts
        if (type(pts) is not int or packet.dts != pts or packet.corrupt
                or (packet.discard and not (allow_preroll and pts < 0))
                or (allow_preroll and pts < 0 and not packet.discard)
                or (ticks and pts <= ticks[-1])):
            return None
        ticks.append(pts)
    return packets or None
