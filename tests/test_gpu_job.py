import ctypes as C
from types import SimpleNamespace
import threading
import pytest
from sm64_events.replay.gpuprocess.process_job import OwnedJob


class Kernel:
    def __init__(self):
        self.active = 1
        self.query_ok = True
        self.size_ok = True
        self.close_ok = True
        self.close_calls = []
        self.terminate_calls = []

    def QueryInformationJobObject(self, handle, kind, info, size, returned):
        assert handle == 11 and kind == 1 and size == 48
        if not self.query_ok:
            C.set_last_error(5)
            return 0
        info._obj.active_processes = self.active
        info._obj.total_processes = 3
        returned._obj.value = size if self.size_ok else 0
        return 1

    def CloseHandle(self, handle):
        self.close_calls.append(handle)
        return int(self.close_ok)

    def TerminateJobObject(self, handle, code):
        self.terminate_calls.append((handle, code))
        return 1


def job():
    j = OwnedJob.__new__(OwnedJob)
    j.lock = threading.Lock()
    j.k = Kernel()
    j.handle = 11
    j.child = SimpleNamespace(poll=lambda: 0)
    j.closed = False
    j.spawning = False
    j.assigned = True
    j.empty = False
    j.active = None
    j.total = 0
    j.termination_requested = False
    j.error = None
    return j


def test_launcher_exit_does_not_prove_job_empty():
    j = job()
    j.stop()
    assert not j.snapshot()["empty"] and not j.k.close_calls
    j.k.active = 0
    assert j.snapshot()["empty"] and j.k.close_calls == [11]
    j.stop()
    assert len(j.k.terminate_calls) == 1 and j.snapshot()["handle_closed"]


@pytest.mark.parametrize(
    "mode", ["query-fail", "short", "spawning", "launcher-running"]
)
def test_uncertain_or_racing_disposal_never_closes_proof_handle(mode):
    j = job()
    j.k.active = 0
    j.stop()
    if mode == "query-fail":
        j.k.query_ok = False
    elif mode == "short":
        j.k.size_ok = False
    elif mode == "spawning":
        j.spawning = True
    else:
        j.child.poll = lambda: None
    state = j.snapshot()
    assert not state["empty"] and not j.k.close_calls
    if mode in ["query-fail", "short"]:
        assert state["active_processes"] is None and state["error"]


def test_failed_close_keeps_handle_and_reports_incomplete_disposal():
    j = job()
    j.k.active = 0
    j.k.close_ok = False
    j.stop()
    state = j.snapshot()
    assert state["empty"] and not state["handle_closed"] and state["error"]
    assert j.handle == 11
    j.k.close_ok = True
    assert j.snapshot()["handle_closed"]
