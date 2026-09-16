"""The real process controller validates native custody before successful retirement."""

import pytest

from sm64_events.replay.gpuprocess.retirement import close_encoder
from test_gpu_process import controller, reply, finish, wait


@pytest.mark.parametrize("mode", ["echo", "close-pending", "close-missing-return"])
def test_retirement_drains_admitted_submit_and_validates_native_close(mode):
    c = controller()
    try:
        c.enqueue({"op": "Open", "adapter_luid": [0, 84637], "fixture": mode})
        reply(c)
        c.enqueue(dict(op="Submit", slot=0, bridge_token=77, serial=10, pts=1,
                       encoder_duration=3, force_idr=True))
        # Submit's pending reply/key ownership remains in the real controller.
        closed = close_encoder(c, timeout=3, poll_s=.005)
        assert closed is (mode != "close-missing-return")
        wait(lambda: c.status()["done"])
        if closed:
            assert c.status()["fault"] is None
            assert c.status()["process_exit"] == 0
            assert c.status()["pending_count"] == 0
        else:
            assert "custody" in c.status()["fault"]
    finally:
        finish(c)


def test_retirement_retains_watchdog_for_nonresponsive_helper():
    c = controller(call_timeout=.1)
    try:
        c.enqueue({"op": "Open", "adapter_luid": [0, 84637], "fixture": "echo"})
        reply(c)
        c.enqueue({"op": "Poll", "fixture": "hang"})
        assert not close_encoder(c, timeout=2, poll_s=.005)
        wait(lambda: c.status()["done"])
        assert "deadline" in c.status()["fault"]
    finally:
        finish(c)
