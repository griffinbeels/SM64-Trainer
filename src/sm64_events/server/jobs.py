"""One board for the background jobs a page POLLS -- the column export and
the sheet import so far.

A wait he can see is a defect, and the honest answer to a wait is to narrate
it: start the work on a thread, hand the client a `job_id`, and let it poll
`{state, progress, message}` until `state` leaves `running`. That shape is
`compare/service.py`'s, and `scorecard_api.py` carried its own copy of the
registry + thread + status GET for the column export (round 26). The sheet
import needed the same thing in round 29 -- his words: "we should have a
similar progress bar, like the one we made for the copy sheet column button.
I want to see my progress as it's happening, otherwise it feels laggy and
unresponsive" -- and a third copy is where the rule says extract, so both
routers hold a `JobBoard` now. `compare/service.py` keeps its own: it is a
service class whose jobs also write rows, a different shape.

`work(step)` runs on the board's thread. It calls `step(fraction, sentence)`
at its REAL boundaries -- never a timer wearing a measurement's clothes -- and
returns `(result, closing_sentence)`. Anything it raises ends the job in
`error` with `str(err)` as the message, so a work function phrases its own
failures for the person waiting ("could not read the sheet: ...").

Bounded: a session that copies or imports all day must not grow the map
forever, and a finished job nobody polled is of no use to anyone -- one
client polls one job to completion and never looks again. The cap is a leak
guard, not a cache.
"""
import logging
import threading
import uuid
from collections.abc import Callable

_log = logging.getLogger("sm64.jobs")

Step = Callable[[float, str], None]


class JobBoard:
    def __init__(self, keep: int = 8):
        self._jobs: dict[str, dict] = {}
        self._keep = keep

    def start(self, name: str, work: Callable[[Step], tuple[object, str]]) -> str:
        """Run `work` on a daemon thread named `name`; return its job id at
        once. The status dict exists before this returns, so a poll that
        races the thread's first step still finds `running`."""
        job_id = uuid.uuid4().hex
        for stale in list(self._jobs)[:-self._keep]:
            self._jobs.pop(stale, None)
        job = {"state": "running", "progress": 0.0,
               "message": "Starting…", "result": None}
        self._jobs[job_id] = job

        def step(fraction: float, message: str) -> None:
            job["progress"] = fraction
            job["message"] = message

        def run() -> None:
            try:
                result, closing = work(step)
            except Exception as err:                    # noqa: BLE001
                _log.warning("%s job failed: %r", name, err)
                job["state"] = "error"
                job["message"] = str(err)
                return
            job["result"] = result
            job["progress"] = 1.0
            job["message"] = closing
            job["state"] = "done"

        threading.Thread(target=run, name=name, daemon=True).start()
        return job_id

    def status(self, job_id: str) -> dict | None:
        """A shallow copy of the job's status, or None for an id this board
        never issued (or has since dropped)."""
        job = self._jobs.get(job_id)
        return dict(job) if job is not None else None
