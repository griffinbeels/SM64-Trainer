# Video Replay of Entries — Design Spec

**Date:** 2026-06-11
**Status:** Approved (brainstorm complete, awaiting implementation plan)
**Branch note:** spec committed on `feature/garbage-runs-markers-progress-ui`; implementation gets its own branch.

## Problem / goal

"Wow, that PB was great — let me rewatch it to understand what I did." While the
server runs, continuously record the Project64 gameplay window (video **and**
game audio) into a replay buffer. For any attempt — a star grab *or* a
death/reset — the user clicks **View Replay** and a precisely-cut clip of that
exact attempt plays inline in the tracker UI, with smooth scrubbing. **Save
Replay** persists the clip to a browsable folder tree.

## Requirements (decided in brainstorm)

| Question | Decision |
|---|---|
| Which entries get replays | Star grabs **and** practice attempts (deaths/resets). Both route through attempts: a star grab is an attempt with outcome `success`. |
| Clip bounds | Whole attempt + padding both ends (defaults: 3 s before anchor, 2 s after closing event; configurable). |
| Audio | **Required** from v1 — SM64 practice relies on audio cues (dust/landing sounds). |
| Capture engine | **Fully self-contained** — no OBS, no external apps. Capture/encode/mux inside the Python server. |
| Window mode | PJ64 runs **windowed** (exclusive fullscreen is explicitly out of scope — WGC cannot reliably capture it). |
| Buffer design | **Disk segment ring** — small TS segments + retention deque. "Last 10 min" and "whole session" are retention settings. |
| Save location | Configurable root, default `./replays/<YYYY-MM-DD>/session_<N>/` (gitignored). |
| Playback | Inline `<video>` under the entry; smooth scrubbing via HTTP Range + faststart MP4. |

## Verified stack (researched 2026-06-11)

Full research notes: `~/.claude/agent-memory/frontier-research/windows-capture-recording-stack.md`.

- **`windows-capture` v2.0.0** (PyPI, maintained, Rust-backed) — per-window
  capture via Windows.Graphics.Capture. Captures occluded windows; targets by
  HWND/title; every frame carries a QPC timestamp (`frame.timespan`, 100 ns
  ticks) — the foundation of A/V sync and UTC mapping.
- **`proc-tap` v1.0.3** (maintained) — WASAPI **per-process** loopback by PID:
  only PJ64's audio, no Discord/music bleed. float32 PCM 48 kHz stereo.
  Fallback: `PyAudioWPatch` device-wide loopback.
- **PyAV ≥ 14** — binary wheels bundle NVENC-enabled ffmpeg (`h264_nvenc`),
  gated on NVIDIA driver ≥ 570 (RTX 5090 requires ≥ 570 anyway). Fallback:
  `libx264 -preset ultrafast` (trivial CPU at 480p). Do **not** use PyAV's
  `segment` muxer (long-standing crash bug, PyAV issue #254) — rotate
  containers manually.
- **Starlette `FileResponse`** — native HTTP Range/206 support; this is what
  makes `<video>` scrubbing smooth. Do not serve clips via `StaticFiles`
  (can return 200 instead of 206, breaking seek).

Known risks (none blocking): WGC yellow capture border may need a consent
prompt to suppress (`draw_border=False` — UNVERIFIED, cosmetic only);
long-session A/V drift needs a live check; minimized windows yield stale
frames (degraded state, not failure).

## Architecture

New top-level zone `src/sm64_events/replay/` — completely independent of the
poller/detector pipeline (never touches emulator memory; read-only rule
untouched). One concern per module, composed in `main.py`:

```
replay/clock.py      CaptureClock — QPC↔UTC offset captured once at recorder
                     start. THE bridge: event timestamps (UTC) ↔ stream time
                     (QPC). The single shared contract.
replay/video.py      VideoCapture — windows-capture on the PJ64 window (HWND
                     found via the pid pymem already attaches to). Emits
                     (BGRA frame, QPC ts) into a bounded queue.
replay/audio.py      AudioCapture — proc-tap per-process loopback on PJ64 pid.
                     PCM 48 kHz stereo; audio time advances by sample count.
replay/encoder.py    SegmentEncoder — PyAV, h264_nvenc + AAC, one MPEG-TS
                     container per ~2 s segment, GOP = segment length, closed
                     GOP. Conforms WGC's on-change frames to constant 30 fps
                     by holding the last frame across gaps (pause menus =
                     static frames, correct behavior).
replay/ring.py       SegmentRing — deque of {path, utc_start, utc_end};
                     evicts past retention; disk-cap guard.
replay/extract.py    ClipExtractor — [utc_start, utc_end] → overlapping
                     segments → decode → re-encode → one MP4 (+faststart,
                     ~0.5 s GOP). Clip cache keyed by attempt.
replay/recorder.py   ReplayRecorder — orchestrator: threads/queues, lifecycle
                     (attach-retry like the poller), status, extract().
```

**Data flow:** video and audio callbacks land on their own threads → bounded
queues → one encoder thread muxes both with PTS from the shared CaptureClock
anchor → segment files rotate into the ring in `data/replay_buffer/`. A/V
sync from a shared QPC origin lands well within one frame. Encoding is on the
GPU; the 30 Hz poll loop is unaffected.

## Clip extraction & timestamp mapping

Replays are **attempt-scoped**. An attempt's anchor event (practice reset /
savestate load) and closing event (star grab / death / reset) both carry
`timestamp_utc`:

