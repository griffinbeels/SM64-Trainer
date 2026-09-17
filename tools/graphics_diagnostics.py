"""Read bounded PJ64/plugin logs and existing capture status; never enable capture.

Run with the profiling dependency group. Works while PJ64 is playing or after it
closes with --pj64-dir. --seconds 0 is a snapshot; 1..30 samples the control
page's counters at 4 Hz. No registry writes, graphics calls, tracing or app restarts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
from datetime import UTC, datetime

from sm64_events.core.plugin_installation import file_identity

TAIL_BYTES = 64 * 1024
#: the control page name the wrapper publishes (plugin/gfxwrap/gfxwrap.c)
CONTROL_NAME = "sm64_trainer_gfx_v1"
MAX_LOGS = 20


def file_tail(path: Path, limit: int = TAIL_BYTES) -> dict:
    """Read at most limit bytes, preserving truncation and read failure evidence."""
    try:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            offset = max(0, size - limit)
            stream.seek(offset)
            raw = stream.read(limit)
        return {"path": str(path), "bytes": size, "offset": offset,
                "tail_sha256": hashlib.sha256(raw).hexdigest(),
                "text": raw.decode("utf-8-sig", errors="replace"), "raw": raw, "error": None}
    except OSError as exc:
        return {"path": str(path), "error": str(exc), "text": "", "raw": b""}


def file_fingerprint(path: Path) -> dict:
    return file_identity(path)


def wrapper_files(folders: set[Path]) -> list[dict]:
    paths = {directory / "sm64_trainer_gfx.dll" for folder in folders
             for directory in (folder, folder / "Plugin")
             if (directory / "sm64_trainer_gfx.dll").is_file()}
    return [file_fingerprint(path) for path in sorted(paths)]


def find_processes() -> list[dict]:
    import psutil
    found = []
    for process in psutil.process_iter(["pid", "name", "exe", "create_time"]):
        if (process.info["name"] or "").lower() != "project64.exe":
            continue
        row = dict(process.info)
        try:
            row["graphics_modules"] = [m.path for m in process.memory_maps()
                                       if any(name in m.path.lower() for name in
                                              ("sm64_trainer_gfx", "gliden64", "nvogl", "discordhook", "nvspcap"))]
        except (psutil.Error, OSError) as exc:
            row["modules_error"] = str(exc)
        found.append(row)
    return found


def log_paths(folders: set[Path]) -> list[Path]:
    """Only known installation/log folders; never recurse through the user's disk."""
    result = set()
    for folder in sorted(folders):
        for directory in (folder, folder / "Plugin", folder / "Logs"):
            if directory.is_dir():
                for path in sorted(directory.glob("*.log*")):
                    if path.name.endswith((".log", ".log.1")) and path.is_file():
                        result.add(path)
                    if len(result) >= MAX_LOGS:
                        return sorted(result)
    return sorted(result)


def wrapper_identity(logs: list[dict], processes: list[dict]) -> list[dict]:
    """Correlate startup lines with process start; module enumeration is insufficient."""
    identities = []
    for log in logs:
        for line in log["text"].splitlines():
            match = re.search(r"pid=(\d+)\s+.*?build=([\w.-]+).*event=(?:init_begin|initiate)", line)
            if not match:
                continue
            try:
                timestamp = datetime.fromisoformat(line.split()[0]).timestamp()
            except ValueError:
                timestamp = None
            matches = [p for p in processes if p["pid"] == int(match[1])]
            active = bool(timestamp is not None and matches
                          and matches[0].get("create_time") is not None
                          and matches[0]["create_time"] <= timestamp <= time.time())
            identities.append({"pid": int(match[1]), "build_id": match[2],
                               "source": log["path"], "line": line,
                               "matches_current_process_start": active})
    return identities[-20:]


def control_snapshot(name: str) -> dict:
    """Read candidate identity/status without enabling a lease or pixel pool."""
    from dataclasses import asdict
    from sm64_events.replay.capturecontrol import CaptureControl
    try:
        with CaptureControl(name) as control:
            return {"status": asdict(control.status()), "error": None}
    except (OSError, RuntimeError) as exc:
        return {"status": None, "error": str(exc)}


def _same_bytes(left, right):
    if not left or not right or not left.get("sha256") or not right.get("sha256"):
        return None
    return left["sha256"] == right["sha256"]


