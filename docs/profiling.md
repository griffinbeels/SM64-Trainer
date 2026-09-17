# Profiling practice and replay

Profiling is opt-in and expires after at most five minutes. It does not restart
the trainer, change the renderer, alter capture rates, or replace an active
profile. Existing `perf_log.jsonl` remains a memory/resource monitor, not a CPU or
GPU profiler. Capture tools run from the development environment.

Routine resource monitoring runs every minute using OS memory/process/handle
probes. It does not walk the Python heap, enumerate DXGI adapters or scan replay
files. Those more expensive probes remain available with `SM64_PERFMON_DEEP=1`
before launch; missing measurements stay null/unmeasured. `sampling.mode` and
`sampling.collect_ms` identify the mode and collection/log-formatting cost
(excluding the final file write). Application gauges read cached recorder
scalars; slow probes and persistence run outside the polling event loop, with
one owned sample at a time and completion awaited on shutdown.

GPU capture status includes `frame_source_health.stage_timings`: fixed-size
cold-start, tick, scheduling-gap and nested audio/adapter/media/report aggregates.
Each peak includes monotonic and Unix start times. These inclusive durations
must not be summed; a peak belongs to the current capture run and need not be
the final tick before a failure. Unix peak time is derived from the run's initial
wall/monotonic clock pair. Comparing it across a system clock adjustment needs
care. There is no per-frame diagnostic file logging or emulator-thread timing
work in this collector.

Nested `mux_video`, `mux_audio` and `ledger_feed` stages distinguish packet mux,
AAC work and picture-ledger publication. The independent media sink reports
`frame_source_health.publication.max_feed_ms` and
`max_feed_started_unix_s`, along with pending byte/block counts and errors. Its
final snapshot is logged on retirement. An outer media-drain peak alone cannot
identify disk I/O as its cause; compare these individual stages and timestamps.

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
Routine external samples leave process thread counts unmeasured. On Windows,
psutil obtains that count through a system process-information query; repeating
it for each PID made the observer expensive during R45. Use existing cached server
health counts for context, and always inspect observer_ms before treating a capture
as live performance evidence. This removes that query, not every possible source
of observer overhead or protected-process fallback.
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
instance support, starts a UUID-owned `tools/profiles/replay.wprp` capture, and stops only that
instance. A conflicting recording is not cancelled. `system.etl` opens in Windows
Performance Analyzer (WPA); `doctor` checks availability on PATH but does not
install software (it also checks the venv and standard Toolkit install directory).
WPR includes system evidence that Python and browser timers
cannot supply. The custom profile has a fixed 128 MiB buffer budget for CPU
scheduling/stacks, DPC/ISR and requested graphics events. Built-in profiles scaled
to several GB on the developer machine, so they are no longer the default.
Requested graphics events are not proof of GPU coverage: inspect actual provider
events. The process sampler separately records I/O counters. A zero-loss header
with no sampled CPU data cannot pass the offline profile exporter.
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
`replay.storage_maintenance` measures the complete periodic inventory and eviction
pass, including waiting for its lock. It helps distinguish session-length-related
filesystem work from capture and encoder work.
`replay.probe_native_packets` isolates the packet-index subprocess and validation
inside native extraction. It retains the existing malformed-media checks; adding
this span does not enable an in-process packet-reader shortcut.
`replay.fragment_publish` and `replay.fragment_select` measure indexing/writing
each complete fragment and selecting published ranges in the opt-in archive.
The default fragment recorder uses them. `replay.native_index` measures native
MP4 header construction without payload reads. Selection excludes response file I/O
and browser presentation; a small selection duration is not first-open latency.
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
Native timing comes from the shipped wrapper's own log (below); there is no
separate instrumented build. Never overwrite Project64's loaded plugin or restart
a live recording to obtain a profile.

