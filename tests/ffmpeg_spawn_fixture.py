"""Inspect real spawn arguments without starting audio against a fake child."""
import io

from sm64_events.replay.ffmpeg_sink import FfmpegAvSink


def capture_spawn(monkeypatch, config, codec="h264_nvenc"):
    captured = {}

    class FakeProcess:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()

        def wait(self, timeout=None):
            return 0

    def popen(args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return FakeProcess()

    with monkeypatch.context() as patches:
        patches.setattr("sm64_events.replay.ffmpeg_sink.subprocess.Popen", popen)
        patches.setattr("sm64_events.replay.ffmpeg_sink._assign_kill_on_close", lambda proc: None)
        # These cases inspect argv. A fake child cannot connect to a real pipe
        # or drain a real mux, whose idle audio workers otherwise outlive them.
        for method in ("_open_audio_pipe", "_open_mux", "_audio_writer_loop", "_audio_mux_loop"):
            patches.setattr(FfmpegAvSink, method, lambda *args: None)
        sink = FfmpegAvSink(config, lambda segment: None, ffmpeg="ffmpeg", codec=codec)
        workers = []
        try:
            sink._spawn(320, 240)
            workers = [*sink._readers, sink._audio_thread]
        finally:
            sink.stop()
        assert all(worker is None or not worker.is_alive() for worker in workers)
    return captured
