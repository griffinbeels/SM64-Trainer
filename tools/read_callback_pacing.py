"""Read a bounded native-log tail. Callback coverage never proves monitor pacing.

No live requests, process control, database writes or frame filtering. Bin
bounds in telemetry version 1 are exclusive, in ms: 10, 16.667, 25, 33.334,
40, 50, 75, 100, 125, 250, 500, infinity (callback_pacing.h).
Completed callback windows report after 5 seconds with a tail, otherwise 60
seconds. A final unsent window is in memory only; this tail is not a complete
session census. Arrival gaps do not distinguish CPU work from descheduling.
"""
import argparse
import json
import re
from pathlib import Path


def records(path: Path, max_bytes: int = 2 * 1024 * 1024):
    if not 1024 <= max_bytes <= 32 * 1024 * 1024:
        raise ValueError("tail limit must be between 1 KiB and 32 MiB")
    with path.open("rb") as source:
        size = source.seek(0, 2)
        source.seek(max(0, size - max_bytes))
        if size > max_bytes:
            source.readline()  # do not parse a partial first record
        text = source.read(max_bytes).decode("utf-8", errors="replace")
    for line in text.splitlines():
        if "event=callback_pacing " not in line and "event=callback_progress " not in line:
            continue
        row = dict(re.findall(r"(?:^| )([a-zA-Z_]+)=([^ ]+)", line))
        frequency = int(row["frequency"])
        row["frequency_valid"] = frequency > 0
        for field in ("duration_max_ticks", "gap_max_ticks", "age_ticks"):
            if field in row:
                row[field.removesuffix("_ticks") + "_ms"] = (
                    round(int(row[field]) * 1000 / frequency, 3) if frequency > 0 else None
                )
        for field in ("gap_bins", "duration_bins"):
            if field in row:
                row[field] = [int(value) for value in row[field].split(",")]
        row["monitor_presentation"] = "unmeasured"
        row["tail_truncated"] = size > max_bytes
        yield row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--max-bytes", type=int, default=2 * 1024 * 1024)
    args = parser.parse_args()
    for row in records(args.log, args.max_bytes):
        print(json.dumps(row, separators=(",", ":")))


if __name__ == "__main__":
    main()
