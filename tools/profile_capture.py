"""Bounded, explicit-localhost performance capture; never starts the trainer."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from sm64_events.core.childproc import quiet_spawn_kwargs

PROFILE = "/api/diagnostics/profile"
MAX_RESPONSE = 4 * 1024 * 1024
MAX_SAMPLE_LOG = 64 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("Profiling endpoints must not redirect")


def local_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("Supply an explicit http://localhost:PORT server origin")
    if parsed.port is None:
        raise ValueError("An explicit server port is required")
    return value.rstrip("/")


def request(origin: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = Request(origin + path, data=data, headers={"Content-Type": "application/json"})
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=2) as response:
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError("Profile response exceeds 4 MiB")
    result = json.loads(raw)
    if result is None and path == "/api/replay/status":
        # A server without a recorder legitimately returns JSON null.
        return {"available": False}
    if not isinstance(result, dict):
        raise ValueError("Expected an object from profiling endpoint")
    return result


def command(args: list[str], timeout: float = 15) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                            **quiet_spawn_kwargs())
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr or result.stdout}")
    return result.stdout.strip()


def tool_path(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    sibling = Path(sys.executable).parent / (name + (".exe" if os.name == "nt" else ""))
    if sibling.is_file():
        return str(sibling)
    if os.name == "nt" and name in {"wpr", "wpa", "wpaexporter", "xperf"}:
        toolkit = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Windows Kits/10/Windows Performance Toolkit"
        candidate = toolkit / (name + ".exe")
        if candidate.is_file():
            return str(candidate)
    return None


def doctor() -> dict:
    tools = {name: tool_path(name) for name in ("wpr", "wpa", "wpaexporter", "xperf", "py-spy", "nvidia-smi")}
    return {"tools_on_path": tools, "psutil": importlib.util.find_spec("psutil") is not None,
            "note": "Searches PATH, this Python environment and the standard Windows Performance Toolkit install. GPU data requires a separate trace."}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class SystemSampler:
    """Low-rate system/process counters. PID plus creation time fences PID reuse."""

    def __init__(self, pids: list[int]):
        import psutil
        self.psutil = psutil
        self.processes = []
        for pid in pids:
            process = psutil.Process(pid)
            self.processes.append(process)
            process.cpu_percent()
        psutil.cpu_percent()

    def sample(self) -> dict:
        psutil = self.psutil
        processes = []
        for process in self.processes:
            try:
                if not process.is_running():
                    raise psutil.NoSuchProcess(process.pid)
                with process.oneshot():
                    io = process.io_counters()
                    processes.append({"pid": process.pid, "created": process.create_time(),
                                      "name": process.name(), "cpu_percent": process.cpu_percent(),
                                      "rss_bytes": process.memory_info().rss,
                                      "threads": process.num_threads(),
                                      "read_bytes": io.read_bytes, "write_bytes": io.write_bytes})
            except psutil.Error as exc:
                processes.append({"pid": process.pid, "error": type(exc).__name__})
        return {"cpu_percent": psutil.cpu_percent(),
                "memory_used_bytes": psutil.virtual_memory().used,
                "processes": processes, "gpu": None}


class WakeProbe:
    """Independent 50 ms wake samples; scheduler delay, not display latency."""

    def __init__(self):
        self.stop = threading.Event()
        self.samples = []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            start = time.perf_counter()
            if self.stop.wait(0.05):
                break
            if len(self.samples) < 7000:
                self.samples.append(max(0, (time.perf_counter() - start - 0.05) * 1000))

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)


class ExternalTraces:
    """Only UUID-owned WPR sessions and our py-spy child can be stopped here."""

    def __init__(self, output: Path):
        self.output = output
        self.instance = "SM64-profile-" + uuid.uuid4().hex
        self.wpr = None
        self.spy = None
        self.spy_log = None
        self.profiles = []
        self.missing = []

    def start(self, seconds: float, wpr: bool, spy_pid: int | None):
        if wpr:
            exe = tool_path("wpr")
            if not exe or "-instancename" not in command([exe, "-help", "advanced"]):
                raise RuntimeError("WPR with named instance support is required")
            available = {line.split()[0] for line in command([exe, "-profiles"]).splitlines() if line.split()}
            self.profiles = ["GeneralProfile"]
            if "GPU" in available:
                self.profiles.append("GPU")
            else:
                self.missing.append("WPR GPU profile unavailable; no GPU activity trace requested")
            # Memory mode is bounded; filemode can fill the disk. Name is always last.
            self.wpr = exe
            (self.output / "wpr-owner.json").write_text(json.dumps({
                "instance": self.instance,
                "recovery": [exe, "-stop", str(self.output / "system.etl"),
                             "-instancename", self.instance]}), encoding="utf-8")
            command([exe, *[arg for profile in self.profiles for arg in ("-start", profile)],
                     "-instancename", self.instance])
        if spy_pid:
            exe = tool_path("py-spy")
            if not exe:
                raise RuntimeError("py-spy is not on PATH; install it explicitly before requesting stacks")
            self.spy_log = (self.output / "py-spy.log").open("w", encoding="utf-8")
            self.spy = subprocess.Popen([
                exe, "record", "--pid", str(spy_pid), "--duration", str(int(seconds)),
                "--rate", "49", "--format", "speedscope", "--output",
                str(self.output / "python-stacks.json")], stdout=self.spy_log,
                stderr=subprocess.STDOUT, **quiet_spawn_kwargs())

    def close(self) -> list[str]:
        errors = []
        if self.spy:
            try:
                code = self.spy.wait(timeout=5)
                if code:
                    errors.append(f"py-spy exit {code}; see py-spy.log")
            except subprocess.TimeoutExpired:
                self.spy.terminate()
                self.spy.wait(timeout=5)
                errors.append("py-spy interrupted before completion")
        if self.spy_log:
            self.spy_log.close()
        if self.wpr:
            try:
                command([self.wpr, "-stop", str(self.output / "system.etl"),
                         "-skipPdbGen", "-instancename", self.instance], timeout=90)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                errors.append(str(exc))
                try:
                    command([self.wpr, "-cancel", "-instancename", self.instance])
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as cleanup:
                    errors.append(f"Owned WPR cleanup failed: {cleanup}")
        return errors


def validate_capture(args) -> tuple[str, dict]:
    origin = local_url(args.url)
    workload = json.loads(args.workload.read_text(encoding="utf-8"))
    required = {"scenario", "rom", "save_state", "renderer", "resolution", "settings", "ambient"}
    if not isinstance(workload, dict) or not required <= workload.keys():
        raise ValueError(f"Workload JSON requires {sorted(required)}")
    if not 1 <= args.seconds <= 300 or not 0.1 <= args.interval <= 5:
        raise ValueError("Capture lasts 1..300 seconds, sample interval 0.1..5 seconds")
    health = request(origin, "/health")
    before = request(origin, PROFILE)
    if before.get("enabled"):
        raise ValueError("A profile is already active; leaving it untouched")
    replay = request(origin, "/api/replay/status")
    return origin, {"version": 1, "started_utc": datetime.now(UTC).isoformat(), "origin": origin,
            "workload": workload, "variant": args.variant,
            "machine": {"host": platform.node(), "platform": platform.platform(),
                        "cpu_count": os.cpu_count()},
            "sampling": {"seconds": args.seconds, "interval": args.interval,
                         "wpr": args.wpr, "python_stacks": args.py_spy_pid is not None},
            "artifacts": {str(path): digest(path) for path in args.artifact},
            "recorder_config": {key: replay.get(key) for key in ("encoder", "audio_mode", "frame_source", "retention_s", "max_buffer_bytes")},
            "health": health, "doctor": doctor(), "complete": False, "errors": []}


def collect_samples(origin: str, output: Path, sampler: SystemSampler,
                    started: float, seconds: float, interval: float):
    deadline = started + seconds
    written = 0
    with (output / "samples.jsonl").open("w", encoding="utf-8") as stream:
        while time.perf_counter() < deadline:
            tick = time.perf_counter()
            sample = {"elapsed_s": tick - started, "system": sampler.sample()}
            for name, path in (("profile", PROFILE), ("replay", "/api/replay/status"), ("health", "/health")):
                try:
                    sample[name] = request(origin, path)
                except (OSError, ValueError) as exc:
                    sample[name] = {"error": str(exc)}
            sample["observer_ms"] = (time.perf_counter() - tick) * 1000
            line = json.dumps(sample) + "\n"
            written += len(line.encode("utf-8"))
            if written > MAX_SAMPLE_LOG:
                raise ValueError("Sample log exceeds 64 MiB; shorten the capture or increase the interval")
            stream.write(line)
            time.sleep(max(0, min(deadline - time.perf_counter(), interval - (time.perf_counter() - tick))))


def capture(args) -> Path:
    origin, meta = validate_capture(args)
    sampler = SystemSampler(args.pid)
    args.output.mkdir(parents=True, exist_ok=False)
    traces = ExternalTraces(args.output)
    probe = WakeProbe()
    owner = None
    started = time.perf_counter()
    try:
        traces.start(args.seconds, args.wpr, args.py_spy_pid)
        initial = request(origin, PROFILE + "/start", {"duration_s": args.seconds})
        owner = initial["session_id"]
        meta["profile_session"] = owner
        meta["initial_profile"] = initial
        probe.thread.start()
        started = time.perf_counter()
        collect_samples(origin, args.output, sampler, started, args.seconds, args.interval)
        meta["complete"] = True
    except (OSError, ValueError, RuntimeError, KeyError, KeyboardInterrupt) as exc:
        meta["errors"].append(str(exc) or "Interrupted")
    finally:
        if probe.thread.ident is not None:
            probe.close()
        meta["wake_excess_ms"] = probe.samples
        if owner:
            try:
                meta["final_profile"] = request(origin, PROFILE + "/stop", {"session_id": owner})
            except (OSError, ValueError) as exc:
                meta["errors"].append(f"Profile stop failed; server capture expires automatically: {exc}")
        meta["elapsed_s"] = time.perf_counter() - started
        meta["errors"].extend(traces.close())
        meta["external_traces"] = {"wpr_profiles": traces.profiles, "missing": traces.missing}
        (args.output / "capture.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return args.output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor")
    run = sub.add_parser("record")
    run.add_argument("--url", required=True)
    run.add_argument("--workload", type=Path, required=True)
    run.add_argument("--variant", required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--seconds", type=int, default=30)
    run.add_argument("--interval", type=float, default=1)
    run.add_argument("--pid", type=int, action="append", default=[])
    run.add_argument("--artifact", type=Path, action="append", default=[])
    run.add_argument("--wpr", action="store_true")
    run.add_argument("--py-spy-pid", type=int)
    args = parser.parse_args()
    if args.action == "doctor":
        print(json.dumps(doctor(), indent=2))
    else:
        output = capture(args)
        print(output)
        meta = json.loads((output / "capture.json").read_text(encoding="utf-8"))
        if not meta["complete"] or meta["errors"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
