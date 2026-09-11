"""Closing the shared connection must not interrupt a materializing read."""
import sqlite3
import threading

from sm64_events.storage.db import Database


def test_close_waits_until_an_in_flight_read_has_materialized(tmp_path):
    db = Database(tmp_path / "shutdown.db")
    raw = db._conn
    read_started, release_read = threading.Event(), threading.Event()
    close_started, closed = threading.Event(), threading.Event()
    outcomes = []

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchall(self):
            read_started.set()
            assert release_read.wait(5)
            return self.cursor.fetchall()

    class Connection:
        def execute(self, *args):
            return Cursor(raw.execute(*args))

        def close(self):
            raw.close()
            closed.set()
            close_started.set()

    class Lock:
        def __enter__(self):
            if threading.current_thread().name == "database-close":
                close_started.set()
            gate.acquire()

        def __exit__(self, *_args):
            gate.release()

    gate = threading.Lock()
    db._conn, db._lock = Connection(), Lock()

    def read():
        try:
            outcomes.append(db.events())
        except sqlite3.Error as error:
            outcomes.append(error)

    reader = threading.Thread(target=read)
    closer = threading.Thread(target=db.close, name="database-close")
    try:
        reader.start()
        assert read_started.wait(5)
        closer.start()
        assert close_started.wait(5)
        assert not closed.is_set(), "connection closed while its read still owned it"
    finally:
        release_read.set()
        reader.join(5)
        if closer.ident is not None:
            closer.join(5)
        raw.close()
    assert not reader.is_alive() and not closer.is_alive()
    assert outcomes == [[]]
    assert closed.is_set()
