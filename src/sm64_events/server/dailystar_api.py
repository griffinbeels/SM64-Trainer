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

Your rank and the next goal are read off the star's OVERALL curve -- what the
star itself grades on across every strategy, the number MARELO aggregates and
the Library's "Overall Rank Standards" prints -- never one strategy's ladder
(his round-2 note: "the rank that should be shown should be the OVERALL rank
for the star, not the strategy rank"). The rank is where your fastest saved PB
sits; the next goal is the time that reaches one division above it (round 1:
"This is likely just the rank above your current time"). With no PB the goal
is the first step above the floor; at the top there is none.

`seen` is the date whose announcement the user dismissed, kept server-side so
the desktop window and a browser tab announce each day once between them.
"""
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sm64_events.ranks.classify import display_cs
from sm64_events.ranks.dailystar import daily_entry
from sm64_events.ranks.scorecard import template_rows
from sm64_events.server.scorecard_standards import tile_strategies
from sm64_events.tracking.activestrat import ActiveStrats
from sm64_events.tracking.views import entity_rank, fastest_current_pbs

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


def _local_midnight(day: date) -> datetime:
    naive = datetime.combine(day, time.min)
    try:
        return naive.astimezone()
    except (OSError, OverflowError, ValueError):
        # Windows' local-time lookup refuses dates far from today; the
        # rotation runs forever, so fall back to today's offset.
        return naive.replace(tzinfo=datetime.now().astimezone().tzinfo)


def _local_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start = _local_midnight(day)
    end = _local_midnight(day + timedelta(days=1))
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
    announced, but nothing can be practiced or timed against it."""
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
    try:
        start, end = _local_bounds_utc(day)
    except OverflowError:       # the calendar's last day: nothing was played
        return None
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


_NO_PB_FRAMES = 10 ** 7    # slower than any curve's floor: "no PB yet"


def standing(progress: dict | None, time_cs: int, has_pb: bool) -> tuple[dict | None, dict | None]:
    """(your rank, next goal) from one graded position on a curve. `progress`
    is `curves.progress_for_time`'s shape; `time_cs` the time it graded."""
    if progress is None:
        return None, None
    rank = ({"tier": progress["tier"], "division": progress["division"]}
            if has_pb else None)
    if progress["next_gap_cs"] is None:
        return rank, None
    return rank, {"cs": round(time_cs - progress["next_gap_cs"]),
                  "tier": progress["next_tier"], "division": progress["next_division"]}


def overall_standing(ranks, entity_key: str, pb_frames: int | None):
    if ranks is None:
        return None, None
    frames = pb_frames or _NO_PB_FRAMES
    return standing(entity_rank(ranks, entity_key, frames), display_cs(frames),
                    pb_frames is not None)


def create_dailystar_router(service, library=None, adoptions=None) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _db():
        if service.db is None:
            raise HTTPException(503, "daily star unavailable")
        return service.db

    def _strategy(entity_key: str, label: str, clock: str) -> str | None:
        """The Scorecard tile's own strategy for this entry: the Library
        page the announcement opens."""
        ranks = service.ranks
        if ranks is None:
            return None
        active = ActiveStrats.from_db(service.db, service.strat_by_star,
                                      service.strat_by_segment)
        return tile_strategies(
            [{"entries": [(entity_key, label, clock)]}], ranks, active,
            library.payload if library is not None else {},
            adoptions.rows() if adoptions is not None else {}).get(entity_key)

    @router.get("/daily-star")
    def get_daily_star(date: str | None = None):
        db = _db()
        day = _parse_day(date)
        entry = daily_entry(day)
        entity_key, label, clock = _resolve(db, entry.key)
        pb = today = goal = rank = strategy = None
        if entity_key is not None:
            course_id, star_id, segment_id = _identity(entity_key)
            row = fastest_current_pbs(db.pbs()).get(
                (course_id, star_id, segment_id, clock))
            pb = row["frames"] if row else None
            today = todays_best(db.attempts(), entity_key, clock, day)
            strategy = _strategy(entity_key, label, clock)
            rank, goal = overall_standing(service.ranks, entity_key, pb)
        return {"date": day.isoformat(), "day": entry.day, "key": entry.key,
                "entity_key": entity_key, "label": label,
                "course_name": entry.course_name, "clock": clock,
                "pb_frames": pb, "pb_rank": rank, "strategy": strategy,
                "today": today, "next_goal": goal,
                "seen": db.get_state(SEEN_KEY, None) == day.isoformat()}

    @router.post("/daily-star/seen")
    def post_seen(body: SeenBody):
        db = _db()
        db.set_state(SEEN_KEY, _parse_day(body.date).isoformat())
        return {"seen": body.date}

    return router
