"""The immutable media clock of one picture-feed encoder process.

NUT receives assigned video ticks relative to the first picture. MPEG-TS keeps
that 90 kHz clock. A segment or cut must retain the run identity and that origin;
fitting an offset against a periodic feed cannot establish picture identity.
"""
from dataclasses import dataclass
from fractions import Fraction
from uuid import uuid4

MEDIA_HZ = 90_000
MEDIA_TIME_BASE = Fraction(1, MEDIA_HZ)


@dataclass(frozen=True)
class MediaRun:
    id: str
    origin_ts: float

    @classmethod
    def starting_at(cls, stamp: float) -> "MediaRun":
        # Segment UTC boundaries use datetime microseconds. Represent the
        # origin on that same grid so clamping to coverage cannot skip PTS 0.
        return cls(uuid4().hex, round(stamp, 6))

    def microseconds_at(self, stamp: float) -> int:
        return round((stamp - self.origin_ts) * 1_000_000)

    def ticks_at(self, stamp: float) -> int:
        # Quantize capture microseconds using FFmpeg's AV_ROUND_NEAR_INF rule.
        micros = self.microseconds_at(stamp)
        return (micros * 9 + 50) // 100
