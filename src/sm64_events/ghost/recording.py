"""One recorder-owned native source with an optional pose archive.

The sole NativeCapture owns live publication before its archive callback. A
path of None adds no archive, directory or SQLite resource to this owner.
"""
import threading

from sm64_events.ghost.archive import PoseArchive


class PoseRecording:
    def __init__(self, path, factory, *, producer_pid, allowed, live_source=None, live_input=None):
        # The archive keeps its own two-hour window whatever the video keeps.
        self.archive = PoseArchive(path) if path is not None else None
        self.source = None
        self._factory, self._pid = factory, producer_pid
        self._allowed, self._accepting = allowed, False
        self._control = threading.Lock()
        self._live_session = live_source, live_input
        self._start_attempted = self._starting = self._stopping = False

    def start(self):
        with self._control:
            if self._start_attempted or self._stopping:
                raise RuntimeError("native pose source already started or stopping")
            self._start_attempted = self._starting = True
            live_session = self._live_session
        options = {name: value for name, value in zip(("live_source", "live_input"), live_session, strict=True)
                   if value is not None}
        try:
            source = self._factory(on_batch=self._batch, producer_pid=self._pid, **options)
            with self._control:
                self.source = source  # retain even a partially started owner
                if not self._stopping and any(now is not old for now, old in zip(self._live_session, live_session, strict=True)):
                    self._set_source_live(source, *self._live_session)
                self._accepting = not self._stopping
                stopping = self._stopping
            if stopping:
                self.request_stop()
                return
            source.start()
        finally:
            with self._control:
                self._starting = False

    @staticmethod
    def _set_source_live(source, outgoing, incoming):
        joint = getattr(source, "set_live_session", None)
        if joint is not None:
            joint(outgoing, incoming)
            return
        setter = getattr(source, "set_live_source", None)
        if incoming is not None or setter is None:
            raise RuntimeError("native pose source does not support live publication")
        setter(outgoing)

    def _set_live_session_locked(self, outgoing, incoming):
        self._live_session = outgoing, incoming
        if self.source is not None and not self._stopping:
            self._set_source_live(self.source, outgoing, incoming)

    def set_live_session(self, outgoing=None, incoming=None):
        """Queue the two local endpoints atomically on the current source."""
        with self._control:
            self._set_live_session_locked(outgoing, incoming)

    def set_live_source(self, source):
        """Remember initial demand or forward a change to the sole source.

        The source's setter only queues its pump transition. A retiring owner
        never forwards a new attachment after immediate withdrawal.
        """
        with self._control:
            self._set_live_session_locked(source, self._live_session[1])

    def set_live_input(self, incoming):
        """Compatibility wrapper retaining the requested outgoing endpoint."""
        with self._control:
            self._set_live_session_locked(self._live_session[0], incoming)

    def _batch(self, producer, frames):
        # A retired callback only retains this owner's archive, never a newer
        # one. An already-entered callback finishes before the source joins.
        if self.archive is not None and self._accepting and self._allowed():
            self.archive.ingest(producer, frames)

    def request_stop(self):
        """Retire acceptance and source acquisition before any bounded join."""
        with self._control:
            self._accepting = False
            self._stopping = True
            source = self.source
        request_stop = getattr(source, "request_stop", None)
        if request_stop is not None:
            request_stop()
        else:
            withdraw = getattr(source, "withdraw_live_source", None)
            if withdraw is not None:
                withdraw()

    def stop(self):
        self.request_stop()
        with self._control:
            if self._starting:
                raise RuntimeError("native ghost capture cleanup pending: startup in progress")
        if self.source is not None and not self.source.stop(timeout_s=3):
            details = getattr(self.source, "cleanup_error", None)
            raise RuntimeError("native ghost capture cleanup pending" + (f": {details}" if details else ""))
        if self.archive is not None:
            self.archive.close()
