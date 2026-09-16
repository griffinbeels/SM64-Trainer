import ctypes as C
from types import SimpleNamespace
import pytest
from sm64_events.replay.gpuprocess.process_resume import (
    SuspendedChildResume,
    ThreadEntry,
)


class Fn:
    def __init__(self, f):
        self.f = f

    def __call__(self, *args):
        return self.f(*args)


class Kernel:
    def __init__(self, mode):
        self.mode = mode
        self.calls = []
        self.closed = []
        self.index = 0
        for name in [
            "CreateToolhelp32Snapshot",
            "Thread32First",
            "Thread32Next",
            "OpenThread",
            "GetProcessId",
            "GetProcessIdOfThread",
            "WaitForSingleObject",
            "ResumeThread",
            "CloseHandle",
        ]:
            setattr(self, name, Fn(lambda *args, n=name: self.call(n, *args)))

    def call(self, name, *args):
        self.calls.append(name)
        if name == "CreateToolhelp32Snapshot":
            return C.c_void_p(-1).value if self.mode == "snapshot-fail" else 100
        if name in ("Thread32First", "Thread32Next"):
            entries = [(10, 1), (20, 77), (30, 2)]
            if self.mode == "none":
                entries = [(10, 1)]
            if self.mode == "ambiguous":
                entries = [(20, 77), (21, 77)]
            if self.index >= len(entries):
                C.set_last_error(18)
                return 0
            p = args[1]._obj
            p.size = 8 if self.mode == "short" else C.sizeof(ThreadEntry)
            p.thread_id, p.owner_pid = entries[self.index]
            self.index += 1
            return 1
        if name == "CloseHandle":
            self.closed.append(args[0])
            return 1
        if name == "OpenThread":
            return 0 if self.mode == "open-fail" else 200
        if name == "GetProcessId":
            return 78 if self.mode == "process-mismatch" else 77
        if name == "GetProcessIdOfThread":
            return 78 if self.mode == "thread-mismatch" else 77
        if name == "WaitForSingleObject":
            assert args[1] == 0
            return 0 if self.mode == "process-ended" else 0x102
        if name == "ResumeThread":
            return {
                "already-running": 0,
                "still-suspended": 2,
                "resume-fail": 0xFFFFFFFF,
            }.get(self.mode, 1)


def test_owned_resume_positive_sequence_and_handles():
    k = Kernel("ok")
    result = SuspendedChildResume(k).resume(SimpleNamespace(pid=77, _handle=555))
    assert result == dict(
        owner_pid=77, thread_id=20, snapshot_entries=3, previous_suspend_count=1
    )
    assert k.closed == [100, 200] and k.calls.index("ResumeThread") > k.calls.index(
        "WaitForSingleObject"
    )


@pytest.mark.parametrize(
    "mode",
    [
        "snapshot-fail",
        "none",
        "ambiguous",
        "short",
        "open-fail",
        "process-mismatch",
        "thread-mismatch",
        "process-ended",
        "already-running",
        "still-suspended",
        "resume-fail",
    ],
)
def test_ambiguous_or_wrong_owned_thread_never_guessed(mode):
    k = Kernel(mode)
    with pytest.raises((OSError, RuntimeError)):
        SuspendedChildResume(k).resume(SimpleNamespace(pid=77, _handle=555))
    if mode not in ["already-running", "still-suspended", "resume-fail"]:
        assert "ResumeThread" not in k.calls
    if "OpenThread" in k.calls and mode != "open-fail":
        assert 200 in k.closed
    if mode != "snapshot-fail":
        assert 100 in k.closed
