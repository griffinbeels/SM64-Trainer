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
    """
    frames = store.frames_between(attempt.started_utc, attempt.ended_utc)
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
