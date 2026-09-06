"""Containment proof: a grandchild cannot outlive its test runner."""
import os
from pathlib import Path
import subprocess
import sys

import psutil

from sm64_events.core.childproc import quiet_spawn_kwargs
from tools.test_job import TestJob


def test_job_closes_over_the_grandchild_even_after_its_parent_exits(tmp_path):
    script = tmp_path / "spawn.py"
    script.write_text('''
import subprocess, sys
from sm64_events.core.childproc import quiet_spawn_kwargs
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **quiet_spawn_kwargs())
print(child.pid, flush=True)
''', encoding="utf-8")
    with TestJob([sys.executable, str(script)], tmp_path) as child:
        grandchild = psutil.Process(int(child.stdout.readline()))
        assert child.wait(timeout=10) == 0
        assert grandchild.is_running()
    grandchild.wait(timeout=5)
    assert not grandchild.is_running()


def test_killing_runner_kills_only_its_job(tmp_path):
    script = tmp_path / "runner.py"
    root = Path(__file__).resolve().parents[1]
    script.write_text('''
import sys, time
from tools.test_job import TestJob
with TestJob([sys.executable, "-c", "import time; time.sleep(30)"], ".") as child:
    print(child.pid, flush=True)
    time.sleep(30)
''', encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root), str(root / "src")])}
    outsider = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                **quiet_spawn_kwargs())
    runner = subprocess.Popen([sys.executable, str(script)], cwd=root, env=env,
                              stdout=subprocess.PIPE, text=True, **quiet_spawn_kwargs())
    try:
        owned = psutil.Process(int(runner.stdout.readline()))
        runner.kill()
        runner.wait(timeout=5)
        owned.wait(timeout=5)
        assert not owned.is_running()
        assert outsider.poll() is None
    finally:
        if runner.poll() is None:
            runner.kill()
        runner.wait()
        outsider.kill()
        outsider.wait()
