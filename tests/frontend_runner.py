"""Run a browser-free JS file inside pytest's existing budget and job.

Use tools/run_tests.py so Node and its worker inherit admission, affinity and
crash cleanup. No npm wrapper, watch process, server or browser is started.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tests" / "frontend"
sys.path.insert(0, str(ROOT / "tools"))


def run_frontend(*filenames: str) -> None:
    node = shutil.which("node")
    runner = FRONTEND / "node_modules" / "vitest" / "vitest.mjs"
    assert node and runner.is_file(), (
        "Browser-free UI tests require Node 24.13+ and "
        "`npm ci --prefix tests/frontend`. Missing dependencies are not a pass."
    )
    command = [node, str(runner), "run", *filenames,
               "--config", str(FRONTEND / "vitest.config.mjs")]
    if os.name == "nt":
        from test_job import TestJob

        # A nested job also closes Vitest's worker on timeout or when this
        # individual test fails, rather than waiting for the full suite to end.
        with TestJob(command, ROOT) as child:
            child.stdout.reconfigure(encoding="utf-8")
            output, _ = child.communicate(timeout=60)
            code = child.returncode
    else:
        result = subprocess.run(command, cwd=ROOT, capture_output=True,
                                encoding="utf-8", timeout=60, **quiet_spawn_kwargs())
        output, code = result.stdout + result.stderr, result.returncode
    print(output, end="")
    assert code == 0, output
