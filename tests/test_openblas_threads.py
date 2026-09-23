"""numpy's OpenBLAS reserves a buffer per thread at import: on his 32-thread
machine `import numpy` cost 749 MiB of private commit, and 9 MiB with one
thread. Nothing here calls BLAS, so the package pins it to one before any
entry point can load numpy (sm64_events/__init__.py)."""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _fresh(code: str) -> str:
    env = {key: value for key, value in os.environ.items() if key != "OPENBLAS_NUM_THREADS"}
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                            capture_output=True, text=True, timeout=120, check=True)
    return result.stdout.strip()


def test_importing_the_package_pins_openblas_to_one_thread_before_numpy():
    assert _fresh("import sm64_events, os, numpy; print(os.environ['OPENBLAS_NUM_THREADS'])") == "1"


def test_an_explicit_setting_still_wins():
    code = ("import os; os.environ['OPENBLAS_NUM_THREADS'] = '4'; import sm64_events; "
            "print(os.environ['OPENBLAS_NUM_THREADS'])")
    assert _fresh(code) == "4"


def test_the_frozen_entry_reaches_the_package_before_anything_loads_numpy():
    """gui_entry.py is the exe's first module; its only import before the
    package is `sys`, and the runtime hook that runs before it loads comtypes,
    never numpy."""
    code = ("import sys; sys.argv = ['x']; import gui_entry; "
            "print('numpy' in sys.modules)")
    assert _fresh(code) == "False"
    assert "numpy" not in (REPO / "tools" / "rthook_comtypes.py").read_text(encoding="utf-8")
