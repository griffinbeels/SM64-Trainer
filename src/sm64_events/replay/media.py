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


def picture_duration_filter(end_pts: int | None = None) -> str:
    """Hold each encoded picture until the next PTS, without moving either.

    The encoders' nominal packet durations do not describe a VFR hold. The
    segment muxer otherwise files those holds as coverage holes. A finished
    cut also knows when its final picture ends; a live run does not.
    Requires ordered packets (our encoders disable B-frames).
    """
    tail = "DURATION" if end_pts is None else f"max(1,{end_pts}-PTS)"
    return f"setts=pts=PTS:dts=DTS:duration='if(gt(NEXT_PTS,PTS),NEXT_PTS-PTS,{tail})'"


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


def next_picture_pts(run: MediaRun, stamp: float, last_pts: int | None) -> int:
    """Assign the next transport tick before encoding, preserving feed order.

    Distinct captured pictures can quantize to the same tick. Record this actual
    assigned tick with the occurrence; never let an encoder retime it invisibly.
    The caller commits last_pts only after the owning stage accepts the picture.
    """
    return max(run.ticks_at(stamp), last_pts + 1 if last_pts is not None else 0)
