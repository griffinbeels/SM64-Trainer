"""Project entry point for the installed shared local verifier."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
harness = Path(os.environ.get("HARNESS_ROOT", Path.home() / ".claude/harness"))
entry = harness / "tools/verify.py"
if not entry.is_file():
    raise SystemExit("Shared verifier unavailable; install the shared harness first.")
sys.path.insert(0, str(harness))
sys.argv[1:1] = ["--project", str(ROOT)]
runpy.run_path(str(entry), run_name="__main__")
