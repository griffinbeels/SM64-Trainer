"""Real-time-safe body of SystemAudioSource (split for readability).

THE RULE (learned the hard way): the PortAudio callback must do nothing but
hand off. PortAudio's input buffer is ~21 ms deep; any work in the callback
that stalls past that depth silently DROPS packets (measured: a previous
in-callback chunk flush lost ~6% of samples). So: callback = bytes copy +
put_nowait; a consumer thread does the rest.

The pump is now a PURE HANDOFF — it forwards each delivered packet's PCM to the
recorder and tracks loudness for the deaf-stream watchdog. It does NOT place
samples on a wall-locked timeline or inject silence for idle gaps anymore: the
single ffmpeg mux owns the clock (`-use_wallclock_as_timestamps` stamps each
packet by arrival; `aresample=async` fills idle gaps with silence to match the
video master). The old sample-count placement existed only for the retired
count-based PCM-sidecar writer; keeping it here would dump a silence burst into
the pipe at every idle resume and fight aresample.
"""
import logging
import queue
import threading
import time

import numpy as np

log = logging.getLogger("sm64.replay")


class AudioPump:
    """Queue between the PortAudio callback and the consumer that forwards PCM
    to the recorder. Drop-and-count if the consumer wedges (>5 s of backlog) —
    blocking the callback is the one unforgivable failure."""

    def __init__(self, rate: int, on_pcm):
        self._rate = rate
        self._on_pcm = on_pcm
        self._q: queue.Queue = queue.Queue(maxsize=256)
        self._dropped = 0
        self._overflows = 0
        self.last_loud_t = 0.0   # monotonic time of last non-silent packet
        self._thread = threading.Thread(
            target=self._consume, name="audio-pump", daemon=True)
        self._thread.start()

    # -- callback side (real-time: no locks, no allocation beyond the copy) --
    def feed(self, in_data: bytes, status: int) -> None:
        if status:
            self._overflows += 1  # PortAudio flagged over/underflow
        try:
            self._q.put_nowait(bytes(in_data))
        except queue.Full:
            self._dropped += 1

    # -- consumer side --------------------------------------------------------
    def _consume(self) -> None:
        last_report = 0.0
        while True:
            raw = self._q.get()
            if raw is None:
                return
            data = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2)
            if len(data) and int(np.abs(data[::16]).max()) > 50:
                self.last_loud_t = time.monotonic()
            self._on_pcm(data)
            now = time.monotonic()
            if now - last_report > 30:
                if self._overflows or self._dropped:
                    log.warning("audio pump: %d device overflows, %d queue "
                                "drops in last 30s", self._overflows,
                                self._dropped)
                self._overflows = self._dropped = 0
                last_report = now

    def stop(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=5)
