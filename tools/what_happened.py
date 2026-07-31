"""Read back what the human just did in game, as a timeline.

WHY THIS EXISTS. The event journal has always held the answer -- ~30 event
types, each with a frame, a UTC wall stamp and a JSON payload -- but nothing
read it back, so "what did you just do" meant improvising SQL, and the quality
of the answer varied with whichever query got written that day.

The failure this is really built to stop is quieter than that. There are THREE
live journals on this machine and they are all valid: the repo checkout, every
worktree, and the installed exe under %LOCALAPPDATA%. Which one a session
writes to depends only on which binary was launched and from where. On
2026-07-31 they held events newest at today, Jul 30 and Jul 28 respectively.
Reading the wrong one does not error -- it returns three-day-old events that
look entirely plausible, and the debugging that follows is confident and wrong.
So this tool picks the freshest journal, says which one it picked, and REFUSES
by default when the newest event is too old to be the session just played.

Usage:
    uv run python tools/what_happened.py                  # last 10 minutes
    uv run python tools/what_happened.py --minutes 45
    uv run python tools/what_happened.py --session         # the whole last session
    uv run python tools/what_happened.py --list            # just show the journals
    uv run python tools/what_happened.py --db <path>       # force one
    uv run python tools/what_happened.py --allow-stale     # debug an old session
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Newest event older than this and the journal is almost certainly not the
# session that was just played. Ten minutes is generous for "I stopped playing,
# alt-tabbed, and described what happened".
STALE_AFTER = timedelta(minutes=10)

# Payload keys worth surfacing inline, in the order they read best. Anything
# else in the payload is summarised rather than dumped.
INTERESTING = (
    "course_id", "star_id", "segment_id", "strat_tag", "level", "area",
    "from", "to", "kind", "frames", "igt", "timer_mode", "reason", "action",
)


def candidate_journals() -> list[Path]:
    """Every journal this machine could plausibly have been writing to.

    Order does not matter -- the freshest wins -- but coverage does, which is
    why worktrees are globbed rather than listed.
    """
    found = [REPO_ROOT / "data" / "tracker.db"]
    found.extend(sorted(REPO_ROOT.glob(".claude/worktrees/*/data/tracker.db")))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        found.append(Path(local_app_data) / "SM64Trainer" / "data" / "tracker.db")
    return [path for path in found if path.exists()]


def open_readonly(path: Path) -> sqlite3.Connection:
    """Read-only, so a live server's journal is never disturbed by an inspection."""
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def newest_event(path: Path) -> tuple[int, datetime | None]:
    try:
        conn = open_readonly(path)
        count, newest = conn.execute(
            "SELECT COUNT(*), MAX(wall_time_utc) FROM events"
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return 0, None
    return count or 0, parse_stamp(newest)


def parse_stamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def describe_age(stamp: datetime | None, now: datetime) -> str:
    if stamp is None:
        return "no events"
    delta = now - stamp
    seconds = delta.total_seconds()
    if seconds < -60:
        # Surfaced rather than smoothed over: a journal stamped in the future
        # means local time is being written into a field labelled UTC, and
        # every age below would be wrong in the same direction.
        return f"{abs(seconds) / 60:.0f}m IN THE FUTURE (clock/timezone bug?)"
    if seconds < 90:
        return f"{seconds:.0f}s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m ago"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h ago"
    return f"{seconds / 86400:.1f}d ago"


def label_for(path: Path) -> str:
    parts = path.parts
    if "worktrees" in parts:
        return f"worktree {parts[parts.index('worktrees') + 1]}"
    if "SM64Trainer" in parts:
        return "installed exe"
    return "repo checkout"


def render_payload(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw[:90]
    if not isinstance(payload, dict):
        return str(payload)[:90]
    shown = [
        f"{key}={payload[key]}"
        for key in INTERESTING
        if key in payload and payload[key] is not None
    ]
    remaining = len(payload) - len(shown)
    if remaining > 0:
        shown.append(f"(+{remaining} more)")
    return " ".join(shown)


def timeline(conn: sqlite3.Connection, since: datetime | None, session_only: bool):
    if session_only:
        row = conn.execute("SELECT MAX(session_id) FROM events").fetchone()
        return conn.execute(
            "SELECT wall_time_utc, frame, type, payload FROM events "
            "WHERE session_id = ? ORDER BY id",
            (row[0],),
        ).fetchall()
    return conn.execute(
        "SELECT wall_time_utc, frame, type, payload FROM events "
        "WHERE wall_time_utc >= ? ORDER BY id",
        (since.isoformat().replace("+00:00", "Z"),),
    ).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=10,
                        help="how far back to read (default 10)")
    parser.add_argument("--session", action="store_true",
                        help="read the whole most recent session instead")
    parser.add_argument("--db", type=Path, help="force a specific journal")
    parser.add_argument("--list", action="store_true",
                        help="show every journal and its freshness, then stop")
    parser.add_argument("--allow-stale", action="store_true",
                        help="read the journal even if it looks stale")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    journals = [args.db] if args.db else candidate_journals()
    if not journals:
        print("No journal found. Has the server ever run?", file=sys.stderr)
        return 2

    surveyed = []
    for path in journals:
        count, newest = newest_event(path)
        surveyed.append((path, count, newest))
    surveyed.sort(key=lambda row: row[2] or datetime.min.replace(tzinfo=timezone.utc),
                  reverse=True)

    print("Journals found:")
    for path, count, newest in surveyed:
        print(f"  {label_for(path):<26} {count:>7} events   "
              f"newest {describe_age(newest, now)}")
    print()
    if args.list:
        return 0

    chosen, count, newest = surveyed[0]
    print(f"Reading: {label_for(chosen)}  ({chosen})")

    if newest is None:
        print("That journal has no events at all.", file=sys.stderr)
        return 2
    age = now - newest
    if age > STALE_AFTER and not args.allow_stale:
        print(
            f"\nREFUSING: the freshest journal's newest event is "
            f"{describe_age(newest, now)}, which is not the session you just "
            f"played.\nYou were probably running a different binary — the "
            f"installed exe writes to %LOCALAPPDATA%, each worktree writes to "
            f"its own data/.\nStart the server you actually played on, or pass "
            f"--allow-stale to read this one anyway.",
            file=sys.stderr,
        )
        return 1

    since = now - timedelta(minutes=args.minutes)
    rows = timeline(open_readonly(chosen), since, args.session)
    if not rows:
        window = "that session" if args.session else f"the last {args.minutes} min"
        print(f"\nNo events in {window}.")
        return 0

    scope = "last session" if args.session else f"last {args.minutes} min"
    print(f"\n--- {len(rows)} events, {scope} ---\n")
    previous = None
    for raw_stamp, frame, kind, payload in rows:
        stamp = parse_stamp(raw_stamp)
        gap = ""
        if previous and stamp:
            seconds = (stamp - previous).total_seconds()
            if seconds >= 2:
                gap = f"   (+{seconds:.0f}s)"
        clock = stamp.strftime("%H:%M:%S") if stamp else "??:??:??"
        print(f"{clock}  f{frame:<7} {kind:<22} {render_payload(payload)}{gap}")
        previous = stamp or previous
    return 0


if __name__ == "__main__":
    sys.exit(main())
