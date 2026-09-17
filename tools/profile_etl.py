"""Export bounded offline ETL summaries without opening WPA or starting a trace.

Example: uv run --group profiling python tools/profile_etl.py system.etl --output etl-report
Uses installed xperf help contracts: -i TRACE -o OUTPUT -a ACTION [options].
Raw reports remain authoritative; absent/lost events and unresolved symbols are
limitations, never inferred zero-cost graphics or proof that a trace is complete.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from profile_capture import tool_path
from sm64_events.core.childproc import quiet_spawn_kwargs

SUMMARIES = {
    "tracestats": ["-timespan"],
    "profile": ["-detail"],
    "cpudisk": [],
    "dpcisr": ["-summary"],
    "hardfault": [],
    "process": [],
}
READ_LIMIT = 1024 * 1024


def find_xperf() -> str:
    found = tool_path("xperf")
    if found:
        return found
    installed = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Windows Kits/10/Windows Performance Toolkit/xperf.exe"
    if installed.is_file():
        return str(installed)
    raise FileNotFoundError("xperf is unavailable; install the Windows Performance Toolkit")


def excerpt(path: Path) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        raw = stream.read(READ_LIMIT)
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    return raw.decode(encoding, errors="replace")


def loss_evidence(text: str) -> dict:
    """Only explicit numeric loss labels establish zero or loss; no label is unknown."""
    evidence = []
    pattern = re.compile(r"(?i)(events?\s*lost|lost\s*events?|buffers?\s*lost|lost\s*buffers?)\s*[:=,\t ]+\s*(\d[\d,]*)")
    for line in text.splitlines():
        match = pattern.search(line)
        if match:
            evidence.append({"label": match[1], "value": int(match[2].replace(",", "")),
                             "line": line[:500]})
    state = "unknown"
    if evidence:
        state = "reported_loss" if any(row["value"] > 0 for row in evidence) else "reported_zero"
    return {"state": state, "evidence": evidence,
            "note": "Header loss evidence only; unknown does not mean zero. Inspect raw tracestats and action errors."}


def export_action(exe: str, trace: Path, output: Path, action: str,
                  timeout: float, symbol_path: str | None) -> dict:
    report = output / f"{action}.txt"
    log = output / f"{action}.log"
    command = [exe, "-i", str(trace), "-o", str(report), "-quiet", "-target", "machine"]
    environment = os.environ.copy()
    if symbol_path:
        environment["_NT_SYMBOL_PATH"] = symbol_path
        environment["_NT_SYMCACHE_PATH"] = str(output / "symcache")
        command.append("-symbols")
    command.extend(["-a", action, *SUMMARIES[action]])
    row = {"action": action, "command": command, "report": report.name,
           "log": log.name, "returncode": None, "error": None}
    try:
        with log.open("w", encoding="utf-8") as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                    timeout=timeout, env=environment, **quiet_spawn_kwargs())
        row["returncode"] = result.returncode
        if result.returncode:
            row["error"] = f"xperf returned {result.returncode}; inspect {log.name}"
        elif not report.is_file() or report.stat().st_size == 0:
            row["error"] = "xperf produced no report; unavailable is not zero activity"
        elif action == "profile" and "no sampled profile data" in excerpt(report).lower():
            row["error"] = "Trace contains no sampled profile data; successful export is not CPU coverage"
    except (OSError, subprocess.TimeoutExpired) as exc:
        row["error"] = str(exc)
    row["log_excerpt"] = excerpt(log)[:4000]
    return row


def export(trace: Path, output: Path, *, actions: list[str] | None = None,
           timeout: float = 60, symbol_path: str | None = None) -> dict:
    trace = trace.resolve(strict=True)
    if not trace.is_file() or trace.suffix.lower() != ".etl" or trace.stat().st_size == 0:
        raise ValueError("Supply a nonempty existing .etl trace")
    if not 1 <= timeout <= 300:
        raise ValueError("Per-action timeout must be 1..300 seconds")
    chosen = list(dict.fromkeys(["tracestats", *(actions or list(SUMMARIES))]))
    if any(action not in SUMMARIES for action in chosen):
        raise ValueError("Only the documented summary actions are allowed; no event dump")
    exe = find_xperf()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with trace.open("rb") as stream:
        fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {"version": 1, "created_utc": datetime.now(UTC).isoformat(),
                "input": {"path": str(trace), "bytes": trace.stat().st_size, "sha256": fingerprint},
                "xperf": exe, "timeout_per_action_s": timeout,
                "symbols": {"requested": bool(symbol_path), "resolved": None,
                            "note": "Module attribution by default. Requested symbols may remain unresolved; inspect action logs and profile report."},
                "actions": [], "complete": False,
                "limitations": ["GPU tables require WPA analysis; these text summaries do not report GPU utilization.",
                                "No -tle override: xperf may refuse traces containing lost events."]}
    try:
        for action in chosen:
            row = export_action(exe, trace, output, action, timeout, symbol_path)
            manifest["actions"].append(row)
            if action == "tracestats" and row["error"]:
                break  # Invalid ETL is not fed through five further analyzers.
        manifest["complete"] = len(manifest["actions"]) == len(chosen) and not any(
            row["error"] for row in manifest["actions"])
    finally:
        manifest["loss"] = loss_evidence(excerpt(output / "tracestats.txt") + "\n" + excerpt(output / "tracestats.log"))
        if manifest["loss"]["state"] == "reported_loss":
            manifest["complete"] = False
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--action", choices=SUMMARIES, action="append", dest="actions")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--symbol-path", help="Explicit PDB/server search path; can contact configured symbol servers")
    args = parser.parse_args()
    manifest = export(args.trace, args.output, actions=args.actions, timeout=args.timeout,
                      symbol_path=args.symbol_path)
    print(json.dumps({"manifest": str(args.output / "manifest.json"), "complete": manifest["complete"],
                      "loss": manifest["loss"]["state"], "symbols_resolved": None}, indent=2))
    if not manifest["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
