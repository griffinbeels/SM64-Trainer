"""Admission and real process boundaries, without running five expensive suites."""
import json
import os
import subprocess
import sys
import time
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


@pytest.mark.parametrize("obs,workers,cpus", [(False, 8, 20), (True, 4, 8)])
def test_each_run_gets_half_the_machine_budget_and_obs_has_a_lower_ceiling(obs, workers, cpus):
    """Two merge checks may run at once; together they fill the old
    single-run budget (16 workers normal, 8 with OBS) and never exceed it."""
    actual, mask = resources.budget(list(range(32)), obs)
    assert (actual, len(mask)) == (workers, cpus)
    assert actual * resources.SLOTS == resources.MACHINE_WORKERS[obs]
    assert resources.budget(list(range(32)), obs, 100)[0] == workers
    assert resources.budget(list(range(32)), obs, 0)[0] == 0


def test_a_dedicated_ci_machine_keeps_every_cpu_and_a_worker_per_two():
    """A GitHub runner has no desktop to protect. A browser test is a page, a
    server and Chromium's own processes, so a worker gets two of its CPUs."""
    workers, mask = resources.budget(list(range(4)), False, dedicated=True)
    assert (workers, mask) == (2, [0, 1, 2, 3])
    assert resources.budget(list(range(4)), False, 1, dedicated=True)[0] == 1


def test_slot_zero_is_the_lock_every_older_runner_takes():
    """An older worktree's runner knows one lock file. Slot 0 must be it, or a
    new merge check and an old full suite would never exclude each other."""
    paths = resources.slot_paths()
    assert paths[0] == resources.LOCK_PATH
    assert len(paths) == resources.SLOTS == 2 and len(set(paths)) == 2


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


HOLDER = '''
import sys, time
from pathlib import Path
from sm64_events.storage.instance_lock import acquire_instance_lock
handle = acquire_instance_lock(Path(sys.argv[1]))
print("held" if handle else "busy", flush=True)
time.sleep(60)
'''

CONTENDER = '''
import json, sys, time, subprocess
from pathlib import Path
import psutil
from tools import test_resources
test_resources.competing_runs = lambda _: []
from sm64_events.core.childproc import quiet_spawn_kwargs
admit = sys.argv[3] == "merge"
with test_resources.TestResources(path=Path(sys.argv[1]), admit=admit) as lease:
    child = subprocess.run([sys.executable, "-c", "import psutil; print(psutil.Process().cpu_affinity())"],
                           capture_output=True, text=True, check=True, **quiet_spawn_kwargs())
    Path(sys.argv[2]).write_text(json.dumps([time.time(), lease.slot, psutil.Process().cpu_affinity(),
                                             json.loads(child.stdout)]))
'''


@pytest.fixture
def checkout_env():
    # Child scripts need this checkout, not the editable main install.
    previous = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "src"), previous])
    yield
    os.environ["PYTHONPATH"] = previous


def _wait_for(path, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return json.loads(path.read_text())
        time.sleep(0.1)
    return None


def test_both_slots_held_a_focused_run_proceeds_and_a_third_merge_check_waits(tmp_path, checkout_env):
    """Real processes, the whole admission rule: two holders take both slots;
    a focused run starts at once anyway; a third merge check announces it is
    queued and starts nothing until a slot frees; then it runs, inheriting its
    CPU mask into the process it spawns."""
    holder_script, contender_script = tmp_path / "holder.py", tmp_path / "contender.py"
    holder_script.write_text(HOLDER, encoding="utf-8")
    contender_script.write_text(CONTENDER, encoding="utf-8")
    lock = tmp_path / "budget.lock"
    holders = [_spawn(holder_script, slot) for slot in resources.slot_paths(lock)]
    others = []
    try:
        assert [holder.stdout.readline().strip() for holder in holders] == ["held", "held"]
        focused, third = tmp_path / "focused.json", tmp_path / "third.json"
        others.append(_spawn(contender_script, lock, third, "merge"))
        others.append(_spawn(contender_script, lock, focused, "focused"))
        started = _wait_for(focused, 30)
        assert started is not None, "a focused run must never wait for a merge-check slot"
        assert started[1] is None, "a focused run holds no slot"
        assert _wait_for(third, 3) is None, "a third merge check started while both slots were held"
        holders[1].kill()
        holders[1].wait()
        admitted = _wait_for(third, 30)
        assert admitted is not None and admitted[1] == 1, admitted
        assert admitted[2] == admitted[3], "the spawned child must inherit the admitted CPU mask"
        output, _ = others[0].communicate(timeout=30)
        assert others[0].returncode == 0, output
        assert "queued" in output, output
    finally:
        for process in [*holders, *others]:
            if process.poll() is None:
                process.kill()
            process.wait()


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
