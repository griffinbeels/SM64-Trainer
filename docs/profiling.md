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
Each sample also reads `/health` for input skip/mismatch counters. The sample log
has a 64 MiB cap; exceeding it marks the capture incomplete instead of filling
the disk. Record meaningful activity and sufficient capture margins rather than
assuming an empty histogram means zero cost.

## Whole-desktop lag and graphics attribution

Add `--wpr` to request a bounded memory-mode Windows Performance Recorder trace.
WPR must be installed and may require elevation. The tool checks local named
instance support, starts UUID-owned `GeneralProfile` and `GPU` profiles, and stops only that
instance. A conflicting recording is not cancelled. `system.etl` opens in Windows
Performance Analyzer (WPA); `doctor` checks availability on PATH but does not
install software (it also checks the venv and standard Toolkit install directory).
WPR includes system evidence that Python and browser timers
cannot supply. Start with CPU scheduling/stacks, disk I/O and GPU activity.
The installed `wpr -profiles` is queried first; if GPU is absent, the artifact
explicitly records that gap and the report marks GPU trace coverage unavailable.
Native graphics CPU wall times remain a separate measurement from GPU events.

For agent-readable offline summaries:

```powershell
uv run --group profiling python tools/profile_etl.py data/profiles/before/system.etl --output data/profiles/before/etl-analysis
```

This exports CPU/module, CPU/disk, DPC/ISR, hard-fault, process and trace statistics
with xperf, preserving raw reports and a manifest. Missing loss information is
unknown; unresolved symbols are not evidence of no work. GPU tables remain in
the ETL for WPA analysis.

## Browser timeline and playback

`profile_browser.py` imports an otherwise inactive diagnostic module and records
a bounded Chrome Performance trace plus rAF scheduling gaps, long tasks, video
playback quality, waiting/seeking events, API resource timings and action markers.
No UI module imports it during normal use. It never changes playback rate or
seeks on its own. Explicit action files drive the same controls used for practice.

```powershell
uv run --group profiling python tools/profile_browser.py --url http://localhost:8065 --seconds 30 --actions review-actions.json --output data/profiles/browser-before
```

Actions are a list of `{"action":"click","value":"CSS selector"}`,
`{"action":"wait","value":"CSS selector"}`, `{"action":"press","value":"Space"}`,
or `{"action":"mark","value":"description"}`. Waits require visible elements;
each action has begin/end markers. Choose selectors from the actual review state.
Inspect first-picture events and timeline readiness separately; a player shell is
not proof of a usable synchronized replay. Downloaded footage retains its own
frame timing capabilities, never inferred game-frame accuracy.

The default creates an owned headless browser. `--cdp-url http://localhost:PORT`
instead attaches to exactly one existing page matching `--url`, without navigating
or focusing it. That browser must already expose its debugging endpoint; the tool
does not restart Chrome, WebView2, the trainer or Project64. Headless and attached
results are different environments and must not be treated as a matched pair.
Use identical viewport, workload, media, browser version and trace settings.
`--no-trace` runs just the inexpensive observers for an instrumentation comparison.
`chrome-trace.json` opens in Chrome Performance; `browser.json` records summaries.
Hidden-tab time is explicit. rAF gaps are callback scheduling, not display FPS;
video counters are unavailable after a source reset rather than subtracted across
different media. Unsupported long-task APIs and GPU counters remain null.

## Backend coverage and boundary effects

Stages cover poll tick duration/interval, input read and emit, timeline assembly,
capture slot read/decode/conversion, recorder callbacks, video/audio mux, extraction,
frame probing, tail wait, picture association, input audit and total replay view.
These durations include nested work; do not sum them. The collector retains fixed
histograms rather than an unbounded event log. It is disabled by default, needs no
per-frame disk write, and retains only the most recent session. Calls finishing
outside the capture window are censored: pending and late-completion counters make
that visible and invalidate automatic comparisons. Re-run with enough head/tail
room for the operation. Measure profiler-off versus profiler-on as well as before
versus after; profiling itself is an intervention.

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

Keep browser captures beside the matching backend session; the Python comparison
report does not merge browser artifacts. Trace buffer saturation or known event
loss marks a browser capture incomplete. A navigation failure still attempts to
stop the owned trace; existing unrelated traces are never ended.
Native graphics profile histograms, when the instrumented plugin
supports them, are retained separately from backend stages. Their CPU wall times
include GPU waits; VI-call intervals are not displayed FPS or GPU engine timing.

The instrumented wrapper must be built from `plugin/gfxwrap` and packaged using
`tools/build_plugin.py`. Updating the bundle alone does not update the DLL loaded
by Project64. The existing capture setup flow owns installation when the emulator
can safely be closed; never overwrite its loaded plugin or restart a live recording
to obtain a profile. Older wrappers report native profiling as unavailable.

Primary references: [WPR command options](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/wpr-command-line-options),
[Windows Performance Toolkit](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/),
[py-spy](https://github.com/benfred/py-spy),
[Chrome Performance panel](https://developer.chrome.com/docs/devtools/performance).
Named-instance syntax was also checked with the installed `wpr -help advanced`:
`-instancename NAME` must be the final argument on every session command.
