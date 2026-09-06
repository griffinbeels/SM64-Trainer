"""Admission and real process boundaries, without running five expensive suites."""
import json
import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from tools import test_resources as resources

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_activity(monkeypatch):
    # These exercise tiny locks/masks, not the user's ambient processes. The
    # real outer runner still observes legacy activity throughout this file.
    monkeypatch.setattr(resources, "competing_runs", lambda _: [])


@pytest.mark.parametrize("obs,workers,cpus", [(False, 16, 20), (True, 8, 8)])
def test_budget_is_machine_sized_and_obs_has_a_lower_ceiling(obs, workers, cpus):
    actual, mask = resources.budget(list(range(32)), obs)
    assert (actual, len(mask)) == (workers, cpus)
    assert resources.budget(list(range(32)), obs, 100)[0] == workers
    assert resources.budget(list(range(32)), obs, 0)[0] == 0


def test_budget_respects_an_existing_non_contiguous_affinity_mask():
    eligible = [2, 4, 6, 8, 10, 12, 14, 16]
    workers, mask = resources.budget(eligible, True, reserve=100)
    assert workers == 1 and mask == [2]
    assert resources.budget([7], False) == (1, [7])


def test_a_stale_owner_token_does_not_bypass_admission(monkeypatch):
    monkeypatch.setenv(resources.OWNER_ENV, f"{os.getpid()}:0")
    assert not resources.inherited_owner()


@pytest.mark.parametrize("transports,expected", [([], 0), (["popen", "popen"], 2), (["64*popen"], 64)])
def test_worker_count_uses_effective_transports_including_explicit_tx(transports, expected):
    assert resources.effective_workers(transports) == expected


def test_remote_workers_cannot_bypass_the_local_budget():
    with pytest.raises(ValueError, match="local popen"):
        resources.effective_workers(["ssh=somewhere"])


def test_obs_opening_mid_run_tightens_then_latches_and_restores(tmp_path, monkeypatch):
    process = psutil.Process()
    original = process.cpu_affinity()
    monkeypatch.setattr(resources, "obs_is_open", lambda: False)
    with resources.TestResources(path=tmp_path / "budget.lock") as lease:
        before = process.cpu_affinity()
        monkeypatch.setattr(resources, "obs_is_open", lambda: True)
        lease.refresh()
        assert process.cpu_affinity() == resources.budget(original, True)[1]
        assert set(process.cpu_affinity()) <= set(before)
        monkeypatch.setattr(resources, "obs_is_open", lambda: False)
        lease.refresh()
        assert lease.obs is True
    assert process.cpu_affinity() == original


def _spawn(script, *args):
    return subprocess.Popen([sys.executable, str(script), *map(str, args)], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            **quiet_spawn_kwargs())


def test_five_controllers_queue_before_work_and_inherit_affinity(tmp_path):
    script = tmp_path / "contender.py"
    script.write_text('''
import json, sys, time, subprocess
from pathlib import Path
import psutil
from tools import test_resources
test_resources.competing_runs = lambda _: []
TestResources = test_resources.TestResources
from sm64_events.core.childproc import quiet_spawn_kwargs
with TestResources(path=Path(sys.argv[1])):
    start = time.time()
    child = subprocess.run([sys.executable, "-c", "import psutil; print(psutil.Process().cpu_affinity())"],
                           capture_output=True, text=True, check=True, **quiet_spawn_kwargs())
    time.sleep(0.1)
    Path(sys.argv[2]).write_text(json.dumps([start, time.time(), psutil.Process().cpu_affinity(), json.loads(child.stdout)]))
''', encoding="utf-8")
    outputs = [tmp_path / f"result-{index}.json" for index in range(5)]
    # Child scripts need this checkout, not the editable main install.
    previous = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "src"), previous])
    children = []
    try:
        children = [_spawn(script, tmp_path / "shared.lock", result) for result in outputs]
        logs = []
        for child in children:
            output, _ = child.communicate(timeout=30)
            assert child.returncode == 0, output
            logs.append(output)
    finally:
        os.environ["PYTHONPATH"] = previous
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    intervals = sorted(json.loads(result.read_text()) for result in outputs)
    assert all(left[1] <= right[0] for left, right in zip(intervals, intervals[1:]))
    assert any("queued" in log for log in logs)
    assert all(parent == child for _, _, parent, child in intervals)


def test_a_killed_owner_releases_the_os_lock(tmp_path):
    script = tmp_path / "owner.py"
    script.write_text('''
import sys, time
from pathlib import Path
from sm64_events.storage.instance_lock import acquire_instance_lock
handle = acquire_instance_lock(Path(sys.argv[1]))
print("held" if handle else "busy", flush=True)
time.sleep(30)
''', encoding="utf-8")
    child = _spawn(script, tmp_path / "crash.lock")
    try:
        assert child.stdout.readline().strip() == "held"
    finally:
        child.kill()
        child.wait()
    with resources.TestResources(path=tmp_path / "crash.lock"):
        pass
