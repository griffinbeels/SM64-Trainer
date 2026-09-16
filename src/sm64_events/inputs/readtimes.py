"""Exact read instants with bounded pending RAM, even on a paused game frame."""
import struct
import tempfile
import zlib
from datetime import datetime, timedelta, timezone

import numpy as np

_TIME = struct.Struct("<q")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_BLOCK_BYTES = 64 * 1024


def utc_time(stamp: str) -> datetime:
    """Compare instants, including legacy Z and variable-precision spellings."""
    moment = datetime.fromisoformat(stamp)
    if moment.utcoffset() is None:
        raise ValueError("input observation must have a timezone")
    return moment.astimezone(timezone.utc)


def micros(stamp: str) -> int:
    delta = utc_time(stamp) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def stamp_at(value: int) -> str:
    return (_EPOCH + timedelta(microseconds=value)).isoformat(timespec="microseconds")


class ReadTimes:
    """Retain exact instants until the sampler chooses a final state.

    Short histories stay packed in a bounded buffer: a within-frame rewrite
    then discards no compressor work, and a final state compresses once.
    Long holds stream into the compressed spool as that buffer fills, keeping
    pending RAM bounded even when the emulator holds one counter for hours.
    Both paths retain the same zlib wire format and close the temporary file.
    The sealed output and database necessarily grow with retained evidence.
    """

    RAM_BYTES = 64 * 1024

    def __init__(self):
        self._file = tempfile.SpooledTemporaryFile(max_size=self.RAM_BYTES)
        self._compressor = None
        self._pending = bytearray()
        self.lower = self.upper = None

    def add(self, stamp: str) -> None:
        if self._file.closed:
            raise ValueError("input observation history is closed")
        value = micros(stamp)
        self.lower = value if self.lower is None else min(self.lower, value)
        self.upper = value if self.upper is None else max(self.upper, value)
        self._pending.extend(_TIME.pack(value))
        if len(self._pending) >= self.RAM_BYTES:
            self._compress_pending()

    def _compress_pending(self) -> None:
        if self._compressor is None:
            self._compressor = zlib.compressobj()
        self._file.write(self._compressor.compress(self._pending))
        self._pending.clear()

    def finish(self) -> tuple[bytes, str, str]:
        try:
            if self._file.closed:
                raise ValueError("input observation history is closed")
            if self._compressor is None:
                blob = zlib.compress(self._pending)
            else:
                self._compress_pending()
                self._file.write(self._compressor.flush())
                self._file.seek(0)
                blob = self._file.read()
            return blob, stamp_at(self.lower), stamp_at(self.upper)
        finally:
            self.close()

    def close(self) -> None:
        self._pending.clear()
        self._compressor = None
        self._file.close()


def iter_times(blob: bytes):
    """Stream exact microseconds; never inflate a long hold into a Python list."""
    for raw in _time_blocks(blob):
        for value, in struct.iter_unpack("<q", raw):
            yield value


def contains_time(blob: bytes, low: int, high: int) -> bool:
    """Search exact instants in bounded blocks, validating even after a match.

    A paused frame can retain millions of polls. Comparing its packed integers
    avoids a Python iteration for each poll without filling gaps or assuming
    that UTC always advances. Exhaust the stream so a malformed tail cannot
    turn a valid prefix into accepted observation evidence.
    """
    found = False
    for raw in _time_blocks(blob):
        if not found:
            if len(raw) <= 1024:
                # Ordinary moving frames have only a handful of final reads;
                # avoid array dispatch for those small observations.
                found = any(low <= value <= high
                            for value, in struct.iter_unpack("<q", raw))
            else:
                values = np.frombuffer(raw, dtype="<i8")
                found = bool(np.any((values >= low) & (values <= high)))
    return found


def _time_blocks(blob: bytes):
    """Yield complete packed instants with bounded decompression memory."""
    reader = zlib.decompressobj()
    remainder = b""
    try:
        for offset in range(0, len(blob), _BLOCK_BYTES):
            pending = blob[offset:offset + _BLOCK_BYTES]
            while pending:
                raw = remainder + reader.decompress(pending, _BLOCK_BYTES)
                if reader.unused_data:
                    # After bounded decompression reaches EOF, trailing bytes
                    # can also remain in unconsumed_tail. Reject them before
                    # feeding that unchanged tail forever.
                    raise ValueError("trailing input observation times")
                stop = len(raw) - len(raw) % _TIME.size
                if stop:
                    yield raw[:stop]
                remainder = raw[stop:]
                pending = reader.unconsumed_tail
        if not reader.eof or remainder:
            raise ValueError("incomplete input observation times")
    except zlib.error as error:
        raise ValueError("invalid input observation times") from error
