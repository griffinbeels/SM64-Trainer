"""GET /api/daily-star and POST /api/daily-star/seen -- the Daily Star.

`ranks/dailystar.py` decides WHICH entry a date deals; this answers the rest
for one install: its label and the install's own entity key for it (a Bowser
fight is `segment:<id>` here, a seed key in the deck), your saved PB, and
today's best.

The browser sends its own local DATE. The day boundary is the browser's
midnight, which is his ("if they had it open when we shifted from 11:59pm to
12:00am"); the server only turns that date into UTC bounds, on the same
machine and so in the same timezone.

Today's best is the fastest successful, uncleared attempt on the entry that
ENDED inside today, on the entry's Scorecard clock, whatever the strategy. It
reads attempts rather than the saved-PB table because a PB is a deliberate
save and "what was your best time achieved TODAY" must exist without one. An
imported time is excluded: it was typed in today, not played today.

`seen` is the date whose announcement the user dismissed, kept server-side so
the desktop window and a browser tab announce each day once between them.
"""
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sm64_events.ranks.dailystar import daily_entry
from sm64_events.ranks.scorecard import template_rows
from sm64_events.tracking.views import fastest_current_pbs

SEEN_KEY = "daily_star_seen"


class SeenBody(BaseModel):
    date: str


def _parse_day(value: str | None) -> date:
    if not value:
        return datetime.now().astimezone().date()
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise HTTPException(400, f"not a date: {value!r}") from err


def _local_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day + timedelta(days=1), time.min).astimezone()
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _utc(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _resolve(db, deck_key: str) -> tuple[str | None, str, str]:
    """(entity key on this install, label, clock). A fight whose seeded
    segment this install lacks resolves to no entity key: it can still be
    announced, but nothing can be practised or timed against it."""
    if deck_key.startswith("seg:bowser-"):
        for row in db.segment_defs():
            if row.get("seed_key") == deck_key:
                return f"segment:{row['id']}", row["name"], "rta"
        return None, f"Bowser {deck_key.rsplit('-', 1)[1]}", "rta"
    for row in template_rows():
        for key, label, clock in row["entries"]:
            if key == deck_key:
                return key, label, clock
    raise LookupError(f"{deck_key} is not a Scorecard cell")


def _identity(entity_key: str):
    kind, _, rest = entity_key.partition(":")
    if kind == "segment":
        return None, None, int(rest)
    course_id, star_id = rest.split(":")
    return int(course_id), int(star_id), None


def _frames(attempt, clock: str) -> int | None:
    frames = attempt.igt_frames if clock == "igt" else attempt.rta_frames
    return frames if frames else None


def todays_best(attempts, entity_key: str, clock: str, day: date) -> dict | None:
    course_id, star_id, segment_id = _identity(entity_key)
    start, end = _local_bounds_utc(day)
    best, count = None, 0
    for attempt in attempts:
        if (attempt.course_id, attempt.star_id, attempt.segment_id) != \
                (course_id, star_id, segment_id):
            continue
        ended = _utc(attempt.ended_utc)
        if ended is None or not start <= ended < end \
                or attempt.timed_by == "imported":
            continue
        count += 1
        if attempt.outcome != "success" or attempt.cleared:
            continue
        frames = _frames(attempt, clock)
        if frames is not None and (best is None or frames < best["frames"]):
            best = {"frames": frames, "strat": attempt.strat_tag}
    if best is None and count == 0:
        return None
    return {**(best or {"frames": None, "strat": None}), "attempts": count}


def create_dailystar_router(service) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _db():
        if service.db is None:
            raise HTTPException(503, "daily star unavailable")
        return service.db

    @router.get("/daily-star")
    def get_daily_star(date: str | None = None):
        db = _db()
        day = _parse_day(date)
        entry = daily_entry(day)
        entity_key, label, clock = _resolve(db, entry.key)
        pb = today = None
        if entity_key is not None:
            course_id, star_id, segment_id = _identity(entity_key)
            row = fastest_current_pbs(db.pbs()).get(
                (course_id, star_id, segment_id, clock))
            pb = row["frames"] if row else None
            today = todays_best(db.attempts(), entity_key, clock, day)
        return {"date": day.isoformat(), "day": entry.day, "key": entry.key,
                "entity_key": entity_key, "label": label,
                "course_name": entry.course_name, "clock": clock,
                "pb_frames": pb, "today": today,
                "seen": db.get_state(SEEN_KEY, None) == day.isoformat()}

    @router.post("/daily-star/seen")
    def post_seen(body: SeenBody):
        db = _db()
        db.set_state(SEEN_KEY, _parse_day(body.date).isoformat())
        return {"seen": body.date}

    return router
