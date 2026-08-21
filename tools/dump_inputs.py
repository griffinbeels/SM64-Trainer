# tools/dump_inputs.py
"""READ BACK what you just played, as an input document.

The end-to-end proof for input capture: memory -> sampler -> store ->
document, in one command. Play one attempt, run this, and the last thing you
did comes back as text.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from what_happened import (candidate_journals, describe_age,  # noqa: E402
                           label_for, newest_event, open_readonly)

from sm64_events.inputs.store import decode_runs  # noqa: E402
from sm64_events.inputs.document import encode  # noqa: E402


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
        "SELECT id, started_utc, ended_utc, outcome, igt_frames, strat_tag,"
        " course_id, star_id, segment_id, anchor_frame"
        " FROM attempts ORDER BY started_utc DESC LIMIT ?", (limit,)
    ).fetchall()


def frames_for(conn: sqlite3.Connection, row: sqlite3.Row) -> list:
    chunks = conn.execute(
        "SELECT runs FROM input_chunks"
        " WHERE started_utc <= ? AND ended_utc >= ?"
        " ORDER BY started_utc, id", (row["ended_utc"], row["started_utc"])
    ).fetchall()
    out = []
    for chunk in chunks:
        out.extend(decode_runs(chunk["runs"]))
    if row["anchor_frame"] is None:
        return out
    return [(number, frame) for number, frame in out
            if number >= row["anchor_frame"]]


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
            "SELECT id, started_utc, ended_utc, outcome, igt_frames, strat_tag,"
            " course_id, star_id, segment_id, anchor_frame"
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
    text = encode(frames, target=target_of(row), strategy=row["strat_tag"],
                  version="us", origin=f"attempt {row['id']}")
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out} — {len(frames)} frames")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
