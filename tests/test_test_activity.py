"""Old test runners cannot hide behind a different checkout or harness name."""
from types import SimpleNamespace

import pytest

from tools import test_activity as activity


@pytest.fixture(autouse=True)
def _nothing_remembered(monkeypatch):
    """What competing_runs remembers per process belongs to one machine's
    processes; each test's fakes are a machine of their own."""
    monkeypatch.setattr(activity, "_SEEN", {})
    monkeypatch.setattr(activity, "_ANCESTRY", {})


class Process:
    def __init__(self, pid, argv=(), parents=(), born=10.0, name="python.exe"):
        self.pid, self.argv, self._parents, self.born = pid, argv, parents, born
        self.info = {"name": name}

    def parents(self):
        return [SimpleNamespace(pid=pid) for pid in self._parents]

    def cmdline(self):
        return list(self.argv)

    def create_time(self):
        return self.born

    def cwd(self):
        return f"checkout-{self.pid}"


def test_legacy_tree_is_reported_once_but_registered_waiters_and_own_children_are_not(tmp_path, monkeypatch):
    current = Process(1, parents=[90])
    processes = [Process(2, ["python", "tools/run_tests.py"]),
                 Process(3, ["python", "-m", "pytest"], parents=[2]),
                 Process(4, ["python", "-m", "pytest"], parents=[40, 90]),
                 Process(5, ["python", "-m", "pytest"], parents=[1]),
                 Process(6, ["python", "ordinary_script.py"]),
                 Process(40, ["uv", "run", "python", "tools/run_tests.py"], parents=[90], name="uv.exe")]
    by_pid = {p.pid: p for p in [current, *processes]}
    (tmp_path / "4-10.0.runner").touch()
    monkeypatch.setattr(activity.psutil, "Process", lambda pid=None: by_pid[pid or 1])
    monkeypatch.setattr(activity.psutil, "process_iter", lambda attrs: processes)
    assert activity.competing_runs(tmp_path) == [{"pid": 2, "started": 10.0, "checkout": "checkout-2"}]


def test_recycled_pid_does_not_make_an_old_runner_cooperative(tmp_path, monkeypatch):
    (tmp_path / "4-9.0.runner").touch()
    current, old = Process(1), Process(4, ["python", "-m", "pytest"], born=10.0)
    monkeypatch.setattr(activity.psutil, "Process", lambda pid=None: old if pid else current)
    monkeypatch.setattr(activity.psutil, "process_iter", lambda attrs: [old])
    assert activity.competing_runs(tmp_path)[0]["pid"] == 4


def test_each_process_is_read_once_and_one_started_later_is_still_found(tmp_path, monkeypatch):
    """The runner's watcher calls this every scan; reading every Python
    process's command line and ancestry each time cost seconds of GIL on a
    busy desktop. A process is read once while it lives; a new one is read
    when it appears."""
    reads = []

    class Counted(Process):
        def cmdline(self):
            reads.append(self.pid)
            return super().cmdline()

    current = Process(1)
    plain, early = Counted(6, ["python", "ordinary_script.py"]), Counted(2, ["python", "-m", "pytest"])
    processes = [plain, early]
    monkeypatch.setattr(activity.psutil, "Process", lambda pid=None: current)
    monkeypatch.setattr(activity.psutil, "process_iter", lambda attrs: list(processes))
    assert [run["pid"] for run in activity.competing_runs(tmp_path)] == [2]
    assert [run["pid"] for run in activity.competing_runs(tmp_path)] == [2]
    assert sorted(reads) == [2, 6], "a second scan read no process again"
    processes.append(Counted(7, ["python", "-m", "pytest"]))
    assert [run["pid"] for run in activity.competing_runs(tmp_path)] == [2, 7]
    assert sorted(reads) == [2, 6, 7]
