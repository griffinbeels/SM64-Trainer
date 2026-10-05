"""Read a bounded native-log tail. Callback coverage never proves monitor pacing.

No live requests, process control, database writes or frame filtering. Bin
bounds in telemetry version 1 are exclusive, in ms: 10, 16.667, 25, 33.334,
40, 50, 75, 100, 125, 250, 500, infinity (callback_pacing.h).
Completed callback windows report after 5 seconds with a tail, otherwise 60
seconds. Shutdown tries to queue partial windows without waiting; final-window
records distinguish queued, unsent and unavailable. Queued does not prove a
disk write succeeded. This tail is not a complete session census. Version 2 attaches raw thread cycles and quantized CPU-time
observations to exact maximum gap/duration intervals. Cycles are never time.
CPU time can be coarse (15.625 ms steps observed in one local calibration),
and a measured zero remains unresolved. Low producer CPU cannot distinguish
waiting, intentional pacing, pause, or ready-but-descheduled time.
"""
import argparse
import json
import re
from pathlib import Path

CPU_REASONS = ("measured", "no_sample", "query_failed", "thread_changed",
               "counter_reversed", "overlap", "clock_invalid")


def cpu_evidence(row, prefix, frequency):
    """Keep raw observations; absent, failed or quantized-zero time stays unknown."""
    stem = prefix + "_cpu_"
    valid = row.get(stem + "valid") == "1"
    reason = int(row.get(stem + "reason", "1"))
    row[stem + "valid"] = valid
    row[stem + "status"] = CPU_REASONS[reason] if 0 <= reason < len(CPU_REASONS) else "unknown"
    if not valid and row[stem + "status"] == "measured":
        row[stem + "status"] = "no_sample"
    for tail in ("begin_qpc", "end_qpc", "generation", "tid", "error", "100ns", "aperture_ticks"):
        key = stem + tail
        row[key] = int(row[key]) if key in row else None
    cycles = prefix + "_thread_cycles"
    row[cycles] = int(row[cycles]) if valid and cycles in row else None
    observed = row[stem + "100ns"]
    row[stem + "ms"] = round(observed / 10000, 4) if valid and observed else None
    row[stem + "time_state"] = "quantized" if valid and observed else "unresolved"
    ticks = row[stem + "aperture_ticks"]
    row[stem + "aperture_ms"] = round(ticks * 1000 / frequency, 4) if ticks is not None and frequency > 0 else None


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
        if not any(f"event={event} " in line for event in ("callback_pacing", "callback_progress", "callback_final_window")):
            continue
        row = dict(re.findall(r"(?:^| )([a-zA-Z_][a-zA-Z0-9_]*)=([^ ]+)", line))
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
        for prefix in ("duration", "gap", "outside"):
            cpu_evidence(row, prefix, frequency)
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
