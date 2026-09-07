"""Exact read instants with bounded pending RAM, even on a paused game frame."""
import struct
import tempfile
import zlib
from datetime import datetime, timedelta, timezone

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
    """Spool compressed instants until the sampler chooses a final state.

    A changed state discards this history. A completed frame transfers its
    compressed bytes to the chunk writer. Disk spill avoids a growing list
    while the emulator holds one counter for hours; the temporary file closes
    on both paths. Pending compression/RAM is bounded; the sealed output and
    database necessarily grow with the evidence retained.
    """

    RAM_BYTES = 64 * 1024

    def __init__(self):
        self._file = tempfile.SpooledTemporaryFile(max_size=self.RAM_BYTES)
        self._compressor = zlib.compressobj()
        self.lower = self.upper = None

    def add(self, stamp: str) -> None:
        value = micros(stamp)
        self.lower = value if self.lower is None else min(self.lower, value)
        self.upper = value if self.upper is None else max(self.upper, value)
        self._file.write(self._compressor.compress(_TIME.pack(value)))

    def finish(self) -> tuple[bytes, str, str]:
        try:
            self._file.write(self._compressor.flush())
            self._file.seek(0)
            return self._file.read(), stamp_at(self.lower), stamp_at(self.upper)
        finally:
            self.close()

    def close(self) -> None:
        self._file.close()


def iter_times(blob: bytes):
    """Stream exact microseconds; never inflate a long hold into a Python list."""
    reader = zlib.decompressobj()
    remainder = b""
    try:
        for offset in range(0, len(blob), _BLOCK_BYTES):
            pending = blob[offset:offset + _BLOCK_BYTES]
            while pending:
                raw = remainder + reader.decompress(pending, _BLOCK_BYTES)
                stop = len(raw) - len(raw) % _TIME.size
                for value, in struct.iter_unpack("<q", raw[:stop]):
                    yield value
                remainder = raw[stop:]
                pending = reader.unconsumed_tail
            if reader.unused_data:
                raise ValueError("trailing input observation times")
        if not reader.eof or remainder:
            raise ValueError("incomplete input observation times")
    except zlib.error as error:
        raise ValueError("invalid input observation times") from error