Primary references: [WPR command options](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/wpr-command-line-options),
[Windows Performance Toolkit](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/),
[py-spy](https://github.com/benfred/py-spy),
[Chrome Performance panel](https://developer.chrome.com/docs/devtools/performance).
Named-instance syntax was also checked with the installed `wpr -help advanced`:
`-instancename NAME` must be the final argument on every session command.


## PJ64 and native plugin logs without a running trainer

`uv run --group profiling python tools/graphics_diagnostics.py --output data/profiles/plugin-check`
collects a read-only snapshot from the actual Project64 process, the wrapper's
control page, and the last 64 KiB of each discovered PJ64/Plugin log (20-file
cap, including `.log.1` rotation). It never creates a capture mapping, renews a
reader lease, starts ETW, writes emulator memory, or changes plugin selection.
Use `--seconds 10` for 4 Hz control-page samples (maximum 30 seconds). After PJ64 has
closed, add `--pj64-dir "C:/path/to/Project64"` to retrieve its retained logs.
The output directory must be new; existing reports are not overwritten.

`sm64_trainer_gfx.log` is the wrapper log; `gliden64.log`, when present, belongs
to the wrapped renderer. Missing logs/errors remain explicit in the report.
Current-process startup records require the PID and timestamp to match the
process lifetime. A loaded DLL may only have been enumerated in the plugin
picker. A saved registry value does not prove which renderer the ROM activated.
The wrapper's build ID is generated by `tools/build_plugin.py` from the native
source bundle, so startup records can identify the candidate used for testing.
The DLL file hash and build ID are different identities: one identifies binary
bytes, the other the native source bundle.

Before a plugin test, add `--expected-wrapper "C:/path/to/the/exact/candidate.dll"`.
The `installation` report compares that candidate with this checkout's bundle
and the installed DLLs. Override the bundle with `--bundled-wrapper` when needed.
It separately compares the current ControlV1 source-build label with the candidate,
requiring a matching PID, process birth and currently mapped wrapper. Missing or
stale runtime evidence stays unknown even when disk bytes match. A build label
does not include compiler flags/toolchain, and the collector does not hash mapped
process memory. Preserve these separate results rather than calling file equality
proof of successful capture.

Installer copies log `capture layer installation committed` only after destination
hash verification and successful settings/configuration writes. The JSON receipt
includes the server PID, source/destination, reason and before/source/after hashes;
the latest receipt is retained in the shared capture-layer settings. Failed copies
restore previous files and record the result. Source servers never automatically
replace DLLs. Packaged servers only refresh bytes matching their last successful
installation receipt; manual changes or unknown history require explicit Install.
This protects external replacements, not release-version ordering or copies made
by older applications still running the old installer implementation.

The native wrapper logs lifecycle events and callbacks taking at least 20 ms,
or callback gaps of at least 50 ms, at most once per five seconds per callback.
Each stall names its total CPU wall time (`total_ms`), the gap since the previous
call and their cumulative maxima; quiet summaries appear once per minute. These
records run on the practice ROM only; any other cartridge forwards untimed. Gaps include pauses,
and these measurements cannot time asynchronous rendering on the GPU.
`dropped_records` reports lost diagnostics. A bounded, event-driven writer keeps
file I/O off emulator callbacks and rotates the log at 1 MiB to `.log.1`.
Logging works with the trainer closed and does not request capture. Abrupt
process exit can lose queued records; absent records are not proof of fast work.

The 2026-09-10 stutter review exposed a failed registry-only A/B: the registry
read back the requested direct renderer while PJ64's picker still selected the
wrapper. That run cannot exonerate the wrapper. The user's subsequent manual
selection plus PJ64 restart reproduced smooth direct GLideN64 and repeated
stutter with the wrapper. Keep that evidence distinct from callback timing:
regular UpdateScreen calls do not certify smooth physical presentation.
## Capture coordinator versus media sink

The GPU runtime wrapper also emits `gpu_summary`, `gpu_summary_phase` and
`gpu_summary_cadence` records every five seconds while capture is healthy. These
are recent windows, separate from the cumulative `gpu_failure` diagnostics.
Read a copied native log with `python tools/native_capture_report.py
PATH_TO_LOG --output report.json`; this reader never contacts PJ64 or opens GPU
objects. It retains at most 120 windows from the last 4 MiB. PID, epoch and QPC
window identity prevent dropped headers from mixing measurements between windows.

Phase durations measure delivery-worker wall time, including any driver waits.
Source cadence comes from the existing immutable boundary timestamps; it is not
physical display FPS. Missing phases remain unknown, and overlapping durations
must not be summed as CPU time. `previous_logging_ms` measures the preceding
summary callback's cost; `dropped_records` exposes the bounded logger's losses.
Installing a new wrapper is necessary to obtain these records from an older live
process.

Newer summaries also contain `snapshot_bytes` and `bridge_bytes`: current logical
texture payloads, not total VRAM or driver allocation. `sample_calls`,
`sample_reuses` and `publish_busy` are cumulative within a capture epoch.
Compare their deltas only within the same PID/epoch. Busy publication should
increase reuse without resampling that immutable source image. The passive
reader exposes these as `resources`; absent fields remain null, not zero.
For a long-session test, retain an early and a late log copy before rotation
overwrites the early windows. Stable logical bytes cannot exonerate driver-side
memory growth, and source cadence cannot establish smooth display presentation.

For the separated GPU sink, `frame_source_health.stage_timings.sink_prepare`
measures cold codec/filter/format work before native picture admission. Compare
`media_setup`, `adapter_pump`, `tick` and `between_ticks` against that value;
`mux_audio`, `mux_video` and `ledger_feed` now execute on the downstream worker.
Their wall times can overlap capture and must not be summed as single-thread CPU.

`frame_source_health.publication.kind="media"` identifies the new sink. Its
`pending_bytes`/`pending_blocks` include in-flight work; `peak_bytes`, `peak_blocks`
and `oldest_ms` expose bounded queue pressure. `slowest_command` labels bind, row,
PCM or video for the existing `max_feed_ms`/UTC peak fields. Those fields now
measure whole sink commands, not just disk writes. Delivery counts advance after
successful mux calls. Session failures log these statistics after retirement.

`tests/test_gpu_cadence.py` supplies the autonomous actual-source/encoder negative
control missing from the older command-driven GPU fixture. Its deliberate mux
delay is a correctness/backpressure test, not a whole-machine performance score.
