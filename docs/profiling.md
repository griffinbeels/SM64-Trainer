# Profiling practice and replay

Profiling is opt-in and expires after at most five minutes. It does not restart
the trainer, change the renderer, alter capture rates, or replace an active
profile. Existing `perf_log.jsonl` remains a memory/resource monitor, not a CPU or
GPU profiler. Capture tools run from the development environment.

## Capture a reproducible workload

Find the actual server listener and confirm `/health`; do not assume the example
port is the running trainer. The running build must include the diagnostics API.

```powershell
uv sync --group profiling
uv run --group profiling python tools/profile_capture.py doctor
uv run --group profiling python tools/profile_capture.py record --url http://localhost:8065 --workload workload.json --variant before --seconds 30 --pid 1234 --pid 5678 --artifact plugin.dll --output data/profiles/before
uv run --group profiling python tools/profile_report.py data/profiles/before
uv run --group profiling python tools/profile_report.py data/profiles/before data/profiles/after --output comparison.json
```

Use actual PIDs for Project64, the trainer, OBS, and browser as needed. Process
CPU percent follows psutil: one fully occupied logical CPU is 100%, so a process
can exceed 100%. System CPU percent spans the machine. Process identity includes
creation time, preventing recycled PIDs from looking like the original process.
The optional artifact arguments hash the actual plugin/build files under test.
Outputs never overwrite an existing capture directory.

`workload.json` is an explicit, reviewable record, for example:

```json
{
  "scenario": "Repeat the same attempt, then open latest replay and loop its first second",
  "rom": "ROM identity or SHA-256",
  "save_state": "Save-state identity or SHA-256",
  "renderer": "Plugin name and version",
  "resolution": "1600x1200",
  "settings": {"capture": "plugin", "audio": "process", "input_rate": "unchanged"},
  "ambient": "OBS streaming with the same scene; no test suite running"
}
```

Use the same workload file, machine, duration, interval, and profiler flags for a
pair. Record the intentional implementation difference in `--variant` and
`--artifact`, not in the supposedly fixed workload. For renderer/capture-mode
isolation, use separately named scenarios and review their individual reports;
the strict automatic comparison deliberately refuses changed configurations.
Repeat matched pairs and inspect ambient CPU, sampling gaps and observer cost.
These are descriptions of observations, not automatic causal claims.

`capture.json` contains metadata, errors, start/final stage histograms and
independent 50 ms scheduler wake samples. `samples.jsonl` contains low-rate system
CPU/memory, selected process CPU/memory/I/O/thread counts, replay status, and
backend snapshots. `observer_ms` measures each polling pass including HTTP time;
it is not CPU overhead. Profile histograms are cumulative within one capture:
the report uses the final histogram and never averages successive percentiles.
Missing data remains unavailable; counter resets and server-session changes
invalidate comparisons. GPU absence is `null`, never zero.

## Whole-desktop lag and graphics attribution

Add `--wpr` to request a bounded memory-mode Windows Performance Recorder trace.
WPR must be installed and may require elevation. The tool checks local named
instance support, starts a UUID-owned `GeneralProfile`, and stops only that
instance. A conflicting recording is not cancelled. `system.etl` opens in Windows
Performance Analyzer (WPA); `doctor` checks availability on PATH but does not
install software. WPR includes system evidence that Python and browser timers
cannot supply. Start with CPU scheduling/stacks, disk I/O and relevant graphics
events; use a targeted graphics profile/tool when GeneralProfile lacks the GPU
event needed for the hypothesis.

Normal completion, Ctrl+C and errors stop owned traces. A forcibly killed capture
process cannot execute cleanup: `wpr-owner.json` records the exact named-instance
stop command for recovery. WPR uses memory mode so such a session cannot fill the
disk. Never run an unnamed `wpr -cancel` to clean up a profiling attempt.

For Python attribution, install py-spy explicitly in the development tooling
environment if `doctor` reports it missing, then add `--py-spy-pid ACTUAL_SERVER_PID`.
This writes a bounded 49 Hz Speedscope recording without restarting the server.
Attachment permissions and interpreter compatibility can fail; inspect
`py-spy.log`. The tool reports failures rather than silently omitting evidence.

Use browser DevTools Performance traces for timeline scripting, layout, paint,
long tasks and video rendering. Export the trace beside the session; do not
mistake scheduler wake delay or backend stage duration for displayed frame time.
Compare profiler-off and profiler-on runs to check observer overhead. Accuracy
still needs decoded video/input-frame/audio tests plus representative live review.

The companion browser capture supports an explicitly supplied CDP debugging
endpoint or an isolated headless browser:

```powershell
uv run --group profiling python tools/profile_browser.py --url http://localhost:8065/ui/ --seconds 30 --output data/profiles/browser-before
```

Use `--cdp-url` to attach to an existing debugging endpoint. `--actions` accepts a
JSON file of `{ "action": "click|wait|press|mark", "value": "..." }` operations;
run `--help` for the command's exact contract. It exports `browser.json` and an
optional Chrome trace. Keep browser captures beside the matching backend
session; the Python comparison report does not merge browser artifacts.
Native graphics profile histograms, when the instrumented plugin
supports them, are retained separately from backend stages. Their CPU wall times
include GPU waits; VI-call intervals are not displayed FPS or GPU engine timing.

Primary references: [WPR command options](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/wpr-command-line-options),
[Windows Performance Toolkit](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/),
[py-spy](https://github.com/benfred/py-spy),
[Chrome Performance panel](https://developer.chrome.com/docs/devtools/performance).
Named-instance syntax was also checked with the installed `wpr -help advanced`:
`-instancename NAME` must be the final argument on every session command.
