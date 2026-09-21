"""Index the restricted, clear, non-reordered FFmpeg fragment topology.

One metadata pass per published unit; no decoding, subprocess or payload copy.
Offsets address the original stream. Unsupported edits/layouts fail explicitly.
This is an experimental producer index, not validation of arbitrary uploads.
"""
from dataclasses import dataclass
import struct

from sm64_events.replay.fragments import FragmentUnit


def boxes(data):
    """Yield (type, payload view, relative box start, header size)."""
    data = memoryview(data)
    at = 0
    while at < len(data):
        if len(data) - at < 8:
            raise ValueError("short MP4 box header")
        size, kind = struct.unpack_from(">I4s", data, at)
        header = 16 if size == 1 else 8
        if size == 1:
            if len(data) - at < header:
                raise ValueError("short extended MP4 header")
            size = struct.unpack_from(">Q", data, at + 8)[0]
        if size < header or size > len(data) - at:
            raise ValueError("MP4 box outside published unit")
        yield kind, data[at + header:at + size], at, header
        at += size


def child(data, wanted):
    found = [payload for kind, payload, _, _ in boxes(data) if kind == wanted]
    if len(found) != 1:
        raise ValueError(f"expected one {wanted!r} box")
    return found[0]


def word(data, offset):
    try:
        return struct.unpack_from(">I", data, offset)[0]
    except struct.error as error:
        raise ValueError("short MP4 metadata") from error


def edit_origin(trak):
    edits = [payload for kind, payload, _, _ in boxes(trak) if kind == b"edts"]
    if not edits:
        return 0
    edit = child(edits[0], b"elst")
    if word(edit, 4) != 1 or edit[0] not in (0, 1):
        raise ValueError("unsupported MP4 edit list")
    fmt = ">Qqhh" if edit[0] == 1 else ">Iihh"
    try:
        _, origin, rate, fraction = struct.unpack_from(fmt, edit, 8)
    except struct.error as error:
        raise ValueError("short MP4 edit") from error
    if origin < 0 or (rate, fraction) != (1, 0):
        raise ValueError("unsupported MP4 edit origin/rate")
    return origin


@dataclass(frozen=True, slots=True)
class Track:
    id: int
    kind: str
    timescale: int
    origin: int
    duration: int
    size: int
    flags: int


@dataclass(frozen=True, slots=True)
class Sample:
    track_id: int
    dts: int
    pts: int
    duration: int
    offset: int
    size: int
    key: bool


def tracks_of(unit):
    moov = child(unit.data, b"moov")
    defaults = {}
    for kind, payload, _, _ in boxes(child(moov, b"mvex")):
        if kind == b"trex":
            if word(payload, 8) != 1:
                raise ValueError("unsupported sample description")
            defaults[word(payload, 4)] = tuple(word(payload, at) for at in (12, 16, 20))
    tracks = {}
    for kind, trak, _, _ in boxes(moov):
        if kind != b"trak":
            continue
        tkhd, mdia = child(trak, b"tkhd"), child(trak, b"mdia")
        identity = word(tkhd, 20 if tkhd[0] == 1 else 12)
        mdhd = child(mdia, b"mdhd")
        scale = word(mdhd, 20 if mdhd[0] == 1 else 12)
        handler = bytes(child(mdia, b"hdlr")[8:12])
        if handler not in (b"vide", b"soun") or scale <= 0 or identity in tracks or identity not in defaults:
            raise ValueError("unsupported fragment track")
        stsd = child(child(child(mdia, b"minf"), b"stbl"), b"stsd")
        descriptions = list(boxes(stsd[8:]))
        if word(stsd, 4) != 1 or len(descriptions) != 1:
            raise ValueError("unsupported sample descriptions")
        # The recorder writes AV1 on a GPU that has an AV1 encoder and H.264
        # everywhere else, so a video track may legitimately be either. Both
        # are one-picture-per-sample and reordering-free, which is the property
        # the sample tables below depend on; nothing else is admitted.
        expected = (b"avc1", b"av01") if handler == b"vide" else (b"mp4a",)
        if descriptions[0][0] not in expected:
            raise ValueError("unsupported fragment codec")
        tracks[identity] = Track(identity, "video" if handler == b"vide" else "audio",
                                 scale, edit_origin(trak), *defaults[identity])
    if sorted(track.kind for track in tracks.values()) not in (["video"], ["audio", "video"]):
        raise ValueError("expected one video and optional audio track")
    return tracks


