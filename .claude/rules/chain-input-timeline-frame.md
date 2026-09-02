---
paths:
  - "src/sm64_events/inputs/sampler.py"
  - "src/sm64_events/inputs/store.py"
  - "src/sm64_events/inputs/track.py"
  - "src/sm64_events/inputs/service.py"
  - "src/sm64_events/replay/recorder.py"
  - "src/sm64_events/replay/ledger.py"
  - "src/sm64_events/replay/ffmpeg_sink.py"
  - "src/sm64_events/replay/extract.py"
  - "src/sm64_events/replay/frameclock.py"
  - "src/sm64_events/replay/mapalign.py"
  - "src/sm64_events/replay/padread.py"
  - "src/sm64_events/replay/service.py"
  - "src/sm64_events/ui/frame.js"
  - "src/sm64_events/ui/components/inputtimeline.js"
  - "src/sm64_events/ui/components/attemptdrawer.js"
  - "src/sm64_events/ui/components/controllerpanel.js"
---
# Chain: which game frame the paused video picture shows, and the pad the panel draws for it

Read this before editing any module named below. A symptom is seen at the
SINK; its cause sits at the FIRST hop where the value stops being true, and a
fix downstream of that hop cannot hold. Before any fix: run the counterfactual
(force the known-good value in at a hop, re-run to the sink, see whether the
symptom vanishes) — the earliest hop whose correction prevents the failure is
the one to fix. Walk the table only when the counterfactual is ambiguous.

- **Value:** the game frame a video picture shows, and therefore the pad the input timeline's panel draws beside that picture.
- **Source truth:** Usamune's own input display, painted INTO every picture (his ruling: the screen is the truth); the pad per game frame in RAM, read by the sampler at 250 Hz (`InputSampler.health()["edge_mismatches"]` is its self-check).
- **Sink:** the panel under the timeline (FRAME n / N, the stick box, the button chips) on the picture the `<video>` is presenting.
- **One clock:** there is none end to end, and that is the whole problem. Hops 1-3 run on the GAME frame counter; hops 4-6 run on wall-clock capture time and a 60 Hz encode grid; hop 7 runs on the browser's `mediaTime`. Hop 5 (the frame map) is the only join, and it is inferred, not stamped.

