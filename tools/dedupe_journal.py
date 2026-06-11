"""Remove double-journaled game events left by concurrent server instances.

Two servers polling the same emulator journal the same physical event twice:
identical (type, frame, payload) within milliseconds, under different
session ids. This tool finds those pairs and (with --fix) deletes the
later copy of each, then re-projects the attempts table from the cleaned
journal.

Scan mode (default) is read-only and safe while a server runs.
--fix REQUIRES the server to be stopped (the tool takes an exclusive
transaction and the running server's in-memory state would be stale).
A timestamped backup copy of the db file is written before any change.

Duplicate rule (deliberately strict): same type+frame+payload AND wall
timestamps within --window-seconds (default 5). GAME events only —
star_collected, practice_reset, state_loaded, death, level_changed,
game_reset. Derived events (attempt_completed etc.) are replay-ignored
and left alone; session_started rows are each server's own (legit).
frame can repeat across console power-ons, which is why the time window
is required.

Usage:
    uv run python tools/dedupe_journal.py [db] [--fix] [--window-seconds N]
"""
import argparse
import json
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Allow importing from src/ when run directly via uv run
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from sm64_events.storage.dedupe import find_duplicates


def _load_rows(db_path: Path, window_seconds: float):
    """Open the db read-only and load GAME event rows.  Returns raw tuples."""
    uri = db_path.resolve().as_uri() + "?mode=ro"  # as_uri() rejects relative paths
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, type, frame, wall_time_utc, payload FROM events ORDER BY id"
    ).fetchall()
    conn.close()
    # Keep raw payload string for matching — don't deserialise yet.
    return [(r["id"], r["type"], r["frame"], r["wall_time_utc"], r["payload"])
            for r in rows]


def _report(all_rows, dup_ids: list[int], window_seconds: float) -> None:
    dup_set = set(dup_ids)
    dup_rows = [r for r in all_rows if r[0] in dup_set]

    print(f"\nTotal journal events : {len(all_rows)}")
    print(f"Duplicates found     : {len(dup_ids)}")
    if not dup_ids:
        print("Journal looks clean — no duplicates detected.")
        return

    by_type: Counter = Counter(r[1] for r in dup_rows)
    print("\nDuplicates by type:")
    for t, n in sorted(by_type.items()):
        print(f"  {t:<25} {n}")

    walls = [r[3] for r in dup_rows]
    print(f"\nAffected time range  : {min(walls)}  ..  {max(walls)}")

    # Star-collected breakdown by (course_id, star_id)
    star_dupes = [(r[0], r[4]) for r in dup_rows if r[1] == "star_collected"]
    if star_dupes:
        star_counter: Counter = Counter()
        for _, payload_json in star_dupes:
            try:
                p = json.loads(payload_json)
                star_counter[(p.get("course_id"), p.get("star_id"))] += 1
            except Exception:
                pass
        print("\nstar_collected dupes by (course_id, star_id):")
        for (c, s), n in sorted(star_counter.items()):
            print(f"  course={c} star={s}  {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db", nargs="?", default="data/tracker.db",
                        help="Path to tracker.db (default: data/tracker.db)")
    parser.add_argument("--fix", action="store_true",
                        help="Delete duplicates and re-project attempts (server must be stopped)")
    parser.add_argument("--window-seconds", type=float, default=5.0,
                        help="Max wall-time gap to consider events duplicate (default: 5.0)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {db_path}  (window={args.window_seconds}s) ...")
    all_rows = _load_rows(db_path, args.window_seconds)
    dup_ids = find_duplicates(all_rows, args.window_seconds)
    _report(all_rows, dup_ids, args.window_seconds)

    if not args.fix:
        print("\nScan only — rerun with --fix after stopping the server.")
        sys.exit(0)

    if not dup_ids:
        print("\nNothing to fix.")
        sys.exit(0)

    # -- fix mode -----------------------------------------------------------
    # 1. Require exclusive lock — abort if the server is still running.
    from sm64_events.storage.instance_lock import acquire_instance_lock
    lock = acquire_instance_lock(db_path.with_suffix(".lock"))
    if lock is None:
        print(
            "\nERROR: server still running (could not acquire instance lock).\n"
            "Stop the server, then rerun with --fix.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Backup the database (and WAL/SHM if present).
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bak = Path(f"{db_path}.bak-{stamp}")
    shutil.copy2(db_path, bak)
    print(f"\nBackup written: {bak}")
    for ext in (".wal", "-wal", ".shm", "-shm"):
        src = db_path.with_suffix(ext) if ext.startswith(".") else Path(str(db_path) + ext)
        if src.exists():
            shutil.copy2(src, Path(str(bak) + ext))
            print(f"Backup written: {bak}{ext}")

    # 3. Open read-write, delete duplicates, re-project.
    from sm64_events.storage.db import Database
    from sm64_events.tracking.projection import replay

    db = Database(db_path)
    db.delete_events(dup_ids)
    print(f"Deleted {len(dup_ids)} duplicate event(s).")

    events = db.events()
    attempts, _ = replay(events)
    db.replace_attempts(attempts)
    print(f"Re-projected {len(attempts)} attempt(s).")
    db.close()

    print("\nDone. Restart the server to load the cleaned state.")


if __name__ == "__main__":
    main()