```
span = [anchor.timestamp_utc − pre_pad, closing.timestamp_utc + post_pad]
```

The attempt view payload (`tracking/views.py`) grows `start_utc` / `end_utc`,
sourced from events the projection already consumes. No new tracking logic.

`ClipExtractor` **decodes and re-encodes** the overlapping segments into one
MP4 rather than stream-copy concatenation — deliberate:

- NVENC at 480p runs many times faster than realtime; even a 4-minute attempt
  extracts in a few seconds.
- Frame-accurate at both edges (stream-copy cuts only on 2 s keyframes).
- Absorbs mid-session window resizes (mixed-dimension segments scale to one
  output size).
- Output gets dense keyframes (~0.5 s) + `+faststart` → instant scrubbing.

**Post-padding race:** a View Replay click within ~2 s of the event waits
(bounded) for the segment covering `span.end` before cutting.
**Truncation:** if retention/buffer-start doesn't cover the span, cut what
exists and return `truncated: true`.
**Cache:** extracted clips land in `data/replay_buffer/clips/` and are reused
on repeat views.

## API surface

Extends the existing `/api` router (`server/api.py`), same error taxonomy:

```
GET  /api/replay/status                  recording? window found? audio mode?
                                         buffer span, disk usage
POST /api/attempts/{id}/replay           extract (or cached) →
                                         { clip_url, duration, truncated }
GET  /api/replay/clips/{clip_id}.mp4     FileResponse (Range/206)
POST /api/attempts/{id}/replay/save      copy to replays folder → { path }
```

## Storage layout

```
data/replay_buffer/        scratch — ring segments + clip cache; wiped on
                           server startup
replays/                   permanent — only what the user explicitly saves
  2026-06-11/
    session_3/
      attempt_0042_bob_koopa-the-quick_1m02s33.mp4
```

Save root configurable (default `./replays/`, gitignored). Date + session
number come from existing session rows. Filenames slugged from course, star,
and IGT (outcome appended for non-success attempts, e.g. `_death`) so the
folder is browsable without the app.

## UI integration

`ui/components/practice.js` + new `ui/components/replay.js`:

- `AttemptRow` actions cell gains **▶ Replay**. Clicking expands a player row
  directly beneath the entry: `<video controls>` on the clip URL, with an
  "extracting…" state first (~1–3 s, then cached).
- Expanded row contains **Save Replay** (shows saved path after POST) and a
  truncation notice when applicable.
- Header gets a recording indicator (● red = recording, grey = window not
  found) fed by `/api/replay/status` — the user knows footage exists before
  they need it.
- The raw event feed stays untouched in v1; star grabs are covered via their
  success-attempt rows in the practice panel.

## Lifecycle & degraded states

Recorder mirrors the poller's attach-retry: PJ64 process + window appear →
capture starts; they vanish → recorder stops cleanly, buffer kept until
server shutdown (replays of the just-ended run still viewable). Startup wipes
scratch.

All degraded states are detected, surfaced via `/api/replay/status`, and
never fatal to the tracker:

| Condition | Behavior |
|---|---|
| PJ64 window not found | Tracker runs normally; replay buttons disabled with tooltip |
| proc-tap build/init fails | Device-wide loopback fallback (status: "audio: system-wide") |
| NVENC probe fails | `libx264 ultrafast` fallback |
| Window minimized | Stale frames; recording continues, status notes it |
| Span partially evicted | Truncated clip + `truncated: true` |
| Disk cap tripped (default 20 GB) | Evict oldest segments regardless of retention |

## Config

One `ReplayConfig` dataclass wired in `main.py`:
`enabled`, `retention` ("session" \| minutes, default "session"), `pre_pad_s` (3), `post_pad_s`
(2), `save_root` (`./replays`), `scratch_dir` (`data/replay_buffer`), `fps`
(30), `max_buffer_gb` (20).

## Testing

Same pattern as `memory/buffer.py`: capture/encode behind Protocols with
in-memory fakes. pytest covers all pure logic without GPU/window/audio:

- CaptureClock math (UTC↔QPC, round-trip)
- Ring eviction, span→segment selection, disk cap
- Truncation + post-padding-race wait logic
- Filename slugging, storage paths
- API endpoints via fakes (status, extract, save, Range serving)

Live verification with the human (existing harness ritual): yellow-border
suppression, A/V sync spot-check, minimized-window behavior, end-to-end
click-to-scrub, long-session drift.

## Dependencies

Via uv: `windows-capture==2.0.0`, `proc-tap==1.0.3`, `av>=14`
(+ optional fallback `pyaudiowpatch`). Python stays 3.12-pinned (proc-tap
native build risk on 3.13).

## Out of scope (v1)

- Exclusive-fullscreen capture (WGC limitation; play windowed)
- Replay buttons on raw feed entries (attempt rows cover the use cases)
- Auto-save on PB (manual Save Replay only; easy follow-up)
- Multi-emulator / multi-window support
