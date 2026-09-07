# src/sm64_events/inputs/track.py
"""An attempt's own input, resolved out of the chunk store.

By UTC first and frame second, never by attempt id: attempts are re-derived
from the journal on every reprojection, so an id is not a durable key. Frame
numbers are not one either — the counter restarts on a console reset, so they
repeat within a session — which is why the wall-clock span picks the chunks
and the frame number only trims inside them.
"""
from typing import NamedTuple

from sm64_events.inputs.document import encode
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.runs import capture_axis
from sm64_events.memory import addresses as A


# How far past the closing event the track may run looking for the grab.
# The close sits within a frame or two of it (measured 2026-08-22 on three of
# his attempts: the star_collected frame was the first star-dance frame, or
# the one before it), so this is a guard against a chunk's tail, not a
# search window.
GRAB_SEARCH_FRAMES = 120


# How far outside the attempt's own wall clock the CHUNK search reaches.
# A chunk is up to ten seconds of capture, so an attempt's first or last
# frames routinely sit inside a neighbouring one whose window does not
# overlap the attempt at all -- his 0'14"70 success (attempt 2317) closed at
# frame 14893115 with the query returning nothing past 14893080, and the 35
# frames it wanted were sitting in the next chunk. The frame-number trim
# below is what actually bounds the track; this only makes sure the frames
# are in the room to be trimmed.
CHUNK_REACH_S = 20.0


def _epochs(chunks):
    """Keep chunk membership while separating observed counter/session seams."""
    epochs = []
    previous = None
    owner = None
    source = None
    for chunk in chunks:
        observed = getattr(chunk, "observations", None)
        chunk_source = observed[0].source_id if observed else None
        for number, frame in chunk.frames:
            if (previous is None or chunk.session_id != owner or number <= previous
                    or chunk_source != source):
                epochs.append(([], set()))
            frames, ids = epochs[-1]
            frames.append((number, frame))
            ids.add(chunk.id)
            previous, owner = number, chunk.session_id
            source = chunk_source
    return epochs


def _observed_frames(chunk, started_utc, ended_utc):
    observations = getattr(chunk, "observations", None)
    if observations is None:
        return chunk.frames  # legacy chunks have no per-frame timestamp
    # Read instants are membership evidence, never a continuous time span.
    return [frame for frame, observation in zip(chunk.frames, observations, strict=True)
            if observation.within(started_utc, ended_utc)]


def _frames_around(store, attempt) -> list[tuple[int, InputFrame]]:
    """Widen only the occurrence selected by the attempt's original chunks.

    New capture selects by actual observation time; legacy chunks retain their
    emission bounds. Preserve chunk/source IDs through widening rather than
    finding the first epoch with the same counters. Never interpolate times.
    """
    from datetime import datetime, timedelta

    def shift(stamp: str, seconds: float) -> str:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return (moment + timedelta(seconds=seconds)).isoformat()

    owner = getattr(attempt, "session_id", None)
    tight = store.chunks_between(attempt.started_utc, attempt.ended_utc, owner)
    selected = {chunk.id: _observed_frames(chunk, attempt.started_utc, attempt.ended_utc)
                for chunk in tight}
    if attempt.anchor_frame is None or not tight:
        return [frame for frames in selected.values() for frame in frames]
    wide = store.chunks_between(shift(attempt.started_utc, -CHUNK_REACH_S),
                               shift(attempt.ended_utc, CHUNK_REACH_S), owner)
    tight_ids = {chunk_id for chunk_id, frames in selected.items() if frames}
    candidates = [frames for frames, ids in _epochs(wide) if ids & tight_ids]
    if len(candidates) > 1:
        # Disjoint counters can eliminate an unrelated epoch; repeated ranges
        # cannot. An absent boundary sample may sit in a hole, so intersect
        # the resolved IGT/dance interval rather than requiring an exact sample.
        candidates = [frames for frames in candidates
                      if _overlaps_attempt(frames, attempt)]
    if len(candidates) > 1:
        raise ValueError("input capture is ambiguous across repeated frame counters")
    return candidates[0] if candidates else []


