"""Read copied native capture logs without contacting PJ64 or opening GPU objects."""
from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import re

FIELDS = re.compile(r"(?:^|\s)([a-zA-Z0-9_]+)=([^\s]+)")
PHASES = ("wait", "take", "sample", "decisions", "copy", "bridge_returns", "reap", "frontier")
MAX_BYTES = 4 * 1024 * 1024


def numeric(fields, name):
    try:
        return int(fields[name])
    except (KeyError, ValueError):
        return None


def milliseconds(ticks, frequency):
    return None if ticks is None or not frequency or frequency < 0 else ticks * 1000 / frequency


def parse(lines):
    """Keep only bounded complete-header groups; dropped/missing rows stay unknown."""
    windows = deque(maxlen=120)
    current = None
    for line in lines:
        fields = dict(FIELDS.findall(line))
        event = fields.get("event")
        if event == "gpu_summary":
            current = dict(utc=line.split()[0], pid=numeric(fields, "pid"),
                           epoch=numeric(fields, "epoch"), header=fields, phases={}, cadence=None)
            windows.append(current)
        elif event in {"gpu_summary_phase", "gpu_summary_cadence"} and current:
            observed = numeric(fields, "observed_qpc")
            if (observed is None or observed != numeric(current["header"], "observed_qpc")
                    or (numeric(fields, "pid"), numeric(fields, "epoch")) != (current["pid"], current["epoch"])):
                continue  # Dropped headers cannot attach new rows to an earlier window.
            frequency = numeric(current["header"], "frequency")
            if event == "gpu_summary_phase" and fields.get("phase") in PHASES:
                calls = numeric(fields, "calls")
                total_ms = milliseconds(numeric(fields, "total_ticks"), frequency)
                current["phases"][fields["phase"]] = dict(
                    calls=calls, total_ms=total_ms,
                    mean_ms=None if not calls or total_ms is None else total_ms / calls,
                    max_ms=milliseconds(numeric(fields, "max_ticks"), frequency),
                    max_started_qpc=numeric(fields, "max_started_qpc"))
            elif event == "gpu_summary_cadence":
                current["cadence"] = {**fields,
                    "max_ms": milliseconds(numeric(fields, "max_ticks"), frequency),
                    "min_ms": milliseconds(numeric(fields, "min_ticks"), frequency)}
    result = list(windows)
    for window in result:
        window["missing_phases"] = [p for p in PHASES if p not in window["phases"]]
        header = window["header"]
        window["previous_logging_ms"] = milliseconds(numeric(header, "previous_emit_ticks"),
                                                       numeric(header, "frequency"))
        window["resources"] = {name: numeric(header, name) for name in (
            "snapshot_bytes", "bridge_bytes", "sample_calls", "sample_reuses", "publish_busy",
            "refused")}
    return {"windows": result, "limits": [
        "Worker phase durations can overlap; do not sum them as CPU time.",
        "Source-boundary cadence is not measured display presentation or game FPS.",
        "Missing summary rows or samples remain unknown; old plugin logs may contain no healthy summaries.",
        "Snapshot/bridge bytes count logical texture payloads, not driver or total GPU memory.",
        "Sampling/reuse/busy counts accumulate within a capture epoch; retries are not new pictures.",
        "At most the last 120 summary windows from the final 4 MiB are retained."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.log.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - MAX_BYTES))
        if size > MAX_BYTES:
            stream.readline()  # A truncated leading record cannot supply identity.
        result = parse(stream.read(MAX_BYTES).decode("utf-8", errors="replace").splitlines())
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
