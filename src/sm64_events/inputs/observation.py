"""Local polling provenance; these IDs do not identify rendered game states.

The sampler's run and ordinal survive RLE and chunk boundaries. UTC locates an
observation beside journal events, while the ordinal retains capture order
even if the wall clock moves backward. Neither proves an unobserved reset or
that a controller read happened after the game's in-frame controller rewrite.
"""
import json
import re
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime

from sm64_events.inputs.readtimes import contains_time, micros, utc_time

_LENGTH = struct.Struct("<I")


@dataclass(frozen=True)
class InputObservation:
    """Local state identity with exact read instants, never inferred coverage."""
    source_id: str
    sequence: int
    observed_utc: str
    first_observed_utc: str | None = None
    lower_utc: str | None = None
    upper_utc: str | None = None
    history: bytes | None = None

    def __post_init__(self):
        for field in ("observed_utc", "first_observed_utc", "lower_utc", "upper_utc"):
            if field in ("lower_utc", "upper_utc") and getattr(self, field) is None:
                endpoints = (self.observed_utc, self.first_observed_utc)
                choose = min if field == "lower_utc" else max
                object.__setattr__(self, field, choose(endpoints))
            stamp = getattr(self, field) or self.observed_utc
            moment = utc_time(stamp)
            if datetime.fromisoformat(stamp).utcoffset().total_seconds() != 0:
                raise ValueError("input observation must use UTC")
            object.__setattr__(self, field, moment.isoformat(timespec="microseconds"))

    def within(self, start: str, end: str) -> bool:
        low, high = micros(start), micros(end)
        if self.history is None:
            return any(low <= micros(stamp) <= high
                       for stamp in (self.first_observed_utc, self.observed_utc))
        return contains_time(self.history, low, high)


def _validate(observations: list[InputObservation], count: int) -> None:
    if len(observations) != count or not observations:
        raise ValueError("input observation count does not match captured frames")
    source = observations[0].source_id
    previous = -1
    for observation in observations:
        if (not isinstance(observation.source_id, str)
                or not re.fullmatch(r"[A-Za-z0-9:_-]{1,80}", observation.source_id)
                or observation.source_id != source
                or type(observation.sequence) is not int
                or not previous < observation.sequence <= 0xFFFFFFFFFFFFFFFF):
            raise ValueError("invalid input observation identity or order")
        previous = observation.sequence


def encode_observations(observations: list[InputObservation], count: int) -> bytes:
    _validate(observations, count)
    rows = [[o.source_id, o.sequence, o.observed_utc, o.first_observed_utc,
             o.lower_utc, o.upper_utc]
            for o in observations]
    metadata = zlib.compress(json.dumps(rows, separators=(",", ":")).encode("utf-8"))
    out = bytearray(_LENGTH.pack(len(metadata)) + metadata)
    for observation in observations:
        history = observation.history or b""
        out += _LENGTH.pack(len(history))
        out += history
    return bytes(out)


def decode_observations(blob: bytes, count: int) -> list[InputObservation]:
    try:
        size, = _LENGTH.unpack_from(blob)
        at = _LENGTH.size + size
        reader = zlib.decompressobj()
        raw = reader.decompress(blob[_LENGTH.size:at], max_length=count * 256 + 4)
        if not reader.eof or reader.unused_data:
            raise ValueError("invalid input observation payload size")
        rows = json.loads(raw)
        observations = []
        for row in rows:
            length, = _LENGTH.unpack_from(blob, at)
            at += _LENGTH.size
            history = blob[at:at + length]
            if len(history) != length:
                raise ValueError("incomplete input observation history")
            observations.append(InputObservation(*row, history=history or None))
            at += length
        if at != len(blob):
            raise ValueError("trailing input observation payload")
        _validate(observations, count)
        return observations
    except (TypeError, AttributeError, struct.error, zlib.error, UnicodeError) as error:
        raise ValueError("invalid input observation payload") from error