def fragment_samples(unit: FragmentUnit, tracks, *, max_samples=131072):
    """Read integer packet clocks and validate every declared sample byte range."""
    top = list(boxes(unit.data))
    if [row[0] for row in top] != [b"moof", b"mdat"]:
        raise ValueError("expected moof/mdat")
    _, moof, moof_at, _ = top[0]
    _, mdat, mdat_at, mdat_header = top[1]
    low, high = mdat_at + mdat_header, mdat_at + mdat_header + len(mdat)
    result = []
    for kind, traf, _, _ in boxes(moof):
        if kind == b"traf":
            result.extend(_track_samples(traf, tracks, moof_at, low, high, unit.offset, max_samples - len(result)))
    ranges = sorted((sample.offset, sample.offset + sample.size) for sample in result)
    if not ranges or any(left[1] > right[0] for left, right in zip(ranges, ranges[1:], strict=False)):
        raise ValueError("empty or overlapping fragment samples")
    return tuple(result)


def _track_samples(traf, tracks, moof_at, low, high, stream_offset, remaining):
    tfhd, tfdt, trun = (child(traf, name) for name in (b"tfhd", b"tfdt", b"trun"))
    if any(kind not in (b"tfhd", b"tfdt", b"trun") for kind, *_ in boxes(traf)):
        raise ValueError("unsupported track fragment metadata")
    flags = word(tfhd, 0)
    if flags & ~0x020038 or not flags & 0x020000:
        raise ValueError("fragment must use default-base-is-moof")
    track = tracks.get(word(tfhd, 4))
    if track is None:
        raise ValueError("unknown fragment track")
    defaults, at = [], 8
    for bit, fallback in ((8, track.duration), (16, track.size), (32, track.flags)):
        defaults.append(word(tfhd, at) if flags & bit else fallback)
        at += 4 if flags & bit else 0
    if len(tfhd) != at or tfdt[0] not in (0, 1):
        raise ValueError("unsupported track header")
    try:
        dts = struct.unpack_from(">Q" if tfdt[0] else ">I", tfdt, 4)[0] - track.origin
    except struct.error as error:
        raise ValueError("short decode timestamp") from error
    return _run_samples(trun, track, dts, defaults, moof_at, low, high, stream_offset, remaining)


def _run_samples(trun, track, dts, defaults, moof_at, low, high, stream_offset, remaining):
    flags, count = word(trun, 0), word(trun, 4)
    # Reject the declared count before allocating any per-sample objects.
    if count > remaining:
        raise ValueError("fragment sample limit exceeded")
    if flags & ~0x01000F05 or not flags & 1 or count > (high - low):
        raise ValueError("unsupported sample run")
    try:
        offset = moof_at + struct.unpack_from(">i", trun, 8)[0]
    except struct.error as error:
        raise ValueError("short sample offset") from error
    at = 12
    first_flags = word(trun, at) if flags & 4 else defaults[2]
    at += 4 if flags & 4 else 0
    result = []
    for number in range(count):
        values = []
        for bit, fallback in ((0x100, defaults[0]), (0x200, defaults[1]),
                              (0x400, first_flags if number == 0 else defaults[2]), (0x800, 0)):
            values.append(word(trun, at) if flags & bit else fallback)
            at += 4 if flags & bit else 0
        duration, size, sample_flags, composition = values
        if composition or duration <= 0 or size <= 0 or offset < low or offset + size > high:
            raise ValueError("invalid or reordered fragment sample")
        result.append(Sample(track.id, dts, dts, duration, stream_offset + offset,
                             size, not bool(sample_flags & 0x10000)))
        offset += size
        dts += duration
    if at != len(trun):
        raise ValueError("trailing sample metadata")
    return result