| # | hop | value is true here as | module | probe (reads it) | inject (forces it) | when the hop is broken, the probe shows | when the probe itself is broken, it shows |
|---|-----|-----------------------|--------|------------------|--------------------|------------------------------------------|--------------------------------------------|
| 1 | the pad in RAM, per game frame | `InputFrame(stick_x, stick_y, buttons)` filed under the gGlobalTimer value read in the same poll | `src/sm64_events/inputs/sampler.py` | `uv run python tools/probe_inputs.py` (live) / `InputSampler.health()["edge_mismatches"]` | none -- domain rule 6 forbids writing emulator memory | `edge_mismatches` > 0, or a `skips` count naming frames nobody read | a green health payload from a server attached to nothing |
| 2 | the chunk store, then the track | the same frames, trimmed to the attempt: the run ends the frame BEFORE the first dance action, `first = last - (igt - 1)` | `src/sm64_events/inputs/store.py`, `src/sm64_events/inputs/track.py` | `uv run python tools/inspect_timeline.py --attempt N --frames a-b` (prints the pad per panel frame) | append edited frames with `db.inputs.append` (the shape `tests/test_inputs_track.py` uses) | frame 0 landing before the reset was pressed (5534: the fall counted as the dance; four frames early) | a track read from a sibling worktree's journal |
| 3 | the timeline payload | `runs` on the capture axis (zero-based at the clip span's first captured frame), `lead_frames`, `attempt_frames`, `stretches` -- the service's `timeline` call | `src/sm64_events/inputs/service.py` | `InputsService(...).timeline(id, span=(lo, hi))` offline, then `run_at(axis)` | none -- pure over hop 2, so hop 2's injection reaches it unchanged | a run whose `stick_x` differs from hop 2's at the same raw frame | a payload built without the clip's span (the attempt alone; a different axis) |
| 4 | the pictures the recorder grabbed, and their encode | each distinct grabbed picture stamped with composition time + the RAM frame (the picture ledger); ffmpeg re-times them onto a 60 Hz CFR grid on the wall clock | `src/sm64_events/replay/recorder.py`, `src/sm64_events/replay/ledger.py`, `src/sm64_events/replay/ffmpeg_sink.py`, `src/sm64_events/replay/extract.py` | `ffprobe -show_frames` on the clip (pts per frame; `start_time`); `tools/inspect_timeline.py --audit` (row rate, torn pairs) | none -- a picture that was not grabbed cannot be invented | 1 slot / 2 slots / 3 slots per picture jittering (5782: 95 / 359 / 92 -- 17% singles and 17% triples against ~1% expected); a clip whose first frame's pts is not 0.000 (5782: 0.011003) | a probe reading the ring segment instead of the extracted clip (segments start at pts 1.4) |
| 5 | the frame map: video slot -> game frame | the ledger's rows matched to the clip's picture runs by time, quantised to one answer per picture, then PINNED by the pad reader to what Usamune drew (digits; the reset's white flash; button icons when their templates exist) | `src/sm64_events/replay/frameclock.py`, `src/sm64_events/replay/mapalign.py`, `src/sm64_events/replay/padread.py`, `src/sm64_events/replay/service.py` | `uv run python tools/score_pad_read.py --attempt N --disagreements` -- BUT SEE THE CATALOGUE: its agreement number is circular against the map the reader itself aligned | corrupt a stored map by +2 inside a hold and re-run `read_clip` (T1 below) | the corrupted map returned UNCHANGED while the verdict stays bit-identical (measured 2026-09-01: 97 slots wrong, verdict identical) | 100% on a clip with the display off (it refuses instead), or a `sure` larger than the slot count (rows were counted, not frames) |
| 6 | the clip's timestamps as the browser sees them | frame k presents at `mediaTime = pts_k`, which is `k/60 + video_start_s` -- the cut's `-avoid_negative_ts make_zero` leaves the video's first pts at the cut's sub-frame remainder, and `video_start_s` carries it to the view | `src/sm64_events/replay/extract.py`, `src/sm64_events/replay/service.py` | `uv run python tools/probe_clip_seek.py --attempt N --slots a-b` (serves the clip to headless Chromium, seeks with the shipped `src/sm64_events/ui/frame.js` both the old way and from `video_start_s`, reads `mediaTime` back) | that probe's `fixed` column IS the injection: the seek with `video_start_s` added | seeking to the timeline's own time for slot k presents slot k-1 (measured on 5782: every seek lands one picture early; the corrected seek lands on k) | a server without Range support: every seek snaps to 0 and reads as "the clip cannot seek" |
| 7 | the panel's lookup | `slot = floor((mediaTime - video_start_s) * clipFps)` (`frame.js::slotAtTime`), `raw = frame_map[slot]`, axis via `stretches`, pad = the run containing it | `src/sm64_events/ui/frame.js`, `src/sm64_events/ui/components/inputtimeline.js`, `src/sm64_events/ui/components/controllerpanel.js` | drive the real page (uilab) and read `.input-inspector-frame` + `.stick-value` on a paused picture; compare with hop 6's `mediaTime` | set `frameMap`/`clipFps` props by hand in the fixture | the readout one frame behind the picture on screen (his 5782 screenshot: picture R2, panel 157 / "--") | the fixture's synthetic view (its map is 2 slots per frame from 30 fps, not a real clip) |

## Counterfactual recipe

For a report of the shape "the screen shows X, the panel shows Y on the same
paused picture":

1. Hop 3: build the payload offline with the clip's span and read the run at the
   panel's axis. If it holds Y where hop 2 holds X, the fault is the run
   collapse or the axis; stop there.
2. Hop 5: `tools/score_pad_read.py` on the clip -- but read its DISAGREEMENTS
   and the reader's own cells at the slots around the picture, not its
   percentage. Decode those slots with `-frames:v` capped (an uncapped `trim`
   printed 501 tiles and an off-by-one that was the probe's, not the clip's).
3. Hop 6: `tools/probe_clip_seek.py --attempt N --slots a-b` around the
   picture. If Chrome presents slot k-1 for the old seek meant for k and the
   corrected seek presents k, the fault is the clip's first pts and the
   panel's slot arithmetic, and no reader fix can hold. `ffprobe -show_frames
   -read_intervals` prints the pts themselves when the probe's numbers need
   a second witness.

## Failure catalogue

- 2026-09-01 hop 2 — "frame 0 is before the reset... frame 3 is the first frame I actually reset on" — the run ended at the first grab ACTION (a midair fall), so `first` landed four frames before the reset press — fixed in `track.py` (the dance's first frame ends the run; the fall is timed).
- 2026-09-01 hop 7 — "84 2 on screen, 84 in our tool, yet screen-checked 100%" (5782) — the map is RIGHT (slot 496 = frame 157 = R2, sharp minimum at shift 0); Chrome presents slot k-1 for the timeline's seek to k because the clip's first pts is 0.011 s (2/3 of a slot) and the seek targets `(k+0.5)/60`; the panel then reads the presented frame honestly and shows the earlier pad — fix belongs at hop 6 (zero the video's first pts at extraction, and carry `start_time` to the panel for older clips), never in the reader.
- 2026-09-01 hop 5 — the METER: `score` counts the objective `align` minimised, so it returns the same number for a correct map and one corrupted by +2 inside an evidence-free hold (fresh-context review, T1). An honest meter reports agreement, COVERAGE (the display can distinguish only ~17% of frames from a neighbour — holds dominate) and EXPOSURE (the longest unpinned run), computed against the map the reader did NOT align.
- 2026-09-01 hop 4 — the structural finding: one video frame per distinct captured picture, stamped with the ledger's RAM frame at encode time, would make the map an identity plus one offset (item 38's approved recorder change, never built). Everything downstream is recovering an identity capture threw away.
- 2026-09-01 hop 6, the FIX itself -- `tools/probe_clip_seek.py` shipped and its first run on 5782 printed `first video pts 0.000000` with the corrected seek still one early: `video_start_of` built the ffprobe path by replacing `ffmpeg` in the whole path, which renamed the install folder (`D:/ffmpeg/bin/ffmpeg.EXE` -> `D:/ffprobe/bin/ffprobe.EXE`) and fell to the 0.0 fallback -- the shipped fix would have changed nothing on his machine. Fixed by file-name replacement + PATH fallback (`ffprobe_beside`). The lesson is the one the probe column exists for: a fix at the right hop still needs its probe run against it, because a fallback that returns the old assumption is indistinguishable from the fix working on a clip that never needed it.
