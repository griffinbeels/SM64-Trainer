"""Return encoder key ownership before disposing its isolated process.

Only the stopped media owner calls this. No new submissions or channel accesses
are allowed; replies for already admitted work are consumed, never published as
complete footage. Controller validation proves native Close custody separately
from process disposal. Its independent watchdog remains the fallback.
"""

import time


def close_encoder(controller, *, timeout, poll_s):
    deadline = time.monotonic() + timeout
    closing = None
    while time.monotonic() < deadline:
        status = controller.status()
        if status["fault"]:
            return False
        reply = controller.take_result()
        if reply is not None:
            metadata = reply.metadata
            if metadata["error"] or metadata["worker_disposal_required"]:
                return False
            if reply.request_id == closing:
                if metadata["result"] == 0:
                    return True  # Controller validated zero held/pending masks and exact returns.
                if metadata["result"] != 10:
                    return False
                closing = None  # native Close pending: poll closure again, never resubmit a picture
            continue
        if status["done"]:
            return False
        if closing is None and not status["pending_count"]:
            request = controller.enqueue({"op": "Close"})
            if type(request) is int:
                closing = request
        time.sleep(poll_s)
    return False
