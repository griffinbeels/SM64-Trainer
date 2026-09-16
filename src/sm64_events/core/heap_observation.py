"""Coordinate heap snapshots with disposal of resources they can retain.

GC observations hold strong references, including SQLite's internal statements.
Resource owners acquire this guard before their own operation lock when closing.
Only cooperating observers are covered; arbitrary external heap lists are not.
"""
import gc
import threading
from contextlib import contextmanager

heap_lifetime_lock = threading.Lock()


@contextmanager
def heap_snapshot():
    """Borrow the tracked-object list until exit; never retain its elements."""
    with heap_lifetime_lock:
        objects = gc.get_objects()
        try:
            yield objects
        finally:
            # Clear aliases too, including a consumer's retained traceback.
            objects.clear()
