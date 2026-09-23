"""One recorder-owned native source and its pose archive lifetime."""
from sm64_events.ghost.archive import PoseArchive


class PoseRecording:
    def __init__(self, path, factory, *, producer_pid, allowed):
        # The archive keeps its own two-hour window whatever the video keeps.
        self.archive = PoseArchive(path)
        self.source = None
        self._factory, self._pid = factory, producer_pid
        self._allowed, self._accepting = allowed, False

    def start(self):
        self.source = self._factory(on_batch=self._batch, producer_pid=self._pid)
        self._accepting = True
        self.source.start()

    def _batch(self, producer, frames):
        # A retired callback only retains this owner's archive, never a newer
        # one. An already-entered callback finishes before the source joins.
        if self._accepting and self._allowed():
            self.archive.ingest(producer, frames)

    def request_stop(self):
        self._accepting = False

    def stop(self):
        self.request_stop()
        if self.source is not None and not self.source.stop(timeout_s=3):
            raise RuntimeError("native ghost capture cleanup pending")
        self.archive.close()
