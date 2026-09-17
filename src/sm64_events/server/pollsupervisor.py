"""Restart failed polling from a fresh observation boundary, never an old event batch."""
import asyncio
import logging
from time import monotonic

log = logging.getLogger("sm64.poller")


class PollSupervisor:
    RETRY_INITIAL_S = 0.25
    RETRY_MAX_S = 8.0
    HEALTHY_RESET_S = 30.0

    def __init__(self, poller):
        self.poller = poller
        self.state = "starting"
        self.error = None
        self.failures = 0
        self.restarts = 0
        self.retry_at = 0.0
        self.child = None

    def health(self):
        state = self.state
        # A newly created task is not evidence that memory/detectors work.
        if state == "resuming" and self.poller.latest is not None:
            state = "running"
        return {"state": state, "error": self.error if state != "running" else None,
                "last_error": self.error, "failures": self.failures,
                "restarts": self.restarts,
                "retry_in_s": round(max(0.0, self.retry_at - monotonic()), 3)}

    async def run(self):
        delay = self.RETRY_INITIAL_S
        while True:
            started = monotonic()
            try:
                if self.failures:
                    await self.poller.prepare_recovery(self.error)
                    self.restarts += 1
                self.retry_at = 0.0
                self.state = "resuming" if self.failures else "running"
                self.child = asyncio.create_task(self.poller.run(), name="game-poll")
                await self.child
                raise RuntimeError("poll loop stopped unexpectedly")
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                error = "poll loop was cancelled unexpectedly"
                log.exception(error)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:512]
                log.exception("poll loop failed; recovering from a fresh read")
            self.failures += 1
            self.error = error
            self.state = "recovering"
            # Clear stale course immediately, including the backoff interval.
            self.poller.latest = None
            if monotonic() - started >= self.HEALTHY_RESET_S:
                delay = self.RETRY_INITIAL_S
            self.retry_at = monotonic() + delay
            await asyncio.sleep(delay)
            delay = min(self.RETRY_MAX_S, delay * 2)


async def maintain_components(service, poller):
    """Cold retries also run while Mario is idle/offline; failures stay contained."""
    failures = {}
    while True:
        await asyncio.sleep(0.5)
        # The same boundary protects late session attachment. Recovery may
        # await broadcasts, so event-loop serialization alone is insufficient.
        async with poller._tick_lock:
            await _maintain_once(service, poller, failures)


async def _maintain_once(service, poller, failures):
    operations = (("tracking", getattr(service, "recover_tracking", None)),
                  ("inputs", getattr(poller, "retry_inputs", None)))
    for name, operation in operations:
        if operation is None:
            continue
        try:
            result = operation()
            if name == "tracking":
                await result
            failures.pop(name, None)
        except Exception as exc:
            now = monotonic()
            message = f"{type(exc).__name__}: {exc}"[:512]
            prior, logged = failures.get(name, (None, 0))
            if message != prior or now - logged >= 30:
                log.exception("%s recovery remains unavailable", name)
                failures[name] = (message, now)