class CaptureTrack(NamedTuple):
    frames: list[tuple[int, InputFrame]]
    origin: int | None
    frame_count: int
    lead_frames: int


def _dance_start(frames: list[tuple[int, InputFrame]], close: int) -> int | None:
    """The FIRST frame of the star dance that ends this attempt (None: no
    dance follows the close -- a reset, an abandon).

    His rule (2026-08-22) was always "stop only AFTER mario enters the star
    grab", and the end anchors the whole track: the start is derived from
    it as `last - (igt - 1)`. The first version took the first grab-action
    frame at or AFTER `close` (our anchor plus our RTA) -- but the dance
    routinely begins BEFORE that, since our clock and Usamune's disagree by
    a frame or two and the anchor itself can land late. Measured on four of
    his attempts: the dance started 7, 8 and 25 frames before the chosen
    frame, and every one of those pushed the run's own frame 0 the same
    distance late. On the 25 he could see it -- the fall after his reset
    read as "before the reset" while Usamune's timer, paused mid-fall, said
    0'00"20 (2026-08-31).

    So the dance is found as a contiguous RUN of grab-action frames and the
    run's own first frame is the answer. A run is the attempt's own when it
    contains `close` or starts within `GRAB_SEARCH_FRAMES` after it; an
    earlier star's dance sits far outside that window and cannot be picked.
    No such run: the close stands, which is what a reset or an abandon
    gets.
    """
    runs: list[list[tuple[int, int]]] = []
    for number, frame in frames:
        if frame.action not in A.STAR_GRAB_ACTIONS:
            continue
        if runs and number == runs[-1][-1][0] + 1:
            runs[-1].append((number, frame.action))
        else:
            runs.append([(number, frame.action)])

    def dance_of(run):
        # THE TIMER RUNS THROUGH THE FALL (2026-09-01, attempt 5534): a
        # midair grab falls (ACT_FALL_AFTER_STAR_GRAB) for a few frames
        # before the dance begins, and Usamune keeps counting until the
        # DANCE. His 0'19"20 = 576 frames reached from the spawn frame
        # exactly to the last fall frame; counting from the fall's first
        # frame would have started the run four frames before the reset
        # was even pressed. So the dance is the first dance ACTION of the
        # run; a run the capture cut short before the dance began ends
        # where the capture did.
        for number, action in run:
            if action in A.STAR_DANCE_ACTIONS:
                return number
        return run[-1][0] + 1

    for run in runs:
        if run[0][0] <= close <= run[-1][0]:
            return dance_of(run)
    for run in runs:
        if close < run[0][0] <= close + GRAB_SEARCH_FRAMES:
            return dance_of(run)
    return None


def track_for_attempt(store, attempt,
                      span: tuple[int, int] | None = None
                      ) -> list[tuple[int, InputFrame]]:
    frames, _lead = track_with_lead(store, attempt, span)
    return frames


def _attempt_bounds(frames, attempt) -> tuple[int, int]:
    """One interval for selecting an occurrence and trimming its samples."""
    first = attempt.anchor_frame
    close = (first + attempt.rta_frames if attempt.rta_frames is not None
             else frames[-1][0])
    # The dance's first frame is untimed; the preceding frame ends the run.
    dance = _dance_start(frames, close)
    last = dance - 1 if dance is not None else close
    if getattr(attempt, "igt_frames", None):
        first = last - (attempt.igt_frames - 1)
    return first, last


def _overlaps_attempt(frames, attempt) -> bool:
    first, last = _attempt_bounds(frames, attempt)
    return frames[-1][0] >= first and frames[0][0] <= last