def installation_identity(candidate, bundle, installed, processes, control):
    """Separate disk equality from current PID/birth-bound source-build evidence."""
    loaded = []
    status = control.get("status") or {}
    from sm64_events.replay.capturecontrol import CLOSED
    birth = ((status.get("producer_created_hi", 0) << 32)
             | status.get("producer_created_lo", 0)) / 10_000_000 - 11644473600
    for process in processes:
        paths = [p for p in process.get("graphics_modules", [])
                 if Path(p).name.lower() == "sm64_trainer_gfx.dll"]
        created = process.get("create_time")
        current = bool(paths and isinstance(created, (int, float))
                       and status.get("producer_pid") == process["pid"]
                       and status.get("state", CLOSED) != CLOSED
                       and abs(birth - created) < .01)
        labels = candidate.get("build_ids", []) if candidate else []
        build = status.get("build_id") if current else None
        loaded.append({"pid": process["pid"], "module_paths": paths,
                       "control_matches_process_birth": current,
                       "source_build_id": build,
                       "source_build_matches_candidate": (build == labels[0]
                                                            if build and len(labels) == 1 else None)})
    return {"candidate": candidate, "bundle": bundle,
            "bundle_bytes_match_candidate": _same_bytes(candidate, bundle),
            "installed": [{**item, "bytes_match_candidate": _same_bytes(candidate, item)}
                          for item in installed], "loaded": loaded,
            "limits": ["Equal source-build labels do not prove equal compiled DLL bytes.",
                       "Disk hashes describe files read now, not a hash of mapped process memory.",
                       "Missing or stale loaded evidence remains unknown; no capture is enabled."]}


def collect(output: Path, *, pj64_dir: Path | None = None, seconds: float = 0,
            stream_name: str = CONTROL_NAME, expected_wrapper: Path | None = None,
            bundled_wrapper: Path | None = None) -> dict:
    if not 0 <= seconds <= 30:
        raise ValueError("seconds must be between 0 and 30")
    output.mkdir(parents=True, exist_ok=False)
    processes = find_processes()
    folders = {Path(p["exe"]).parent for p in processes if p.get("exe")}
    if pj64_dir:
        folders.add(pj64_dir.resolve(strict=True))
    # Loaded paths discover a custom Plugin directory without trusting registry
    # selection as evidence of which plugin was activated for this ROM.
    folders.update(Path(module).parent for p in processes for module in p.get("graphics_modules", [])
                   if Path(module).name.lower() == "sm64_trainer_gfx.dll")
    samples = []
    start = time.monotonic()
    try:
        while True:
            samples.append({"elapsed_s": time.monotonic() - start,
                            "control": control_snapshot(stream_name)})
            remaining = seconds - (time.monotonic() - start)
            if remaining <= 0:
                break
            time.sleep(min(.25, remaining))
    finally:
        pass
    logs = [file_tail(path) for path in log_paths(folders)]
    identities = wrapper_identity(logs, processes)
    for index, log in enumerate(logs):
        name = f"{index:02d}-{Path(log['path']).name}"
        (output / name).write_bytes(log.pop("raw"))
        log.pop("text")
        log["captured_as"] = name
    installed = wrapper_files(folders)
    report = {"utc": datetime.now(UTC).isoformat(), "processes": processes,
              "samples": samples, "logs": logs,
              "wrapper_startup_records": identities, "wrapper_files_on_disk": installed,
              "installation": installation_identity(
                  file_identity(expected_wrapper) if expected_wrapper else None,
                  file_identity(bundled_wrapper) if bundled_wrapper else None,
                  installed, processes, samples[-1]["control"]),
              "limits": ["Read-only snapshots; no capture/profile lease was enabled.",
                         "Control page fields are independently published; a snapshot is not an atomic record.",
                         "Loaded module or saved registry selection does not establish the active renderer.",
                         "Callback progression is not display presentation; absent data is unknown.",
                         "Log tails are capped at 64 KiB each, 20 files; older events may be omitted."]}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pj64-dir", type=Path)
    parser.add_argument("--seconds", type=float, default=0)
    parser.add_argument("--stream", default=CONTROL_NAME)
    parser.add_argument("--expected-wrapper", type=Path, help="Exact candidate DLL to compare; never installed")
    parser.add_argument("--bundled-wrapper", type=Path, help="Bundle override; defaults to this checkout's bundle")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("PJ64 diagnostics require Windows")
    from sm64_events.core.paths import bundled_plugin_dll
    report = collect(args.output, pj64_dir=args.pj64_dir, seconds=args.seconds, stream_name=args.stream,
                     expected_wrapper=args.expected_wrapper,
                     bundled_wrapper=args.bundled_wrapper or bundled_plugin_dll())
    print(json.dumps({"report": str(args.output / "report.json"),
                      "pj64_pids": [p["pid"] for p in report["processes"]],
                      "logs": len(report["logs"]), "mapping_error": report["mapping_error"],
                      "installation": report["installation"]}, indent=2))


if __name__ == "__main__":
    main()
