"""One-shot startup build, isolated from the capture interpreter.

The child receives workbook bytes/options in scratch files and returns a
compressed snapshot. Only the parent owns the live library and database.
"""
import asyncio
import gzip
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.library.store import build_and_stamp, write_snapshot
from sm64_events.library.build import SCHEMA_VERSION
from sm64_events.library.workbook import log_revision

WORKER_FLAG = "--library-refresh-worker"
TIMEOUT_S = 120.0
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
log = logging.getLogger("sm64.library")


def _command(directory: Path) -> list[str]:
    args = [str(directory), str(os.getpid())]
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_FLAG, *args]
    # The build uses only stdlib + our source. Bypass Windows venv redirectors
    # so timeout/cancel owns the computing process, not just its launcher.
    return [getattr(sys, "_base_executable", sys.executable),
            "-m", "sm64_events.library.background", *args]


def _spawn(directory: Path):
    env = os.environ.copy()
    if not getattr(sys, "frozen", False):
        # A shared editable venv may belong to another worktree.
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (
            str(Path(__file__).resolve().parents[2]), env.get("PYTHONPATH"))))
    options = quiet_spawn_kwargs()
    if os.name == "nt":
        options["creationflags"] |= subprocess.BELOW_NORMAL_PRIORITY_CLASS
    return subprocess.Popen(_command(directory), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            **options)


async def _wait(child, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    try:
        while child.poll() is None:
            if time.monotonic() >= deadline:
                raise TimeoutError("library build worker timed out")
            await asyncio.sleep(0.05)
        if child.returncode:
            raise RuntimeError(f"library build worker exited {child.returncode}")
    finally:
        # Cancellation stops/reaps the actual child before scratch cleanup.
        if child.poll() is None:
            child.kill()
        await asyncio.to_thread(child.wait)


def _read_json(path: Path, limit: int) -> dict:
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("library worker response exceeds its size limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("invalid library worker response")
    return payload


def _read_prepared(path: Path, receipt: dict) -> dict:
    with gzip.open(path, "rb") as handle:
        raw = handle.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("library worker snapshot exceeds its size limit")
    payload = json.loads(raw)
    if (not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION
            or not isinstance(payload.get("targets"), list)
            or payload.get("sheet_revision") != receipt["sheet_revision"]):
        raise ValueError("library worker snapshot identity is invalid")
    return payload


async def refresh_at_startup(library, fetch_fn, overrides=None, *, timeout_s=TIMEOUT_S):
    """Download off-thread; build/fit/stamp/compress in a quiet owned child."""
    data = await asyncio.to_thread(fetch_fn)
    revision = log_revision(data)
    skipped = library.skip_revision(revision)
    if skipped is not None:
        return skipped  # no process, temporary workbook, build or disk churn
    with tempfile.TemporaryDirectory(prefix="sm64-library-") as name:
        directory = Path(name)
        (directory / "sheet.xlsx").write_bytes(data)
        del data
        request = {"version": 1, "overrides": overrides}
        (directory / "request.json").write_text(json.dumps(request), encoding="utf-8")
        await _wait(_spawn(directory), timeout_s)
        receipt = _read_json(directory / "result.json", 16 * 1024)
        if receipt.get("version") != 1 or receipt.get("error"):
            raise ValueError(receipt.get("error") or "invalid library worker version")
        if receipt.get("sheet_revision") != revision:
            raise ValueError("library worker revision differs from requested workbook")
        cpu_s, wall_s = float(receipt["cpu_s"]), float(receipt["wall_s"])
        apply_start = time.perf_counter()
        snapshot = directory / "snapshot.json.gz"
        payload = _read_prepared(snapshot, receipt)
        result = library.absorb(payload, prepared_snapshot=snapshot)
        log.info("library startup worker: build_cpu_s=%.3f build_wall_s=%.3f "
                 "parent_apply_ms=%.3f applied=%s", cpu_s, wall_s,
                 (time.perf_counter() - apply_start) * 1000, result["applied"])
        return result


def _watch_parent(parent_pid: int) -> None:
    """A killed parent must not leave a CPU-heavy build process behind."""
    if os.name == "nt":
        import ctypes as C
        from ctypes import wintypes as W
        kernel = C.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        kernel.OpenProcess.restype = W.HANDLE
        kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        kernel.WaitForSingleObject.restype = W.DWORD
        kernel.CloseHandle.argtypes = [W.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, parent_pid)  # SYNCHRONIZE
        if handle:
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
            kernel.CloseHandle(handle)
    else:
        while os.getppid() == parent_pid:
            time.sleep(0.2)
    os._exit(1)  # only this scratch worker; no database/app handles exist


def worker_main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        return 2
    directory, parent_pid = Path(args[0]), int(args[1])
    threading.Thread(target=_watch_parent, args=(parent_pid,), daemon=True).start()
    wall, cpu = time.perf_counter(), time.process_time()
    try:
        request = _read_json(directory / "request.json", 1024 * 1024)
        if request.get("version") != 1:
            raise ValueError("invalid library build request version")
        payload = build_and_stamp((directory / "sheet.xlsx").read_bytes(), request["overrides"])
        write_snapshot(directory / "snapshot.json.gz", payload)
        receipt = {"version": 1, "sheet_revision": payload["sheet_revision"],
                   "worker_pid": os.getpid(),
                   "cpu_s": time.process_time() - cpu,
                   "wall_s": time.perf_counter() - wall}
    except Exception as error:  # noqa: BLE001 -- worker failure is an explicit parent receipt
        receipt = {"version": 1, "error": f"{type(error).__name__}: {error}"[:512]}
    (directory / "result.json").write_text(json.dumps(receipt), encoding="utf-8")
    return 0  # failures are bounded receipts, not another app/logging setup


if __name__ == "__main__":
    raise SystemExit(worker_main())
