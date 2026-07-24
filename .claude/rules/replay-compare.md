---
paths:
  - "src/sm64_events/replay/**"
  - "src/sm64_events/compare/**"
  - "src/sm64_events/core/recorder_lock.py"
---

# Replay, compare, compilation — where to change what

| To change... | Edit |
|---|---|
| Replay orchestration (attach loop, source wiring, ring, idle gate) | `replay/recorder.py` + player-input tap `replay/activity.py`; `replay/clock.py` is THE QPC↔UTC contract. Routes BOTH streams into the single AV sink (`_on_frame`→`submit`, `_on_pcm`→`submit_audio`); the in-process `SegmentWriter` is a no-ffmpeg fallback only. Idle THROTTLES capture grabs (`is_idle` → `video.set_idle_check`) AND discards segments; the ring byte-cap is free-disk-gated (`ring.effective_cap`) |
| Video capture (DWM surface primary; GDI/WGC fallbacks) | `replay/video.py` + `replay/_dwm.py` — docstrings carry the PJ64 capture pathology and the no-user32-on-grab-thread rule; `grab_period` trickles grabs to 8 Hz while idle (kills the ~2 GB/s frame-alloc churn; ffmpeg feeder untouched so resume stays seamless); active capture oversamples (fps×2 DWM / fps×1.5 GDI) — a system-lag suspect, see docs/architecture.md. PJ64 window locate in `replay/window.py` (`find_window`/`pick_window`/`WindowInfo`, wired via `main.py` `window_finder=`) |
| A+V single mux (ffmpeg subprocess) | `replay/ffmpeg_sink.py::FfmpegAvSink` — ONE ffmpeg muxes video (stdin) + audio (named pipe) on ONE wall-clock: `-use_wallclock_as_timestamps` before each input, `-fps_mode cfr` video, `-af aresample=async=1` audio → combined A+V MPEG-TS segments. THIS is the two-clock-drift fix (memory `replay-av-drift-two-clocks`); feeder pacing is not load-bearing. Segment→UTC = anchor-at-first-frame + CSV offset RELATIVE to the first segment. In-process fallback (no ffmpeg, legacy two-clock): `replay/encoder.py`. Encode QUALITY comes from `replay/config.py::video_quality_args`, not here |
| GC policy (stop-the-world watchdog + gen-2 idle collector) | `replay/_gcwatch.py` — freezes the startup heap, disables AUTO gen-2, runs manual `gc.collect(2)` during idle with a 5-min never-idle backstop; `arm(is_idle=recorder.is_idle)` from `server/app.py` lifespan |
| Audio (endpoint-by-pid, RT-safe pump, deaf-stream watchdog) | `replay/audio.py` + `replay/_system_audio.py` — `AudioPump` is a PURE RT-safe handoff (device PCM → `recorder._on_pcm` → sink's audio pipe; no sample-count placement / silence injection — ffmpeg's wallclock+aresample own timing). Per-app endpoint resolution + deaf-stream self-heal |
| Clip extraction (ffmpeg cut of A+V segments) | `replay/extract.py` — pure ffmpeg cut: `concat:` the covering segments, accurate-seek the span, re-encode video (0.5s GOP) + faststart, `-c:a copy`. Coverage holes clamp to the contiguous run containing the span start (`contiguous_run` / `_joinable` — a FRAME-SIZE change breaks a run too). Segments carry their `dims` (`SegmentInfo`, stamped per ffmpeg child). Quality from `replay/config.py::video_quality_args` — the cut must be TRANSPARENT w.r.t. its segments |
| Encode quality (ring sink + clip cut) | `replay/config.py` — `VIDEO_CQ`/`VIDEO_CRF` + `video_quality_args(codec, stage, maxrate)`, THE registry both encoders read. Constant-quality, never a bare bitrate (the "blurry recording" bug: extractor with no rate control fell back to ffmpeg's ~2 Mbps default, fixed 2026-07-23). Raising quality costs ring disk — `VIDEO_CQ` is the one knob |
| Clip save/extract/settings orchestration | `replay/service.py` — `ReplayService`: attempt→span→clip→save + user settings + `available_attempt_ids()`; same LookupError/ValueError/RuntimeError→HTTP taxonomy as api.py |
| Single-RECORDER guard (machine-wide) | `core/recorder_lock.py` — fixed temp-dir file lock so only ONE instance captures PJ64, even across exe + dev servers in separate worktrees (different data dirs → per-db instance_lock does NOT coordinate them; two recorders doubled GPU/encode/audio load, live 2026-06-15). `recorder.py` acquires in `_begin_capture` (can't → viewer-only, auto-takeover next attach cycle), releases in `_teardown_capture`. Injectable (`recorder_lock_factory`); tests redirect the path via conftest |
| Side-by-side compare (import jobs, CRUD, view, serve) | `compare/service.py` — composes `compare/importer.py` (yt-dlp/copy/upload-bytes → ffmpeg-normalize → content-addressed cache in `core/paths.compare_cache_dir()`, dedup = "load once"; atomic publish via `.tmp-<name>`+os.replace) + `storage/db.py` comparisons (migration v10) |
| Failure compilation (build + job) | `replay/compilation.py` — `CompilationBuilder` reuses `ClipExtractor` per window then ONE concat-filter ffmpeg pass scales+pads to a common canvas (probed from the finale clip) and re-encodes to one MP4 (single clip → copy); `CompilationService` runs it as a polled daemon-thread job (mirrors `CompareService`), output → `compilations_dir()` |

## Recipe: add a user-visible replay setting

Bounds row in `SETTINGS_LIMITS` + plumb `validate_settings`/`save_settings`/
`apply_settings_file` (replay/config.py) → live-apply + getter in
`ReplayService.update_settings`/`settings()` → field on `SettingsBody`
(server/replay_api.py) → input in the recording-dot panel
(`ui/components/replay.js` BufferSettings) → README settings lines → tests in
test_replay_{config,service,api}.py. Mirror commits 69bb83d / 29fd542.
Settings persist in `data/replay_settings.json` (a JSON overlay beats a db
migration for scalars); corrupt/out-of-range files lose to defaults so the
server always starts.
