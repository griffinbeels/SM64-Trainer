"""Bounded framing for the experimental FFmpeg fragmented-MP4 stream.

This accepts the observed ``ftyp, moov, (moof, mdat)+, [mfra]`` topology,
including 64-bit box sizes. It publishes initialization and media units as
soon as their last byte arrives; encoder shutdown is not required. Unsupported
topologies, unfinished boxes and oversized units fail explicitly.

Bytes, timestamps and payloads are never changed. This is framing, not sample
validation or an index: a complete box pair alone does not prove decoder-safe
random access, sample duration, A/V sync, codec compatibility or valid truns.
The producer must use the tested FFmpeg mux topology, and independently index
source PTS/DTS and random-access dependencies before exposing review media.
The opt-in FfmpegAvSink fragment consumer uses this through FragmentArchive;
the application's recorder/service default and saved/export MP4 stay unchanged.
See docs/replay-fragments.md for evidence and remaining integration gates.
"""
from dataclasses import dataclass
import struct
from typing import Literal


@dataclass(frozen=True)
class FragmentUnit:
    """An immutable complete byte range in the original encoder-run output."""

    kind: Literal["init", "media", "trailer"]
    offset: int
    data: bytes


class FragmentReader:
    """Incrementally frame one FFmpeg run, retaining at most one bounded unit.

    ``feed`` accepts arbitrary chunk boundaries, including split box headers.
    Drain each returned list before feeding more bytes to bound caller memory.
    ``finish`` must be called on producer EOF to distinguish a complete run
    from a truncated final fragment. Reuse after EOF or failure is refused.
    """

    def __init__(self, max_unit_bytes: int = 16 * 1024 * 1024) -> None:
        if max_unit_bytes < 32:
            raise ValueError("fragment unit limit must be at least 32 bytes")
        self._limit = max_unit_bytes
        self._box = bytearray()
        self._unit = bytearray()
        self._wanted = 8
        self._header_complete = False
        self._phase = b"ftyp"
        self._offset = 0
        self._media_count = 0
        self._closed = False

    def feed(self, data: bytes) -> list[FragmentUnit]:
        """Return only complete units; no partial media escapes on short reads."""
        if self._closed:
            raise ValueError("fragment reader is closed")
        result = []
        incoming = memoryview(data)
        try:
            while incoming:
                count = min(self._wanted - len(self._box), len(incoming))
                self._box.extend(incoming[:count])
                incoming = incoming[count:]
                if len(self._box) < self._wanted:
                    continue
                if not self._header_complete:
                    self._read_header()
                if self._header_complete and len(self._box) == self._wanted:
                    unit = self._complete_box()
                    if unit is not None:
                        result.append(unit)
            return result
        except ValueError:
            self._closed = True
            self._box.clear()
            self._unit.clear()
            raise

    def _read_header(self) -> None:
        size, kind = struct.unpack_from(">I4s", self._box)
        header_size = 16 if size == 1 else 8
        if len(self._box) < header_size:
            self._wanted = header_size
            return
        if size == 1:
            size = struct.unpack_from(">Q", self._box, 8)[0]
        if size < header_size:
            raise ValueError("open-ended or undersized MP4 box")
        if len(self._unit) + size > self._limit:
            raise ValueError("MP4 fragment unit exceeds byte limit")
        trailer = self._phase == b"moof" and kind == b"mfra" and self._media_count
        if kind != self._phase and not trailer:
            raise ValueError(f"unsupported MP4 box order: expected {self._phase!r}, got {kind!r}")
        self._wanted = size
        self._header_complete = True

    def _complete_box(self) -> FragmentUnit | None:
        kind = bytes(self._box[4:8])
        self._unit.extend(self._box)
        self._box.clear()
        self._wanted = 8
        self._header_complete = False
        if kind in (b"ftyp", b"moof"):
            self._phase = b"moov" if kind == b"ftyp" else b"mdat"
            return None
        unit_kind: Literal["init", "media", "trailer"] = "init"
        if kind == b"mdat":
            unit_kind = "media"
            self._media_count += 1
        elif kind == b"mfra":
            unit_kind = "trailer"
        unit = FragmentUnit(unit_kind, self._offset, bytes(self._unit))
        self._offset += len(self._unit)
        self._unit.clear()
        self._phase = b"done" if kind == b"mfra" else b"moof"
        return unit

    def finish(self) -> None:
        """Close the reader, rejecting incomplete headers, bodies or box pairs."""
        if self._closed:
            raise ValueError("fragment reader is closed")
        self._closed = True
        incomplete = bool(self._box or self._unit or not self._media_count)
        self._box.clear()
        self._unit.clear()
        if incomplete:
            raise ValueError("unfinished fragmented MP4 stream")
