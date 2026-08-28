# src/sm64_events/inputs/track.py
"""An attempt's own input, resolved out of the chunk store.

By UTC first and frame second, never by attempt id: attempts are re-derived
from the journal on every reprojection, so an id is not a durable key. Frame
numbers are not one either — the counter restarts on a console reset, so they
repeat within a session — which is why the wall-clock span picks the chunks
and the frame number only trims inside them.
"""
from sm64_events.inputs.document import encode
from sm64_events.inputs.frame import InputFrame
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


def _frames_around(store, attempt) -> list[tuple[int, InputFrame]]:
    """Captured frames near the attempt, widened past its own wall clock.

    The wall clock picks CHUNKS and the frame counter trims inside them --
    but the counter RESTARTS on a console reset, so numbers repeat within a
    session and a widened window could pull a pre-reset frame carrying a
    number in the same range. So the widened set is cut at any backward
    step to the run holding the attempt's own anchor, which is the same
    epoch rule `capture_axis` follows; with no anchor to aim at, the
    unwidened answer stands.
    """
    from datetime import datetime, timedelta

    def shift(stamp: str, seconds: float) -> str:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return (moment + timedelta(seconds=seconds)).isoformat()

    tight = store.frames_between(attempt.started_utc, attempt.ended_utc)
    if attempt.anchor_frame is None or not tight:
        return tight
    wide = store.frames_between(shift(attempt.started_utc, -CHUNK_REACH_S),
                                shift(attempt.ended_utc, CHUNK_REACH_S))
    if len(wide) <= len(tight):
        return tight
    # Split at every backward step -- each is a console reset, and its two
    # sides are different epochs whose numbers collide -- then keep the run
    # that covers what the attempt's OWN window returned. Choosing by
    # number alone cannot do this: a pre-reset frame can carry a number in
    # the same range, which is the whole reason chunks are picked by clock.
    cuts = [0] + [index for index in range(1, len(wide))
                  if wide[index][0] < wide[index - 1][0]] + [len(wide)]
    low, high = tight[0][0], tight[-1][0]
    for first, last in zip(cuts, cuts[1:]):
        if wide[first][0] <= low and high <= wide[last - 1][0]:
            return wide[first:last]
    return tight


def track_for_attempt(store, attempt) -> list[tuple[int, InputFrame]]:
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
    if attempt.anchor_frame is None:
        return frames
    first = attempt.anchor_frame
    if attempt.rta_frames is None:
        return [(number, frame) for number, frame in frames if number >= first]
    close = first + attempt.rta_frames
    last = close
    for number, frame in frames:
        if (close <= number <= close + GRAB_SEARCH_FRAMES
                and frame.action in A.STAR_GRAB_ACTIONS):
            last = number
            break
    if attempt.igt_frames:
        first = last - (attempt.igt_frames - 1)
    return [(number, frame) for number, frame in frames
            if first <= number <= last]


def target_of(attempt) -> str:
    """What the attempt was practicing, as a document header writes it."""
    if attempt.segment_id is not None:
        return f"segment {attempt.segment_id}"
    if attempt.course_id is not None and attempt.star_id is not None:
        return f"star {attempt.course_id} {attempt.star_id}"
    return "unknown"


def document_for_attempt(store, attempt, version: str = "us") -> str:
    return encode(track_for_attempt(store, attempt),
                  target=target_of(attempt), strategy=attempt.strat_tag,
                  version=version, origin=f"attempt {attempt.id}")
