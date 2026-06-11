"""Pure attempt projection: journal events in -> attempts out. (Projector
arrives in the projection task; this slice defines the Attempt row shape.)"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Attempt:
    id: int                    # journal id of the attempt's first event
    session_id: int
    course_id: int | None      # None = failure with no declared target yet
    star_id: int | None
    strat_tag: str | None
    anchor_type: str           # practice_reset | state_loaded | none
    anchor_frame: int | None
    outcome: str               # success | reset | hard_reset | abandoned
    outcome_detail: str | None
    igt_frames: int | None
    rta_frames: int | None
    started_utc: str
    ended_utc: str
    cleared: bool
    cleared_reason: str | None
