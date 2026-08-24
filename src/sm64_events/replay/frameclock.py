# src/sm64_events/replay/frameclock.py
"""WHEN each game frame happened, on the wall clock -- the recorder's stamp.

Round 32, item 17. The footage duplicates and skips single game frames (his
Usamune counter across nine Forward-1 presses: 26, 27, 27, 29, 30, 31, 32,
33, 33) because the emulator presents on its own schedule while the capture
runs on a wall clock. No constant offset can describe that -- a one-frame
shift appears mid-clip and heals (his second sighting, 2026-08-23, was
exactly that shape) -- so the clip has to SAY which game frame each video
frame shows. His ruling: "We need 100% accuracy for this. If it's wrong
even once, then it can't be relied on as a tool."

The one fact that makes this possible without touching the encoder: the
AV sink stamps every fed frame by SYSTEM WALL CLOCK (the two-clocks fix,
ffmpeg_sink.py), and the extractor records the clip's own first-frame wall
time (`start_utc`). So video frame k sits at `start_utc + (k+0.5)/fps` on
the same clock the poller lives on, and all that is missing is the series
"game frame F's logic ran at wall time t". The poller marks that edge here
at its 250 Hz sampling rate (+-4 ms = +-0.12 game frames); `frame_map`
then answers, per video frame, which game frame its PICTURE shows.

The display-lag constant rides INSIDE the map (`lag_s`): the screen shows
a frame the game finished about one frame earlier (measured from his nine
readings, replay/service.py::DISPLAY_LAG_FRAMES), so the picture at wall
time W shows the last game frame whose logic ran at or before W - lag.
Item 8's scorer re-measures that constant against Usamune's own pixels.

Bounded: a deque sized to hold RETENTION_S of frames -- the ring's own
retention decides how far back a clip can be cut, so pairs older than that
can never be asked about. Thread-shape: ONE writer (the poller thread) and
readers on the request path; deque.append is atomic and a reader iterates
a snapshot copy, so no lock.
"""
import time
from collections import deque
from datetime import datetime

from sm64_events.core.timefmt import GAME_FPS

# How long a pair is answerable. The ring's default retention is shorter;
# generous here because 30 pairs/s costs ~50 bytes each.
RETENTION_S = 1800.0
_FPS_CEILING = 40                      # eviction sizing only, above real 30
_FEED_CEILING = 70                     # the sink feeds at 60; headroom


class FrameClock:
    def __init__(self, retention_s: float = RETENTION_S, now=time.time):
        self._now = now
        self._pairs: deque[tuple[float, int]] = deque(
            maxlen=int(retention_s * _FPS_CEILING))
        # What the SINK'S FEEDER actually fed, tagged at CAPTURE with the RAM
        # frame current at that instant -- the v2 series, preferred by
        # frame_map. Scored on his clip 741 (2026-08-23): the edge series
        # plus any constant lag left +-1 game frame of error in BOTH
        # directions within one clip, because the picture's presentation and
        # the feeder's staleness each wobble against the logic clock. Tagging
        # at capture and recording at feed removes both terms: a stall's
        # ffmpeg dup repeats the last fed frame, and so does this series.
        self._feeds: deque[tuple[float, int]] = deque(
            maxlen=int(retention_s * _FEED_CEILING))

    def mark(self, frame: int) -> None:
        """The game's frame counter just advanced to `frame`."""
        self._pairs.append((self._now(), frame))

    def latest_frame(self) -> int | None:
        """The frame the game is computing RIGHT NOW (the last marked edge)
        -- what the capture callback tags each captured picture with."""
        return self._pairs[-1][1] if self._pairs else None

    def mark_feed(self, tag: int | None) -> None:
        """The sink's feeder just fed a frame whose capture-time tag was
        `tag`. An untagged frame (no sampler, clock cold) records nothing --
        the map then falls back to the edge series for that stretch."""
        if tag is not None:
            self._feeds.append((self._now(), tag))

    def frame_map(self, start_utc: datetime, duration_s: float, fps: float,
                  lag_s: float) -> list[int | None] | None:
        """Which game frame each video frame of a clip shows.

        `start_utc` is the clip's first-frame wall time (the extractor's
        `start_utc`), `fps` the clip's encode rate. Entry k answers for the
        video frame at `start_utc + (k+0.5)/fps`; a slot before the first
        marked pair is None (the clock was not running yet -- an attach
        gap), and a whole clip outside the marked span returns None so the
        sidecar simply carries no map and readers fall back to the offset.
        """
        start = start_utc.timestamp()
        count = max(1, round(duration_s * fps))
        feeds = list(self._feeds)
        if feeds and duration_s > 0 and feeds[0][0] <= start \
                and feeds[-1][0] >= start:
            # v2: what was actually FED. Slot k took the last frame fed at or
            # before its own wall time (a feeder stall means ffmpeg dup'd
            # exactly that frame); the tag is the RAM frame at capture, and
            # the picture on screen then trails the logic by the pipeline
            # depth -- the same DISPLAY_LAG constant, now applied per frame
            # rather than per clock.
            lag_frames = round(lag_s * GAME_FPS)
            out: list[int | None] = []
            at = 0
            current: int | None = None
            for index in range(count):
                wall = start + (index + 0.5) / fps
                while at < len(feeds) and feeds[at][0] <= wall:
                    current = feeds[at][1]
                    at += 1
                out.append(None if current is None else current - lag_frames)
            return out
        pairs = list(self._pairs)
        if not pairs or duration_s <= 0:
            return None
        if start + duration_s - lag_s < pairs[0][0] or \
                start - lag_s > pairs[-1][0]:
            return None
        out = []
        at = 0
        current = None
        for index in range(count):
            wall = start + (index + 0.5) / fps - lag_s
            while at < len(pairs) and pairs[at][0] <= wall:
                current = pairs[at][1]
                at += 1
            out.append(current)
        return out
