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

THREE series, best-first at lookup, each one fallback for the next:

- PRESENTS (v4, item 30): the poller watches PJ64's host-side present
  counter (memory/present.py hunts it each session; tools/
  probe_host_present.py is the live evidence) and records every tick --
  the moment the video plugin actually handed a new picture to the
  screen, seen from the RAM side. Within a run of presents the counter
  enumerates game frames IN ORDER, so each tick's frame comes from the
  counter's own value plus a per-run constant, and the logic->present
  wobble that capped v2 lands in the tick TIMES, where it belongs. The
  derivation is `derive_present_frames` below.
- FEEDS (v2): what the SINK'S FEEDER actually fed, tagged at CAPTURE.
  Scored on his clip 741 (2026-08-23): the edge series plus any constant
  lag left +-1 game frame of error in BOTH directions within one clip.
  Tagging at capture and recording at feed removes the grab- and
  feeder-staleness terms; the present wobble remains, which is v4's job.
  The v2 tag now ALSO carries the picture's own composition time
  (WGC's SystemRelativeTime through replay/clock.py -- item 30's probe
  target (a)), so the presents lookup keys on when the picture was
  composed rather than on when our callback ran.
- EDGES (v1): the poller's 250 Hz stamp of each logic-frame edge, with
  the DISPLAY_LAG constant applied per clip. The only series that needs
  no recorder and no hunt; the floor, never the ceiling.

The one fact that makes any of this possible without touching the encoder:
the AV sink stamps every fed frame by SYSTEM WALL CLOCK (the two-clocks
fix, ffmpeg_sink.py), and the extractor records the clip's own first-frame
wall time (`start_utc`). So video frame k sits at `start_utc + (k+0.5)/fps`
on the same clock the poller lives on.

