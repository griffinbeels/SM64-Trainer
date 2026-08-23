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


def track_for_attempt(store, attempt) -> list[tuple[int, InputFrame]]:
    """Every captured frame from the attempt's anchor to its closing event.

    The wall-clock span picks CHUNKS, and a chunk is ten seconds of capture
    that overlaps the span -- so it carries frames from before the anchor
    and after the close. Both ends are trimmed on the frame counter. The
    end trim was missing until 2026-08-22, and his first real attempt read
    598 frames for a 444-frame run: the extra 154 were the next five seconds
    of the chunk the star landed in, drawn as if they were the run.
    """
    frames = store.frames_between(attempt.started_utc, attempt.ended_utc)
    if attempt.anchor_frame is None:
        return frames
    first = attempt.anchor_frame
    last = (first + attempt.rta_frames if attempt.rta_frames is not None
            else None)
    return [(number, frame) for number, frame in frames
            if number >= first and (last is None or number <= last)]


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
