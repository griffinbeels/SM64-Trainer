"""Bounded worker diagnostics; never called by renderer or audio callbacks."""

import time


# Native control.h / gpu_delivery.h reasons, including their distinct namespaces.
# Preserve raw numbers in callers: an older/newer DLL can have an unknown code.
_CONTROL = {
    0: "no cause supplied", 1: "no backend", 2: "owner gone",
    3: "lease expired", 4: "control protocol error", 5: "invalid GPU request",
    6: "backend admission failed", 7: "fresh request required",
    8: "native request invalid", 9: "native resources exhausted",
    10: "native admission system error",
}
_DELIVERY = dict(enumerate((
    "cancelled", "bad source", "bad request", "context unavailable",
    "context changed", "GL unsupported", "snapshot failed", "sample failed",
    "bridge failed", "channel failed", "source gap (legacy, branch unknown)",
    "owner gone", "deadline", "quarantined resources", "stamp table failed",
    "counter exhausted", "source image missing", "source record invalid",
    "source stamp count mismatch", "source stamp length mismatch",
    "source rejected by client", "source epoch changed", "source admission refused",
), 10000))


def native_reason(reason, *, channel=False):
    """Decode only known namespaces; channel Result 6 is not control reason 6."""
    if channel:
        namespace, code = reason & 0xF0000000, reason & 0x0FFFFFFF
        if namespace == 0x10000000:
            return f"channel result {code}"
        if namespace != 0x20000000:
            return "no cause supplied" if reason == 0 else "unknown channel reason"
        reason = code
    return _DELIVERY.get(reason, _CONTROL.get(reason, "unknown native reason"))


def failure_snapshot(exc, *, frontier, adapter, output):
    """Capture worker-owned counters before teardown drains or clears queues.

    This is the instant the server observes the fault, not necessarily the
    earlier native refusal. Status reads perform no channel calls or GPU work.
    An unavailable diagnostic must never hide the original capture exception.
    """
    result = dict(observed_unix_s=time.time(), frontier=frontier,
                  error=str(exc)[:512])
    for name in ("state", "reason"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            result[f"channel_{name}"] = value
    for name, component in (("adapter", adapter), ("publication", output)):
        if component is None:
            continue
        try:
            result[name] = component.status()
        except Exception as status_error:  # noqa: BLE001 - diagnostic cannot replace capture failure.
            result[name] = {"unavailable": str(status_error)[:256]}
    return result


class CaptureTimings:
    """Fixed stage aggregates, retained on failure instead of per-frame logs.

    Scheduling/poll gaps are measured separately from executing media work.
    Storage is constant in capture duration; no raw pictures/packets are kept.
    """

    def __init__(self, clock=time.monotonic, wall_clock=time.time):
        self.clock = clock
        self.utc_offset = wall_clock() - clock()
        self.stages = {name: [0, 0.0, 0.0, 0.0] for name in (
            "channel", "encoder", "sink_prepare", "first_offer", "media_setup", "tick", "between_ticks",
            "audio_drain", "adapter_pump", "heartbeat", "media_drain", "media_age", "report",
            "mux_video", "mux_audio", "ledger_feed",
        )}
        self.last_tick_end = None

    def measure(self, name, operation, *args, **kwargs):
        start = self.clock()
        if name == "tick" and self.last_tick_end is not None:
            self._record("between_ticks", start - self.last_tick_end, self.last_tick_end)
        try:
            return operation(*args, **kwargs)
        finally:
            end = self.clock()
            self._record(name, end - start, start)
            if name == "tick":
                self.last_tick_end = end

    def _record(self, name, elapsed, start):
        row = self.stages[name]
        row[0] += 1
        row[1] += elapsed
        if row[0] == 1 or elapsed > row[2]:
            row[2], row[3] = elapsed, start

    def summary(self):
        return {name: dict(count=n, total_ms=round(total * 1000, 3),
                           max_ms=round(peak * 1000, 3),
                           max_started_monotonic_s=round(start, 6),
                           max_started_unix_s=round(start + self.utc_offset, 6))
                for name, (n, total, peak, start) in self.stages.items() if n}
