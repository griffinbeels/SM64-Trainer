"""Drain media operations before replacing their recording/identity lifetime."""
from contextlib import contextmanager
from threading import Condition


class SessionGate:
    def __init__(self):
        self._condition = Condition()
        self._active = 0
        self._changing = False
        self._closed = False

    @contextmanager
    def use(self):
        with self._condition:
            self._condition.wait_for(lambda: not self._changing)
            if self._closed:
                raise RuntimeError("replay session is closed")
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    @contextmanager
    def change(self, *, close=False):
        with self._condition:
            self._condition.wait_for(lambda: not self._changing)
            self._changing = True
            self._condition.wait_for(lambda: self._active == 0)
        try:
            yield
        finally:
            with self._condition:
                self._closed = close
                self._changing = False
                self._condition.notify_all()
