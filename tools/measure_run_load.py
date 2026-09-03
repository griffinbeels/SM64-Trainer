"""What a full test run costs the DESKTOP, and what buying that back costs in time.

    uv run python tools/measure_run_load.py                       # idle baseline, then 16 workers, then the candidates
    uv run python tools/measure_run_load.py --config 16:0 16:8    # only these (workers:reserved cores)
    uv run python tools/measure_run_load.py --idle-seconds 45     # longer baseline

He reported the machine "HORRIBLY laggy" while the gate runs (2026-09-02).
Lag is not CPU percent -- a run can sit at 100% and stay usable, or sit at
80% and stutter. What a person feels is **how long a normal-priority thread
waits for a core after it is ready to run**, so that is what this measures
directly: a probe thread in THIS process sleeps 50 ms in a loop and records
how much longer than 50 ms each wake actually took. The excess is scheduler
wait, in milliseconds, on the same footing as the compositor, the browser
and the editor he is typing in. p95 of that excess is the number to compare
configurations by; median says nothing, because a stutter is a tail event.

Alongside it: system CPU percent, the share of samples pinned at 95%+, peak
RAM, and the run's own wall time and pass/fail -- so a configuration that
buys a smooth desktop by doubling the gate is visible as exactly that trade.

**Every row carries the AMBIENT load it was measured against**, sampled in
the quiet seconds just before that run starts. This machine is never idle --
sibling Claude sessions run their own suites, and one of them saturating the
CPU for four minutes would swamp the difference between two configurations
while looking like a clean number. A row whose ambient CPU is high was
measured on a busy machine and is not comparable with a row whose ambient
CPU is low; without the column that is invisible, which is the failure mode
of every benchmark run on a shared box.

**The two levers, and the one that is already ruled out.**
BELOW_NORMAL_PRIORITY_CLASS on the whole tree is NOT a candidate and is not
offered here: two full runs at that class went red (6 failed + 51 errors,
then 11 + 13) where the same tree at normal priority ran green, because this
machine always carries normal-priority load beside a run and a starved
worker times out its browsers (2026-09-01, `tools/run_tests.py`). The two
that remain are **worker count** (fewer workers, less of everything, more
wall time) and **reserved cores** -- CPU affinity that keeps the whole test
tree off K of the 32 logical processors, so the desktop always has somewhere
to run while the run keeps all 16 workers. Both are flags on the door
(`tools/run_tests.py --workers N --reserve K`), which is what this tool
starts: the thing measured is the thing that ships.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
PROBE_INTERVAL = 0.05
POLL_INTERVAL = 2.0
SUMMARY = re.compile(r"^(\d+) (passed|failed)", re.M)
FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)


class Probe:
    """A normal-priority thread standing in for whatever he is looking at.

    It sleeps `PROBE_INTERVAL` and records how much longer the wake took than
    it asked for. Samples of system CPU and memory ride the same loop rather
    than a second thread, so the probe never waits on the GIL for a sampler."""

    def __init__(self) -> None:
        self.wake_excess_ms: list[float] = []
        self.cpu_percent: list[float] = []
        self.used_gb: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        psutil.cpu_percent(interval=None)
        tick = 0
        while not self._stop.is_set():
            before = time.perf_counter()
            time.sleep(PROBE_INTERVAL)
            self.wake_excess_ms.append((time.perf_counter() - before - PROBE_INTERVAL) * 1000)
            tick += 1
            if tick % 10 == 0:
                self.cpu_percent.append(psutil.cpu_percent(interval=None))
                self.used_gb.append(psutil.virtual_memory().used / 1e9)

    def __enter__(self) -> "Probe":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def report(self) -> dict:
        lag = sorted(self.wake_excess_ms)
        cpu = self.cpu_percent or [0.0]
        return {
            "samples": len(lag),
            "lag_median_ms": round(percentile(lag, 50), 1),
            "lag_p95_ms": round(percentile(lag, 95), 1),
            "lag_p99_ms": round(percentile(lag, 99), 1),
            "lag_max_ms": round(max(lag or [0]), 1),
            "cpu_mean_pct": round(statistics.fmean(cpu), 1),
            "cpu_pinned_pct": round(100 * sum(1 for value in cpu if value >= 95) / len(cpu), 1),
            "ram_peak_gb": round(max(self.used_gb or [0]), 1),
        }


def percentile(ordered: list[float], which: int) -> float:
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int(round((which / 100) * (len(ordered) - 1))))
    return ordered[index]


def measure_idle(seconds: float, label: str = "idle (no run)") -> dict:
    with Probe() as probe:
        time.sleep(seconds)
    report = probe.report()
    return {"label": label, "wall_s": round(seconds, 1), "outcome": "-",
            "ambient_cpu_pct": report["cpu_mean_pct"],
            "ambient_lag_p95_ms": report["lag_p95_ms"], **report}


def measure_run(workers: int, reserve: int, extra: list[str],
                settle_seconds: float = 15.0, log_dir: Path | None = None) -> dict:
    ambient = measure_idle(settle_seconds) if settle_seconds > 0 else {}
    command = [sys.executable, str(ROOT / "tools" / "run_tests.py"),
               "--workers", str(workers), "--reserve", str(reserve), *extra]
    started = time.perf_counter()
    with Probe() as probe:
        child = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
        chunks: list[str] = []
        reader = threading.Thread(target=lambda: chunks.append(child.stdout.read()), daemon=True)
        reader.start()
        while child.poll() is None:
            time.sleep(POLL_INTERVAL)
        reader.join(timeout=10)
    output = "".join(chunks)
    counts = dict((kind, int(number)) for number, kind in SUMMARY.findall(output))
    # A RED row that cannot say WHICH test failed is half a finding -- and the
    # question a sweep exists to answer is whether a faster configuration
    # breaks the suite or merely looks like it might. Keep every run's output.
    failed = sorted(set(FAILED_LINE.findall(output)))
    log_path = None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{workers}w-{reserve}r.log"
        log_path.write_text(output, encoding="utf-8")
    return {
        "failed_tests": failed,
        "log": str(log_path) if log_path else None,
        "label": f"{workers} workers, {reserve or 'no'} core(s) reserved",
        "workers": workers, "reserve": reserve,
        "ambient_cpu_pct": ambient.get("cpu_mean_pct", 0.0),
        "ambient_lag_p95_ms": ambient.get("lag_p95_ms", 0.0),
        "wall_s": round(time.perf_counter() - started, 1),
        "exit": child.returncode,
        "outcome": ("green" if child.returncode == 0
                    else f"RED ({counts.get('failed', '?')} failed)"),
        **probe.report(),
    }


def parse_config(text: str) -> tuple[int, int]:
    workers, _, reserve = text.partition(":")
    return int(workers), int(reserve or 0)


def render(rows: list[dict]) -> str:
    header = (f"{'configuration':<34}{'wall':>8}{'lag p95':>10}{'lag p99':>10}"
              f"{'cpu':>7}{'pinned':>8}{'ram':>7}{'ambient':>9}  outcome")
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row['label']:<34}{row['wall_s']:>7.0f}s{row['lag_p95_ms']:>9.1f}ms"
            f"{row['lag_p99_ms']:>9.1f}ms{row['cpu_mean_pct']:>6.0f}%"
            f"{row['cpu_pinned_pct']:>7.0f}%{row['ram_peak_gb']:>6.0f}G"
            f"{row.get('ambient_cpu_pct', 0):>8.0f}%  {row['outcome']}")
        for name in row.get("failed_tests") or []:
            lines.append(f"{'':<34}  {name}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", nargs="*", default=["16:0", "16:8", "8:0"],
                        help="workers:reserved-cores, e.g. 16:0 16:8 8:0")
    parser.add_argument("--idle-seconds", type=float, default=30.0)
    parser.add_argument("--settle-seconds", type=float, default=15.0,
                        help="quiet seconds probed before each run, to record the "
                             "ambient load that run was measured against (0 skips)")
    parser.add_argument("--out", type=Path, default=None, help="write the rows as JSON here")
    parser.add_argument("--log-dir", type=Path, default=None,
                        help="keep each run's full pytest output here, one file per "
                             "configuration (defaults beside --out)")
    args, extra = parser.parse_known_args(argv)

    rows = [measure_idle(args.idle_seconds)]
    print(render(rows), flush=True)
    for text in args.config:
        workers, reserve = parse_config(text)
        log_dir = args.log_dir or (args.out.parent / "runs" if args.out else None)
        rows.append(measure_run(workers, reserve, extra, args.settle_seconds, log_dir))
        print(render(rows), flush=True)
    if args.out:
        args.out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