Bounded: deques sized to hold RETENTION_S of entries -- the ring's own
retention decides how far back a clip can be cut, so entries older than
that can never be asked about. Thread-shape: the poller thread writes
_pairs and _presents, the sink's feeder thread writes _feeds, the capture
thread reads _pairs (capture_tag); deque.append is atomic and every reader
iterates a snapshot copy, so no lock.
"""
import time
from bisect import bisect_right
from collections import Counter, deque
from datetime import datetime

from sm64_events.core.timefmt import GAME_FPS

# How long an entry is answerable. The ring's default retention is shorter;
# generous here because ~160 entries/s cost ~50 bytes each.
RETENTION_S = 1800.0
_FPS_CEILING = 40                      # eviction sizing only, above real 30
_FEED_CEILING = 70                     # the sink feeds at 60; headroom
_PRESENT_CEILING = 40                  # presents tick once per game frame

# -- the present pipeline's constants (v4) -----------------------------------
# The screen begins showing, at the FLOOR of a run's delta band, the frame
# the game finished this many frames earlier. Default carried over from the
# same measurement that set service.DISPLAY_LAG_FRAMES (his nine Usamune
# readings, 2026-08-23); item 8's pixel scorer is the instrument that
# corrects it -- and with presents a wrong value is a CONSTANT residual,
# which is exactly the kind of error a constant can fix (v2's could not).
PRESENT_LAG_FRAMES = 1
# How far a recorded tick's time trails the moment the picture that present
# produced is STAMPED for capture: the 250 Hz poll observes the counter move
# up to 4 ms late, and the picture's own capture stamp (WGC composition
# time) lands up to one 60 Hz refresh after the plugin's present call.
# Subtracting the sum aligns the lookup with what the pixels actually show.
# MEASURED END TO END 2026-08-25 on attempt 1952 (the first clip mapped from
# a true per-frame counter, scored by A-icon template match): all 32 press+
# release edges within {-2,-1,0} slots, centered at -1 slot = ~17 ms of map
# lateness -- a CONSTANT, the kind this number exists to absorb. 0.019 =
# 2 ms mean poll delay + ~17 ms present-to-stamp. Re-measure with the same
# instrument if the capture architecture changes; the synthetic tests pass
# their own world's value because their fake capture has no compose stage.
PRESENT_TICK_TRAIL_S = 0.019
# The whole chain answers half a video slot LATE, uniformly: scored sharp
# (A-icon template) on attempt 2147 (2026-08-25), the feed series put 10/10
# button edges at exactly -1 slot and edge-normalized presents 25/27 at -1,
# and his frame-stepped screenshots (attempt 2347) show the same off-by-one
# at pause. One door absorbs it: every slot's lookup wall shifts forward by
# one slot, so each slot answers with the series entry the screen actually
# showed. Applies to all three series -- the constant is a property of the
# encode chain (capture -> feed -> CFR slotting), not of any one series.
MAP_WALL_BIAS_S = 1 / 60
# A present's picture holds the screen until the next present. Across a
# derivation gap (a run the run rules dropped) hold at most this long,
# then let the feed series answer -- a frozen answer seconds stale is a
# guess wearing precision.
PRESENT_HOLD_S = 1.0
# How far before a clip ticks are pulled so the first slots sit in a run
# whose delta floor is already established.
PRESENT_CONTEXT_S = 8.0

# -- derive_present_frames' run rules ----------------------------------------
_DELTA_BAND = 2            # |delta - run base| beyond this = out of band
_SUSTAINED_SHIFT = 3       # consecutive out-of-band ticks that end a run
_MAX_COUNT_JUMP = 8        # counter jumped further = not the same run
_MAX_TICK_GAP_S = 0.5      # silence longer than this = not the same run
_MIN_RUN_TICKS = 3         # shorter runs cannot establish their own floor
_RUN_RATE_LO = 20.0        # a run ticking outside this rate band is not
_RUN_RATE_HI = 40.0        # the present counter -- refuse it (false find)


def derive_present_frames(ticks, min_lag_frames: int = PRESENT_LAG_FRAMES,
                          trail_s: float = PRESENT_TICK_TRAIL_S,
                          edge_pairs=None):
    """Per present tick, the game frame its picture shows.

    `ticks` is [(wall, count, timer_or_None)] in wall order -- the poller's
    observations of the host present counter beside gGlobalTimer. Within a
    RUN (counter advancing steadily, logic advancing with it) presents
    consume rendered frames in order, so frame = count + shift for one
    constant shift; the per-tick spread of delta = timer - count is the
    logic->present wobble and must NOT reach the frames. The run's shift
    comes from its delta FLOOR (the smallest delta seen at least twice):
    at floor ticks the screen shows the frame the game finished
    `min_lag_frames` earlier, so shift = floor - min_lag_frames.

    `edge_pairs` -- the poller's (wall, frame) edge series -- anchors each
    run's PHASE: every hunted counter is a different variable incremented
    at its own point in the frame pipeline, so tick times carry a
    per-counter offset no global constant can absorb (measured 2026-08-25:
    session 12's counter and session 13's both centered -1 slot, one at
    trail 0.002 and one at 0.019 -- seventeen milliseconds of phase
    between two members of the same family). Normalizing each run by the
    MEDIAN of (tick wall - the same frame's edge wall) puts every counter
    on the edge timeline while keeping the per-tick WANDER -- the display
    information presents exist to carry. A run too short to normalize
    (fewer than three edge matches) falls back to `trail_s`.

    A SUSTAINED delta change is a different regime (a load: logic frozen
    while presents keep ticking, or a counter reset) -- the run closes and
    the next one re-derives its own floor. A run whose LOGIC barely moved
    while its counter ticked is a re-present train (the plugin re-showing a
    frozen frame): consuming-in-order does not hold there, so it derives
    nothing and the feed series answers instead. One or two out-of-band
    deltas are a late timer read: their ticks keep their place in the run,
    their deltas stay out of the floor. Ticks whose timer read straddled a
    frame (timer None) carry their count and nothing else.
    """
    derived: list[tuple[float, int]] = []
    run: list[tuple[float, int]] = []
    known_deltas: list[tuple[int, int]] = []      # (count, delta) pairs
    delta_base: int | None = None
    outliers: list[tuple[tuple[float, int], int | None]] = []
    edge_wall_of = {frame: wall for wall, frame in (edge_pairs or [])}

    def run_phase(kept, shift):
        """The run's tick-to-edge median offset, or None to use trail_s."""
        offsets = [wall - edge_wall_of[count_value + shift]
                   for wall, count_value in kept
                   if count_value + shift in edge_wall_of]
        if len(offsets) < 3:
            return None
        return sorted(offsets)[len(offsets) // 2]

    def close_run() -> None:
        nonlocal run, known_deltas, delta_base
        if len(run) >= _MIN_RUN_TICKS and known_deltas:
            span_s = run[-1][0] - run[0][0]
            plausible = True
            if span_s > 0.5:
                count_rate = (run[-1][1] - run[0][1]) / span_s
                plausible = _RUN_RATE_LO <= count_rate <= _RUN_RATE_HI
            first_count, first_delta = known_deltas[0]
            last_count, last_delta = known_deltas[-1]
            count_span = last_count - first_count
            timer_span = (last_count + last_delta) - (first_count
                                                      + first_delta)
            if count_span > 0 and timer_span * 2 < count_span:
                plausible = False        # re-present train: logic frozen
            if plausible:
                occurrences = Counter(delta for _count, delta in known_deltas)
                sustained = [value for value, times in occurrences.items()
                             if times >= 2]
                floor = (min(sustained) if sustained
                         else min(delta for _count, delta in known_deltas))
                shift = floor - min_lag_frames
                # A tail sagging BELOW the floor is a freeze starting (logic
                # stopped, counter kept ticking): those ticks would fabricate
                # frames past the frozen clock, so the run ends at the last
                # tick whose delta still reached the floor.
                last_sound_count = None
                for count_value, delta in reversed(known_deltas):
                    if delta >= floor:
                        last_sound_count = count_value
                        break
                kept = []
                for wall, count_value in run:
                    if last_sound_count is None \
                            or count_value > last_sound_count:
                        break
                    kept.append((wall, count_value))
                phase = run_phase(kept, shift)
                anchor = phase if phase is not None else trail_s
                for wall, count_value in kept:
                    derived.append((wall - anchor, count_value + shift))
        run, known_deltas, delta_base = [], [], None

    previous: tuple[float, int] | None = None
    for wall, count_value, timer in ticks:
        if previous is not None and (
                count_value <= previous[1]
                or count_value - previous[1] > _MAX_COUNT_JUMP
                or wall - previous[0] > _MAX_TICK_GAP_S):
            outliers.clear()
            close_run()
        previous = (wall, count_value)
        delta = None if timer is None else timer - count_value
        in_band = (delta is None or delta_base is None
                   or abs(delta - delta_base) <= _DELTA_BAND)
        if in_band:
            # Buffered outliers were transient: they keep their place in the
            # run (frame = count + shift holds for them) but their deltas
            # never touch the floor.
            run.extend(place for place, _delta in outliers)
            outliers.clear()
            run.append((wall, count_value))
            if delta is not None:
                known_deltas.append((count_value, delta))
                if delta_base is None:
                    delta_base = delta
        else:
            outliers.append(((wall, count_value), delta))
            if len(outliers) >= _SUSTAINED_SHIFT:
                close_run()
                for place, outlier_delta in outliers:
                    run.append(place)
                    if outlier_delta is not None:
                        known_deltas.append((place[1], outlier_delta))
                        if delta_base is None:
                            delta_base = outlier_delta
                outliers.clear()
    close_run()
    return derived


def _source_label(sources: set[str]) -> str:
    return sources.pop() if len(sources) == 1 else "mixed"


class FrameClock:
    def __init__(self, retention_s: float = RETENTION_S, now=time.time,
                 present_trail_s: float = PRESENT_TICK_TRAIL_S,
                 map_wall_bias_s: float = MAP_WALL_BIAS_S):
        self._now = now
        # Injectable so tests model their own world's capture physics; the
        # shipped defaults carry the measured values (see the constants).
        self._present_trail_s = present_trail_s
        self._map_wall_bias_s = map_wall_bias_s
        self._pairs: deque[tuple[float, int]] = deque(
            maxlen=int(retention_s * _FPS_CEILING))
        # (feed wall time, capture-time RAM frame tag, capture composition
        # time) per frame the sink's feeder actually fed -- the v2 series.
        self._feeds: deque[tuple[float, int | None, float | None]] = deque(
            maxlen=int(retention_s * _FEED_CEILING))
        # (observed wall time, counter value, RAM frame at the same poll
        # tick or None on a straddle) per host present tick -- the v4 series.
        self._presents: deque[tuple[float, int, int | None]] = deque(
            maxlen=int(retention_s * _PRESENT_CEILING))

    def mark(self, frame: int) -> None:
        """The game's frame counter just advanced to `frame`."""
        self._pairs.append((self._now(), frame))

    def latest_frame(self) -> int | None:
        """The frame the game is computing RIGHT NOW (the last marked edge)
        -- what the capture callback tags each captured picture with."""
        return self._pairs[-1][1] if self._pairs else None

    def mark_present(self, count: int, timer: int | None) -> None:
        """The host present counter just advanced to `count`; the game's
        frame counter read `timer` at the same poll tick (None when that
        tick's read straddled a frame)."""
        self._presents.append((self._now(), count, timer))

    def capture_tag(self, capture_ts: float | None):
        """The tag a captured picture carries to the feeder: the RAM frame
        current at capture (v2's key) and the picture's own composition
        time as an epoch float (v4's key -- WGC's SystemRelativeTime
        through the recorder's CaptureClock). None when neither is known,
        so an untagged frame still records nothing."""
        frame = self.latest_frame()
        if frame is None and capture_ts is None:
            return None
        return (frame, capture_ts)

    def mark_feed(self, tag) -> None:
        """The sink's feeder just fed a frame carrying `tag` (capture_tag's
        pair). An untagged frame records nothing -- the map then falls back
        to the edge series for that stretch."""
        if tag is None:
            return
        frame, capture_ts = tag
        self._feeds.append((self._now(), frame, capture_ts))

    def frame_map(self, start_utc: datetime, duration_s: float, fps: float,
                  lag_s: float) -> tuple[list[int | None], str] | None:
        """Which game frame each video frame of a clip shows, plus which
        series answered ("presents" / "feeds" / "edges" / "mixed").

        `start_utc` is the clip's first-frame wall time (the extractor's
        `start_utc`), `fps` the clip's encode rate. Entry k answers for the
        video frame at `start_utc + (k+0.5)/fps`; a slot no series can
        answer is None, and a clip no series reaches at all returns None so
        the sidecar simply carries no map and readers fall back to the
        offset. Per slot the series rank presents > feeds > edges: the
        presents lookup keys on the fed picture's own composition time,
        the feed tag carries `lag_s` as a constant, and the edge lookup is
        the v1 rule unchanged.
        """
        start = start_utc.timestamp()
        if duration_s <= 0:
            return None
        slot_count = max(1, round(duration_s * fps))
        pairs = list(self._pairs)
        feeds = list(self._feeds)
        ticks = [tick for tick in list(self._presents)
                 if start - PRESENT_CONTEXT_S <= tick[0]
                 <= start + duration_s + PRESENT_CONTEXT_S]
        window_pairs = [pair for pair in pairs
                        if start - PRESENT_CONTEXT_S <= pair[0]
                        <= start + duration_s + PRESENT_CONTEXT_S]
        derived = derive_present_frames(ticks, trail_s=self._present_trail_s,
                                        edge_pairs=window_pairs)
        derived_walls = [wall for wall, _frame in derived]
        edge_walls = [wall for wall, _frame in pairs]

        def from_presents(shown_at: float | None) -> int | None:
            if shown_at is None or not derived:
                return None
            slot = bisect_right(derived_walls, shown_at) - 1
            if slot < 0 or shown_at - derived_walls[slot] > PRESENT_HOLD_S:
                return None
            return derived[slot][1]

        def from_edges(ran_at: float) -> int | None:
            slot = bisect_right(edge_walls, ran_at) - 1
            return pairs[slot][1] if slot >= 0 else None

        lag_frames = round(lag_s * GAME_FPS)
        sources: set[str] = set()
        out: list[int | None] = []
        if feeds and feeds[0][0] <= start and feeds[-1][0] >= start:
            at = 0
            current: tuple[float, int | None, float | None] | None = None
            for index in range(slot_count):
                wall = start + (index + 0.5) / fps + self._map_wall_bias_s
                while at < len(feeds) and feeds[at][0] <= wall:
                    current = feeds[at]
                    at += 1
                frame: int | None = None
                if current is not None:
                    _feed_wall, tag_frame, capture_ts = current
                    frame = from_presents(capture_ts)
                    if frame is not None:
                        sources.add("presents")
                    elif tag_frame is not None:
                        frame = tag_frame - lag_frames
                        sources.add("feeds")
                if frame is None:
                    frame = from_edges(wall - lag_s)
                    if frame is not None:
                        sources.add("edges")
                out.append(frame)
            return (out, _source_label(sources)) if sources else None
        # No feed series (the in-process encoder path, or no recorder):
        # presents by slot wall time where they reach, the v1 edge rule
        # everywhere else.
        if not pairs and not derived:
            return None
        if pairs and not derived and (
                start + duration_s - lag_s < pairs[0][0]
                or start - lag_s > pairs[-1][0]):
            return None
        for index in range(slot_count):
            wall = start + (index + 0.5) / fps + self._map_wall_bias_s
            frame = from_presents(wall)
            if frame is not None:
                sources.add("presents")
            else:
                frame = from_edges(wall - lag_s)
                if frame is not None:
                    sources.add("edges")
            out.append(frame)
        return (out, _source_label(sources)) if sources else None
