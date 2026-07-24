"""Pure planner for a failure compilation (spec 2026-07-23).

Given one entity's attempts plus what footage is reachable right now, decide
WHICH clips go into the compilation and IN WHAT ORDER — no ffmpeg, no
filesystem — so the whole selection/ordering contract is unit-tested on plain
data. The builder (replay/compilation.py) turns the plan into a video.

Ordering (spec §3.2): failures play in the order they'd occur during a run —
by elapsed real time from the run's start anchor (ended_utc - started_utc), a
metric defined for every failure type (unlike IGT, which resets/deaths often
leave None). The finale is the fastest available successful run, in full, last.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

FAILURE_OUTCOMES = frozenset({"reset", "hard_reset", "abandoned", "death"})


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass(frozen=True)
class EntityRef:
    """Which practiced thing to compile. Star sets course_id+star_id (segment
    None); segment sets segment_id. matches() mirrors projection's attempt
    identity."""
    course_id: int | None = None
    star_id: int | None = None
    segment_id: int | None = None

    def matches(self, a) -> bool:
        if self.segment_id is not None:
            return a.segment_id == self.segment_id
        return (a.segment_id is None and a.course_id == self.course_id
                and a.star_id == self.star_id)


@dataclass(frozen=True)
class ClipSpec:
    attempt_id: int
    kind: str                     # "failure" | "finale"
    source: str                   # "ring" | "saved"
    span_start: datetime | None   # None for a saved finale (use the whole file)
    span_end: datetime | None
    time_frames: int | None = None   # finale only: displayed time for the summary


@dataclass(frozen=True)
class CompilationPlan:
    specs: list                   # ordered ClipSpec; finale (if any) is last
    failure_count: int            # included failures (excludes aged-out)
    aged_out: int                 # failures with no footage in the ring
    no_finale: bool
    finale_frames: int | None


def _time_of(a) -> int | None:
    return a.igt_frames if a.igt_frames is not None else a.rta_frames


def _elapsed_s(a) -> float:
    return (_parse_utc(a.ended_utc) - _parse_utc(a.started_utc)).total_seconds()


def _covered(coverage, start: datetime, end: datetime) -> bool:
    """Ring outer envelope contains [start, end]. Interior coverage holes are
    handled at extract time (the builder drops a window that fails to cut)."""
    if coverage is None:
        return False
    cov_start, cov_end = coverage
    return cov_start <= start and end <= cov_end


def plan_compilation(attempts, coverage, saved_ids, identity: EntityRef,
                     x_before: float, y_after: float,
                     pre_pad: float, post_pad: float) -> CompilationPlan:
    ours = [a for a in attempts if identity.matches(a)]

    failures = [a for a in ours
                if a.outcome in FAILURE_OUTCOMES and not a.cleared]
    specs: list[ClipSpec] = []
    aged_out = 0
    for a in sorted(failures, key=lambda a: (_elapsed_s(a), a.id)):
        end = _parse_utc(a.ended_utc)
        span_start = end - timedelta(seconds=x_before)
        span_end = end + timedelta(seconds=y_after)
        if _covered(coverage, span_start, span_end):
            specs.append(ClipSpec(attempt_id=a.id, kind="failure",
                                  source="ring", span_start=span_start,
                                  span_end=span_end))
        else:
            aged_out += 1

    finale = None
    successes = [a for a in ours
                 if a.outcome == "success" and not a.cleared
                 and _time_of(a) is not None]
    for a in sorted(successes, key=_time_of):
        full_start = _parse_utc(a.started_utc) - timedelta(seconds=pre_pad)
        full_end = _parse_utc(a.ended_utc) + timedelta(seconds=post_pad)
        if _covered(coverage, full_start, full_end):
            finale = ClipSpec(attempt_id=a.id, kind="finale", source="ring",
                              span_start=full_start, span_end=full_end,
                              time_frames=_time_of(a))
            break
        if a.id in saved_ids:
            finale = ClipSpec(attempt_id=a.id, kind="finale", source="saved",
                              span_start=None, span_end=None,
                              time_frames=_time_of(a))
            break

    ordered = list(specs)
    if finale is not None:
        ordered.append(finale)
    return CompilationPlan(specs=ordered, failure_count=len(specs),
                           aged_out=aged_out, no_finale=finale is None,
                           finale_frames=finale.time_frames if finale else None)
