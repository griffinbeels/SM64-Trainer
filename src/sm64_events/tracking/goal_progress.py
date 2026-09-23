"""Progress toward a REPEAT goal: "sub 1'55" 10x", or 10 in a row.

A pinned Scorecard goal may ask for a threshold more than once. Progress
counts the attempts on that entry in the CURRENT session since the pin was
set, so each new sitting re-arms last sitting's goal while the pin itself
persists. Pure: the caller hands in the session's attempts.

Every attempt is judged one of three ways:

- it COUNTS: a successful, uncleared finish at or under the goal, on the
  entry's own clock in displayed centiseconds (the unit `goal_cs` uses);
- it is IGNORED: cleared, abandoned, imported, forgiven by the player, or a
  reset right at the start (`EARLY_RESET_CS`);
- anything else was a real try that missed -- a slower finish, a death, a
  later reset -- and BREAKS a streak.
"""
from datetime import datetime

from sm64_events.ranks.classify import display_cs

# A reset this early on the entry's clock is setup or a distraction, not a
# try, so it neither counts nor breaks a streak. His words: "we shouldn't count
# resets that are RIGHT at the beginning of the attempt, because the player
# might have just been distracted". 3 s is a starting value, not a measurement.
EARLY_RESET_CS = 300

_COUNTS, _IGNORED, _BREAKS = "counts", "ignored", "breaks"


def entry_key_of(attempt) -> str | None:
    """The Scorecard entity key an attempt belongs to, or None."""
    if attempt.segment_id is not None:
        return f"segment:{attempt.segment_id}"
    if attempt.course_id is not None and attempt.star_id is not None:
        return f"star:{attempt.course_id}:{attempt.star_id}"
    return None


def parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _judge(attempt, clock: str, goal_cs: int | None, forgiven) -> str:
    if (attempt.cleared or attempt.outcome == "abandoned"
            or attempt.timed_by == "imported" or attempt.id in forgiven):
        return _IGNORED
    frames = attempt.igt_frames if clock == "igt" else attempt.rta_frames
    cs = display_cs(frames) if frames is not None else None
    if attempt.outcome in ("reset", "hard_reset") and (cs is None or cs < EARLY_RESET_CS):
        return _IGNORED
    if (attempt.outcome == "success" and cs is not None
            and goal_cs is not None and cs <= goal_cs):
        return _COUNTS
    return _BREAKS


def goal_progress(attempts, *, clock: str, goal_cs: int | None, count: int,
                  streak: bool, since: datetime, forgiven=frozenset()) -> dict:
    """`attempts` are ONE entry's, chronological; only those started at or
    after `since` (the later of the session's start and the pin's set_at)
    count. Total mode: `done` = attempts that count. Streak mode: `done` = the
    run since the last break, `best` = the longest run, `broken_by` = the
    most recent breaking attempt's id."""
    total = run = best_run = 0
    broken_by = None
    for attempt in attempts:
        if parse_utc(attempt.started_utc) < since:
            continue
        verdict = _judge(attempt, clock, goal_cs, forgiven)
        if verdict == _COUNTS:
            total += 1
            run += 1
            best_run = max(best_run, run)
        elif verdict == _BREAKS:
            run = 0
            broken_by = attempt.id
    done, best = (run, best_run) if streak else (total, total)
    return {"done": done, "needed": count, "best": best,
            "broken_by": broken_by if streak else None,
            "window_start": since.isoformat().replace("+00:00", "Z")}
