"""Bind the entire input pipeline whenever the session database becomes available."""
from sm64_events.inputs.sampler import InputSampler
from sm64_events.inputs.service import InputsService
from sm64_events.inputs.store import ChunkWriter
from sm64_events.inputs.track import track_for_attempt


class InputRuntime:
    def __init__(self, tracker, memory, layout, replay=None):
        self.tracker = tracker
        self.memory = memory
        self.layout = layout
        self.replay = replay
        self.db = None
        self.inputs = None
        self.writer = None
        self.sampler = None
        self.poller = None

    def bind(self, poller):
        self.poller = poller
        poller.on_stop = self.close
        poller.retry_inputs = self.retry
        poller.input_storage_health = self.health
        if self.replay is not None:
            self.replay.track_pads = self.track_pads
        if self.tracker.db is not None:
            self.attach(self.tracker.db)

    def attach(self, db):
        if self.db is db:
            return
        inputs = InputsService(db.inputs, db.input_templates, db.attempts,
                               version=self.layout.version,
                               events=db.events_between, landmark_names=db.landmark_names)
        writer = sampler = None
        if self.layout.player1_controller is not None:
            writer = ChunkWriter(db.inputs, lambda: self.tracker.session_id)
            sampler = InputSampler(
                self.memory, self.layout, writer.add,
                session_id=lambda: self.tracker.session_id,
                on_activity=self.replay.recorder.set_player_active if self.replay else None)
        self.close()
        self.poller.bind_sampler(sampler)
        self.db, self.inputs = db, inputs
        self.writer, self.sampler = writer, sampler
        self.tracker.on_attempt_settled = writer.close if writer else None

    def get_service(self):
        if self.inputs is None:
            raise RuntimeError("Input storage is unavailable; waiting for database recovery")
        return self.inputs

    def track_pads(self, attempt):
        if self.db is None:
            return {}
        track = track_for_attempt(self.db.inputs, attempt)
        return {number: (frame.stick_x, frame.stick_y, frame.buttons)
                for number, frame in track} if track else {}

    def retry(self):
        if self.writer is not None:
            self.writer.retry()
            if self.writer.health()["error"] is None:
                self.tracker.input_flush_error = None

    def health(self):
        return self.writer.health() if self.writer else {"state": "unavailable"}

    def close(self):
        try:
            if self.sampler is not None:
                self.sampler.flush()
        finally:
            if self.writer is not None:
                self.writer.close()