def resolve_track(store, attempt, span: tuple[int, int] | None = None
                  ) -> CaptureTrack:
    """Every captured frame from the attempt's anchor THROUGH the grab.

    The wall-clock span picks CHUNKS, and a chunk is ten seconds of capture
    that overlaps the span -- so it carries frames from before the anchor
    and after the close. Both ends are trimmed on the frame counter. The
    end trim was missing until 2026-08-22, and his first real attempt read
    598 frames for a 444-frame run: the extra 154 were the next five seconds
    of the chunk the star landed in, drawn as if they were the run.

    The end is the first frame whose action is a star grab, when one follows
    the close within GRAB_SEARCH_FRAMES -- his rule, the same day: "It should
    stop only AFTER mario enters the star grab. Once mario is in star grab,
    none of the players inputs matter, so that's where it should stop." The
    closing event's frame can sit one frame BEFORE the grab action, and a
    track that ends there ends on a frame where the inputs still mattered.
    An attempt with no grab after it (a reset, an abandon) ends at the close.

    THE LENGTH IS THE ATTEMPT'S OWN TIME (2026-08-28). The span used to run
    from our anchor, so its length was OUR `rta_frames` while the row above
    it showed Usamune's `igt_frames` -- two clocks that disagree by a frame
    or two on nearly every run. Measured over 90 of his successes: the span
    sat exactly one frame off the RTA on 82 of them, and -1 to +2 off the
    IGT. That is the whole of "inputs shows 13\"50, but the attempt was
    clearly 13\"56". His ruling: "It should be IDENTICAL in length. The PB
    timing should exactly match the input display." Usamune's number is the
    authoritative one -- it is what he is graded on -- so the track is cut
    to it: the END stays the grab (his earlier rule, untouched) and the
    START moves so the span is exactly the attempt's own time. Where that
    reaches back past our anchor the frames are real capture and belong to
    the run by Usamune's clock, which started before we saw the counter
    move; where it reaches forward, it trims frames outside the graded time.
    """
    frames = _frames_around(store, attempt)
    if not frames or attempt.anchor_frame is None:
        axis = capture_axis(frames)
        return CaptureTrack(frames, None, axis[-1][0] + 1 if axis else 0, 0)
    first, last = _attempt_bounds(frames, attempt)
    # THE CLIP'S OWN WINDOW (round 32 item 53, 2026-08-31). `span` is the
    # range of game frames the CLIP shows -- its pre-pad, the attempt, its
    # post-pad -- so the timeline "visibly matches the actual contents of
    # the video shown" and every part of it points at footage that exists.
    # It replaced reaching back to the level entry (item 51), which on his
    # BBH clip put 418 frames of un-clickable timeline in front of a video
    # that only carries three seconds of run-up. Everything from `first` to
    # the grab is still the attempt itself, so frame numbering and the
    # PB-identical length are untouched -- the buffers draw as negative
    # frames before it and as frames past its end after it.
    low, high = (min(span[0], first), max(span[1], last)) if span else (first, last)
    kept = [(number, frame) for number, frame in frames if low <= number <= high]
    return CaptureTrack(kept, low, max(0, high - low + 1), first - low)


def track_with_lead(store, attempt, span: tuple[int, int] | None = None
                    ) -> tuple[list[tuple[int, InputFrame]], int]:
    track = resolve_track(store, attempt, span)
    return track.frames, track.lead_frames


def target_of(attempt) -> str:
    """What the attempt was practicing, as a document header writes it."""
    if attempt.segment_id is not None:
        return f"segment {attempt.segment_id}"
    if attempt.course_id is not None and attempt.star_id is not None:
        return f"star {attempt.course_id} {attempt.star_id}"
    return "unknown"


def document_for_attempt(store, attempt, version: str = "us",
                         author: str | None = None) -> str:
    track = resolve_track(store, attempt)
    return encode(track.frames, first_frame=track.origin, frame_count=track.frame_count,
                  target=target_of(attempt), strategy=attempt.strat_tag,
                  version=version, origin=f"attempt {attempt.id}", author=author)
