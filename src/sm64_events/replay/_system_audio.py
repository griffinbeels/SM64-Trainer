"""Real-time-safe body of SystemAudioSource (split for readability).

THE RULE (learned the hard way): the PortAudio callback must do nothing but
timestamp and hand off. PortAudio's input buffer is ~21 ms deep; the old
callback took the recorder lock (contended by 60 fps video encode) and every
2 s ran a 384 KB chunk flush + ring eviction INSIDE the callback — each
stall past the buffer depth silently DROPPED packets (measured: writer
received ~45.2k of the device's exactly-48k samples/s; a tone roundtrip
through the idle path was lossless, convicting the callback work). Lost
samples compressed the timeline; the idle-gap guard then punched silence
into the deficit — the "distorted, layered, wrong-speed" audio was entirely
self-inflicted.

Now: callback = bytes copy + qpc stamp + put_nowait. A consumer thread does
everything else (parse, idle-gap fill, writer call — the writer's disk
flush happens on this thread, where stalls cost nothing). The guard's gap
decision uses the qpc captured IN the callback, so consumer lag can never
masquerade as device idle."""
import logging
import queue
import threading

import numpy as np

log = logging.getLogger("sm64.replay")


def plan_placement(packet_start: int, placed: int, n_samples: int,
                   min_fill: int) -> tuple[int, bool]:
    """Where does a packet go on the wall-locked timeline?
    Returns (silence_fill_before, drop_packet). Small timing jitter
    (|gap| < min_fill) appends in sequence — the device clock is exact, so
    cumulative order is right and drift stays bounded by min_fill until the
    next real gap re-anchors it. Engine naps >= min_fill become exact
    silence. A packet entirely behind the placed position is stale (clock
    anomaly) and dropped. Pure — unit-tested."""
    gap = packet_start - placed
    if gap >= min_fill:
        return gap, False
    if gap <= -n_samples:
        return 0, True
    return 0, False


class AudioPump:
    """Queue between the PortAudio callback and the consumer that feeds the
    recorder. Drop-and-count if the consumer wedges (>5 s of backlog) —
    blocking the callback is the one unforgivable failure."""

    def __init__(self, rate: int, on_pcm, epoch_qpc_100ns: int):
        self._rate = rate
        self._on_pcm = on_pcm
        self._epoch = epoch_qpc_100ns
        self._placed = 0                  # samples written to the writer
        self._min_fill = rate // 4        # naps >= 250 ms become silence
        self._q: queue.Queue = queue.Queue(maxsize=256)
        self._dropped = 0
        self._overflows = 0
        self._thread = threading.Thread(
            target=self._consume, name="audio-pump", daemon=True)
        self._thread.start()

    # -- callback side (real-time: no locks, no allocation beyond the copy) --
    def feed(self, in_data: bytes, qpc_ts: int, status: int) -> None:
        if status:
            self._overflows += 1  # PortAudio flagged over/underflow
        try:
            self._q.put_nowait((bytes(in_data), qpc_ts))
        except queue.Full:
            self._dropped += 1

    # -- consumer side --------------------------------------------------------
    def _consume(self) -> None:
        last_report = 0.0
        import time as _time
        while True:
            item = self._q.get()
            if item is None:
                return
            raw, ts = item
            data = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2)
            # the packet ENDS at its callback time; it STARTS n samples back
            packet_start = (int((ts - self._epoch) / 1e7 * self._rate)
                            - len(data))
            fill, drop = plan_placement(packet_start, self._placed,
                                        len(data), self._min_fill)
            if drop:
                continue
            if fill:
                log.info("audio idle gap: %.2f s silence placed (engine "
                         "idle)", fill / self._rate)
                self._on_pcm(np.zeros((fill, 2), dtype=np.int16))
                self._placed += fill
            self._on_pcm(data)
            self._placed += len(data)
            now = _time.monotonic()
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
