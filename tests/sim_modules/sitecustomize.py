"""A test helper for tests/test_sim_boundary.py, never on the app's path: a process started with this folder on
PYTHONPATH and SIM_MODULES_OUT naming a folder writes its command line and every module it imported to
<SIM_MODULES_OUT>/<pid>.json as it exits, so a test reads what a child process really loaded."""
import atexit
import json
import os
import sys

_OUT = os.environ.get("SIM_MODULES_OUT")
if _OUT:
    def _write() -> None:
        path = os.path.join(_OUT, f"{os.getpid()}.json")
        try:
            with open(path + ".part", "w", encoding="utf-8") as handle:
                json.dump({"argv": list(getattr(sys, "orig_argv", sys.argv)), "modules": sorted(sys.modules)}, handle)
            os.replace(path + ".part", path)
        except OSError:
            pass

    atexit.register(_write)
