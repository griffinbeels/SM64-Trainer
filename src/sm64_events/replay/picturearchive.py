"""Scratch identity storage with the same lifetime as retained video.

SQLite pages, rather than Python objects for the whole session, hold old
identities. This is disposable recording scratch: startup removes it with
the footage. Transactions commit on segment completion, not each frame;
the small page cache bounds RAM and deleted pages are reused on disk.
"""
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path


class PictureArchive:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path.resolve()
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._last_source: float | None = None
        self._last_native_id: str | None = None
        self._db.executescript("""
            PRAGMA auto_vacuum=INCREMENTAL;
            PRAGMA cache_size=-2048;
            PRAGMA synchronous=OFF;
            PRAGMA journal_mode=TRUNCATE;
            CREATE TABLE IF NOT EXISTS pictures(ts REAL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS picture_time ON pictures(ts);
            CREATE TABLE IF NOT EXISTS feeds(at REAL, ts REAL, run TEXT, pts INTEGER, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS feed_time ON feeds(at);
            CREATE INDEX IF NOT EXISTS feed_source ON feeds(ts);
            CREATE INDEX IF NOT EXISTS feed_run_pts ON feeds(run, pts);
            CREATE TEMP TABLE removed_sources(ts REAL, native_id TEXT);
        """)
        self._source_columns()

    def _source_columns(self):
        # Scratch from an earlier recorder may remain readable after detach.
        # Migrate once, never scan historical metadata on each new picture.
        for table in ("pictures", "feeds"):
            columns = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
            if "native_id" not in columns:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN native_id TEXT")
                self._db.execute(f"""UPDATE {table} SET native_id = json_extract(data, '$.source_id')
                    WHERE json_type(data, '$.source_id') = 'text'
                    AND length(json_extract(data, '$.source_id')) BETWEEN 1 AND 160""")
        self._db.execute("CREATE INDEX IF NOT EXISTS picture_native ON pictures(native_id, ts)")
        self._db.execute("CREATE INDEX IF NOT EXISTS feed_native ON feeds(native_id, ts)")

    @staticmethod
    def _native_id(value):
        source = value.get("source_id")
        return source if type(source) is str and 0 < len(source) <= 160 else None

    def add_row(self, row: dict) -> None:
        with self._lock:
            self._db.execute("INSERT INTO pictures(ts, data, native_id) VALUES (?, ?, ?)",
                             (row["ts"], json.dumps(row, separators=(",", ":")), self._native_id(row)))

    def add_feed(self, feed: dict) -> None:
        with self._lock:
            self._db.execute("INSERT INTO feeds(at, ts, run, pts, data, native_id) VALUES (?, ?, ?, ?, ?, ?)",
                             (feed["at"], feed["ts"], feed["run_id"], feed["pts"],
                              json.dumps(feed, separators=(",", ":")), self._native_id(feed)))
            source = feed["ts"]
            if source is not None:
                if self._last_source is not None and source > self._last_source:
                    # Observed pictures superseded before the encoder took
                    # them have no feed. Only sweep this newly consumed
                    # interval, never the entire retained session per frame.
                    self._db.execute("""DELETE FROM pictures WHERE ts >= ? AND ts < ?
                        AND CASE WHEN pictures.native_id IS NULL THEN NOT EXISTS
                            (SELECT 1 FROM feeds WHERE feeds.native_id IS NULL AND feeds.ts = pictures.ts)
                        ELSE NOT EXISTS
                            (SELECT 1 FROM feeds WHERE feeds.native_id = pictures.native_id) END""",
                                     (self._last_source, source))
                self._last_source = source
                self._last_native_id = self._native_id(feed)

    def rows_between(self, t0: float, t1: float) -> list[dict]:
        return self._query("SELECT data FROM pictures WHERE ts BETWEEN ? AND ? ORDER BY ts, rowid",
                           t0, t1)

    def feeds_between(self, t0: float, t1: float) -> list[dict]:
        return self._query("SELECT data FROM feeds WHERE at BETWEEN ? AND ? ORDER BY at, rowid",
                           t0, t1)

    def _query(self, sql: str, t0: float, t1: float) -> list[dict]:
        with self._lock:
            if self._db is not None:
                return [json.loads(row[0]) for row in self._db.execute(sql, (t0, t1))]
            if not self._path.exists():
                return []
            # Detached footage remains queryable, without leaving a handle
            # that blocks the next recorder owner's scratch reset on Windows.
            # mode=ro must never recreate a former owner's removed database.
            try:
                with closing(sqlite3.connect(self._path.as_uri() + "?mode=ro", uri=True)) as db:
                    return [json.loads(row[0]) for row in db.execute(sql, (t0, t1))]
            except sqlite3.OperationalError:
                if not self._path.exists():
                    return []
                raise

    def resume(self) -> None:
        """Reopen for the same recorder after it reacquires ownership."""
        with self._lock:
            if self._db is not None:
                return
            self._db = sqlite3.connect(self._path.as_uri() + "?mode=rw", uri=True,
                                       check_same_thread=False)
            self._db.executescript("""
                PRAGMA cache_size=-2048;
                PRAGMA synchronous=OFF;
                PRAGMA journal_mode=TRUNCATE;
                CREATE TEMP TABLE removed_sources(ts REAL, native_id TEXT);
            """)

    def discard_segment(self, seg) -> None:
        """Forget only a video interval explicitly removed from the ring.

        An old encoder's final segment may arrive after a new run's first
        segment. A wall-time cutoff based on current coverage would erase
        that still-pending segment's evidence. Exact removed intervals do not.
        """
        if seg.kind != "video" or seg.media_run is None:
            return
        with self._lock:
            detached = self._db is None
            if detached and not self._path.exists():
                return
            try:
                if detached:
                    self.resume()  # existing, session-specific file only
                self._discard_segment(seg)
            finally:
                if detached:
                    self.close()

    def _discard_segment(self, seg) -> None:
        bounds = (seg.media_run.id, seg.media_run.ticks_at(seg.utc_start.timestamp()),
                  seg.media_run.ticks_at(seg.utc_end.timestamp()))
        self._db.execute("""INSERT INTO removed_sources
            SELECT DISTINCT ts, native_id FROM feeds WHERE run = ? AND pts >= ? AND pts < ? AND ts IS NOT NULL""",
                         bounds)
        self._db.execute("DELETE FROM feeds WHERE run = ? AND pts >= ? AND pts < ?", bounds)
        # A heartbeat may reference a picture composed long before its
        # retained segment. Also retain the last fed picture: the next
        # heartbeat has not been written yet. Newer pending rows survive.
        self._db.execute("""DELETE FROM pictures WHERE rowid IN (
                SELECT p.rowid FROM removed_sources AS r JOIN pictures AS p
                ON p.native_id = r.native_id WHERE r.native_id IS NOT NULL
                UNION
                SELECT p.rowid FROM removed_sources AS r JOIN pictures AS p
                ON p.native_id IS NULL AND p.ts = r.ts WHERE r.native_id IS NULL)
            AND NOT (native_id IS ? AND (native_id IS NOT NULL OR ts IS ?))
            AND CASE WHEN pictures.native_id IS NULL THEN NOT EXISTS
                            (SELECT 1 FROM feeds WHERE feeds.native_id IS NULL AND feeds.ts = pictures.ts)
                        ELSE NOT EXISTS
                            (SELECT 1 FROM feeds WHERE feeds.native_id = pictures.native_id) END""",
                         (self._last_native_id, self._last_source))
        self._db.execute("DELETE FROM removed_sources")
        self._commit()

    def _commit(self) -> None:
        self._db.commit()
        # Incrementally return free pages rather than copying the whole DB
        # with VACUUM on the capture/segment thread.
        self._db.execute("PRAGMA incremental_vacuum(64)")

    def flush(self) -> None:
        with self._lock:
            if self._db is not None:
                self._commit()

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                db = self._db
                self._db = None
                try:
                    db.commit()
                finally:
                    db.close()
