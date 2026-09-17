"""Script entry for the recorder-owned x64 encoder helper."""

from pathlib import Path
import sys

# Source checkout entry point; the installed executable supplies its own dispatch.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from sm64_events.replay.gpuprocess.process_worker import run

if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("nonce/max_json/max_packet required")
    run(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
