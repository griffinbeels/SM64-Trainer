"""Native seekable MP4 headers over existing indexed H.264/AAC sample bytes.

Build only tables, never read/decode/re-encode payloads during construction.
The caller owns archive leases for every read. Media offsets are explicit;
edit lists hide the preceding decoder GOP on the ordinary native player.
"""
from bisect import bisect_right
from contextlib import ExitStack
from dataclasses import dataclass
from itertools import groupby
import struct

from sm64_events.core.profiling import measured
from sm64_events.replay.fragmentindex import boxes, child, word


def box(kind, payload):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def table(kind, rows, fmt):
    return box(kind, struct.pack(">II", 0, len(rows)) + b"".join(struct.pack(fmt, *row) for row in rows))


def duration_header(data, value, *, movie=False, track=False):
    result = bytearray(data)
    version = result[0]
    at = (28 if version == 1 else 20) if track else (24 if version == 1 else 16)
    struct.pack_into(">Q" if version == 1 else ">I", result, at, value)
    if movie:
        struct.pack_into(">I", result, 20 if version == 1 else 12, 90000)
    return result


def rebuild(data, replacements):
    return b"".join(replacements.get(kind, box(kind, bytes(payload)))
                    for kind, payload, *_ in boxes(data))


@dataclass(frozen=True, slots=True)
class Range:
    start: int
    path: object
    offset: int
    size: int


class VirtualMp4:
    @measured("replay.native_index")
    def __init__(self, init, tracks, units, start, end):
        self.start, self.end = start, end
        # Physical source order preserves interleaving and adjacent reads.
        ordered = [(unit, sample) for unit in units for sample in sorted(unit.samples, key=lambda s: s.offset)
                   if sample.pts * 90000 < end * tracks[sample.track_id].timescale]
        self._by_track = {identity: [] for identity in tracks}
        self._ranges = []
        offset = 0
        for unit, sample in ordered:
            self._by_track[sample.track_id].append((sample, offset))
            physical = unit.offset + sample.offset - unit.stream_offset
            if (self._ranges and self._ranges[-1].path == unit.path
                    and self._ranges[-1].offset + self._ranges[-1].size == physical):
                previous = self._ranges.pop()
                self._ranges.append(Range(previous.start, unit.path, previous.offset, previous.size + sample.size))
            else:
                self._ranges.append(Range(offset, unit.path, physical, sample.size))
            offset += sample.size
        if any(not samples for samples in self._by_track.values()):
            raise ValueError("MP4 selection needs each track")
        self.frame_ticks = [sample.pts-start for identity, samples in self._by_track.items()
                            if tracks[identity].kind == "video" for sample, _ in samples if sample.pts >= start]
        # MOV demuxers can clamp the first picture to zero for an edit between
        # pictures. Our source map requires exact ticks, so the caller must use
        # the original held-picture boundary (FragmentMedia.open owns that).
        if not self.frame_ticks or self.frame_ticks[0] != 0:
            raise ValueError("native replay must start on a source picture")
        self._tracks, self._init = tracks, init
        ftyp = box(b"ftyp", child(init, b"ftyp").tobytes())
        # Fixed-width co64 means the header size is independent of offsets.
        provisional = self._moov(0)
        base = len(ftyp) + len(provisional) + 16
        self.header = ftyp + self._moov(base) + struct.pack(">I4sQ", 1, b"mdat", offset + 16)
        self.size = len(self.header) + offset

    def _moov(self, base):
        output = []
        for kind, payload, *_ in boxes(child(self._init, b"moov")):
            if kind == b"mvhd":
                output.append(box(kind, duration_header(payload, self.end - self.start, movie=True)))
            elif kind == b"trak":
                output.append(self._track(payload, base))
            elif kind != b"mvex":
                output.append(box(kind, payload.tobytes()))
        return box(b"moov", b"".join(output))

    def _track(self, trak, base):
        tkhd, mdia = child(trak, b"tkhd"), child(trak, b"mdia")
        identity = word(tkhd, 20 if tkhd[0] else 12)
        track, samples = self._tracks[identity], self._by_track[identity]
        first, last = samples[0][0], samples[-1][0]
        duration = self.end - self.start
        media_start = (self.start * track.timescale + 45000) // 90000 - first.dts
        if media_start < 0:
            raise ValueError("MP4 selection lacks initial track dependency")
        edit = box(b"edts", box(b"elst", struct.pack(">IIQqhh", 0x01000000, 1, duration, media_start, 1, 0)))
        minf = child(mdia, b"minf")
        tables = self._tables(child(minf, b"stbl"), samples, base, track.kind)
        media = rebuild(mdia, {
            b"mdhd": box(b"mdhd", duration_header(child(mdia, b"mdhd"), last.dts + last.duration - first.dts)),
            b"minf": box(b"minf", rebuild(minf, {b"stbl": tables})),
        })
        rebuilt = rebuild(trak, {b"tkhd": box(b"tkhd", duration_header(tkhd, duration, track=True)),
                                 b"edts": b"", b"mdia": box(b"mdia", media)})
        return box(b"trak", rebuilt + edit)

    @staticmethod
    def _tables(stbl, samples, base, kind):
        sizes = [(s.size,) for s, _ in samples]
        durations = [(sum(1 for _ in group), duration)
                     for duration, group in groupby(s.duration for s, _ in samples)]
        tables = [box(b"stsd", child(stbl, b"stsd").tobytes()), table(b"stts", durations, ">II"),
                  table(b"stsc", [(1, 1, 1)], ">III"),
                  box(b"stsz", struct.pack(">III", 0, 0, len(samples))
                      + b"".join(struct.pack(">I", *size) for size in sizes)),
                  table(b"co64", [(base + offset,) for _, offset in samples], ">Q")]
        if kind == "video":
            tables.append(table(b"stss", [(i + 1,) for i, (sample, _) in enumerate(samples) if sample.key], ">I"))
        return box(b"stbl", b"".join(tables))

    def chunks(self, start=0, end=None):
        """Read a half-open HTTP/file byte range while the caller holds leases."""
        end = self.size if end is None else end
        if not 0 <= start <= end <= self.size:
            raise ValueError("invalid MP4 byte range")
        if start < len(self.header):
            yield self.header[start:min(end, len(self.header))]
        low, high = max(0, start - len(self.header)), end - len(self.header)
        if high <= low:
            return
        first_index = max(0, bisect_right(self._ranges, low, key=lambda item: item.start) - 1)
        with ExitStack() as handles:
            path, source, cursor = None, None, 0
            for index in range(first_index, len(self._ranges)):
                item = self._ranges[index]
                if item.start >= high:
                    break
                offset = max(0, low - item.start)
                count = min(item.size, high - item.start) - offset
                if item.path != path:
                    handles.close()
                    source = handles.enter_context(item.path.open("rb"))
                    path, cursor = item.path, 0
                wanted = item.offset + offset
                if cursor != wanted:
                    source.seek(wanted)
                while count:
                    data = source.read(min(count, 65536))
                    if not data:
                        raise OSError("replay sample bytes were truncated")
                    count -= len(data)
                    wanted += len(data)
                    yield data
                cursor = wanted
