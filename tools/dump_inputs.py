# tools/dump_inputs.py
"""READ BACK what you just played, as an input document.

Reads stored observations through the same attempt resolver as the timeline.
This inspects persistence and selection; it cannot prove controller freshness
or that a video picture depicts the input state recorded beside it.

    uv run python tools/dump_inputs.py            # the newest attempt
    uv run python tools/dump_inputs.py --list     # the last few, to pick from
    uv run python tools/dump_inputs.py --attempt 40213
    uv run python tools/dump_inputs.py --out run.sm64inputs

It picks the journal the same way `tools/what_happened.py` does, and NAMES the
one it picked. Three journals exist on this machine -- the repo checkout, each
worktree, and the installed exe -- and reading the wrong one does not error,
it returns days-old play that looks entirely plausible.

Read-only. Safe beside a live session.
"""
import argparse
import sqlite3
import sys
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from what_happened import (candidate_journals, describe_age,  # noqa: E402
                           label_for, newest_event, open_readonly)

from sm64_events.inputs.store import InputStore  # noqa: E402
from sm64_events.inputs.track import document_for_attempt, track_for_attempt  # noqa: E402

_ATTEMPT_COLUMNS = ("id, session_id, started_utc, ended_utc, outcome, igt_frames,"
                    " rta_frames, strat_tag, course_id, star_id, segment_id, anchor_frame")


def freshest_journal() -> Path | None:
    """The journal with the newest event, as what_happened.py chooses it."""
    scored = []
    for path in candidate_journals():
        _count, stamp = newest_event(path)
        if stamp is not None:
            scored.append((stamp, path))
    if not scored:
        return None
    return max(scored)[1]


def attempts(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT " + _ATTEMPT_COLUMNS +
        " FROM attempts ORDER BY started_utc DESC LIMIT ?", (limit,)
    ).fetchall()


def frames_for(conn: sqlite3.Connection, row: sqlite3.Row) -> list:
    return track_for_attempt(InputStore(conn, RLock()), SimpleNamespace(**dict(row)))


def target_of(row: sqlite3.Row) -> str:
    if row["segment_id"] is not None:
        return f"segment {row['segment_id']}"
    if row["course_id"] is not None and row["star_id"] is not None:
        return f"star {row['course_id']} {row['star_id']}"
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, help="attempt id; default newest")
    parser.add_argument("--list", action="store_true",
                        help="show recent attempts and how much input each has")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--out", help="write the document to this file")
    parser.add_argument("--journal",
                        help="read this journal instead of the freshest "
                             "(three exist on this machine)")
    args = parser.parse_args()

    journal = Path(args.journal) if args.journal else freshest_journal()
    if journal is None:
        print("no journal has any events yet")
        return 1
    _count, stamp = newest_event(journal)
    from datetime import datetime, timezone
    print(f"reading {label_for(journal)} ({journal}) — newest event "
          f"{describe_age(stamp, datetime.now(timezone.utc))}\n")
    conn = open_readonly(journal)
    conn.row_factory = sqlite3.Row      # what_happened.py reads tuples
    try:
        conn.execute("SELECT 1 FROM input_chunks LIMIT 1")
    except sqlite3.OperationalError:
        print("this journal has no input_chunks table yet — it predates "
              "input capture, or the server has not restarted since")
        return 2

    rows = attempts(conn, args.limit)
    if not rows:
        print("no attempts recorded")
        return 1
    if args.list:
        for row in rows:
            captured = len(frames_for(conn, row))
            print(f"  #{row['id']:<7} {target_of(row):<14} "
                  f"{row['outcome']:<10} {row['started_utc'][11:19]}  "
                  f"{captured:>5} input frames")
        return 0

    row = rows[0]
    if args.attempt is not None:
        found = conn.execute(
            "SELECT " + _ATTEMPT_COLUMNS +
            " FROM attempts WHERE id=?", (args.attempt,)).fetchone()
        if found is None:
            print(f"no attempt {args.attempt} in this journal")
            return 1
        row = found

    frames = frames_for(conn, row)
    if not frames:
        print(f"attempt #{row['id']} has no captured input.\n"
              "That is a finding, not an error: either it was played before "
              "capture existed, or the sampler was not running.")
        return 3
    text = document_for_attempt(InputStore(conn, RLock()), SimpleNamespace(**dict(row)))
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out} — {len(frames)} frames")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
