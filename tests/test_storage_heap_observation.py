"""A heap observer retaining a cursor must not keep a closed DB file open."""
import contextlib
import gc
import sqlite3
import sys
import threading

import pytest

from sm64_events.storage.db import Database


def test_heap_observation_during_wal_setup_does_not_block_migration(tmp_path, monkeypatch):
    observed, connections = [], []
    connect = sqlite3.connect
    path = tmp_path / "observed.db"

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            cursor = super().execute(sql, *args, **kwargs)
            if sql == "PRAGMA journal_mode=WAL":
                observed.extend(gc.get_objects())
            return cursor

    def observed_connect(*args, **kwargs):
        connection = connect(*args, factory=ObservedConnection, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed_connect)
    try:
        db = Database(path)
        assert any(isinstance(obj, sqlite3.Cursor) and obj.connection is db._conn
                   for obj in observed), "the observer missed the constructor cursor"
        db.close()
    finally:
        for obj in observed:
            if isinstance(obj, sqlite3.Cursor) and any(obj.connection is conn for conn in connections):
                with contextlib.suppress(sqlite3.ProgrammingError):
                    obj.close()
        for connection in connections:
            connection.close()
        observed.clear()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows refuses deletion of open database files")
def test_close_waits_for_the_resource_sampler_to_release_sqlite_objects(tmp_path, monkeypatch):
    from sm64_events.core import procmem

    path = tmp_path / "sampled.db"
    db = Database(path)
    db.events()  # Populate the real connection's prepared-statement cache.
    sampling, release, closing, closed = (threading.Event() for _ in range(4))
    outcomes = []
    histogram = procmem.type_histogram

    def held_histogram(objects):
        assert any(type(obj).__module__ == "sqlite3" and type(obj).__name__ == "Statement"
                   for obj in objects), "the observer missed SQLite's cached statements"
        sampling.set()
        assert release.wait(5)
        return histogram(objects)

    def observe():
        outcomes.append(procmem.sample(histogram=True))

    def close():
        closing.set()
        db.close()
        closed.set()

    monkeypatch.setattr(procmem, "type_histogram", held_histogram)
    observer = threading.Thread(target=observe)
    closer = threading.Thread(target=close)
    try:
        observer.start()
        assert sampling.wait(5)
        closer.start()
        assert closing.wait(5)
        assert not closed.wait(.2), "close returned while the sampler still retained SQLite statements"
    finally:
        release.set()
        observer.join(5)
        if closer.ident is not None:
            closer.join(5)
        assert not observer.is_alive() and not closer.is_alive(), "a resource owner failed to stop"
        db.close()
    assert closed.is_set() and outcomes[0]["objects"] > 0
    path.unlink()  # Immediate real handle release; no gc.collect or cleanup retry.
