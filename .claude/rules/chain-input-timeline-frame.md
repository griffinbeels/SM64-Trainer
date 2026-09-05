---
paths:
  - "src/sm64_events/inputs/sampler.py"
  - "src/sm64_events/inputs/store.py"
  - "src/sm64_events/inputs/track.py"
  - "src/sm64_events/inputs/service.py"
  - "src/sm64_events/replay/recorder.py"
  - "plugin/gfxwrap/gfxwrap.c"
  - "src/sm64_events/replay/pluginsource.py"
  - "src/sm64_events/replay/framestream.py"
  - "src/sm64_events/replay/oracleread.py"
  - "src/sm64_events/replay/ledger.py"
  - "src/sm64_events/replay/ffmpeg_sink.py"
  - "src/sm64_events/replay/extract.py"
  - "src/sm64_events/replay/feedmap.py"
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
fix downstream of that hop cannot hold. Before any fix, run the counterfactual
(force the known-good value in at a hop, re-run to the sink, see whether the
symptom vanishes) — the earliest hop whose correction prevents the failure is
the one to fix.

- **Value:** the game frame a video picture shows, and therefore the pad the input timeline's panel draws beside that picture.
- **Source truth:** the frame counter THE CAPTURE LAYER (`plugin/gfxwrap`) copied out of RDRAM inside Project64 at the ProcessDList that drew the picture. The ledger row says `exact` and nothing downstream infers.
- **The independent witness:** THE ORACLE (`replay/oracleread.py`, `tools/score_oracle.py --attempt N`) — the frame number Usamune's HUD memory display of `0x8032D5D4` prints into the picture, read map-free through the +1 rule. It never ships to a user; it is how any claim about the map is settled.
- **Sink:** the panel under the timeline (FRAME n / N, the stick box, the button chips) on the picture the `<video>` is presenting.
- **One clock:** the picture feed puts hops 3-6 on the pictures' own composition times — seconds since the run's spawn, carried in the clip as `frame_times` — so a frame's time in the browser IS the time the ledger stamped it.

**There is exactly one map path, and it is a read.** A clip whose rows are not
stamped carries `frame_map: None`, and the panel says "Frame-exact capture is
off. Set up" rather than showing a derived guess. That is his standard for this
surface: *"We need 100% accuracy for this. If it's wrong even once, then it
can't be relied on as a tool"* (2026-08-23).

| # | hop | value is true here as | module | probe (reads it) | inject (forces it) | when the hop is broken, the probe shows | when the probe itself is broken, it shows |
|---|-----|-----------------------|--------|------------------|--------------------|------------------------------------------|--------------------------------------------|
| 1 | the pad in RAM, per game frame | `InputFrame(stick_x, stick_y, buttons)` filed under the gGlobalTimer value read in the same poll | `src/sm64_events/inputs/sampler.py` | `uv run python tools/probe_inputs.py` (live) / `InputSampler.health()["edge_mismatches"]` | none — domain rule 6 forbids writing emulator memory | `edge_mismatches` > 0, or a `skips` count naming frames nobody read | a green health payload from a server attached to nothing |
| 2 | the chunk store, then the track | the same frames, trimmed to the attempt: the run ends the frame BEFORE the first dance action, `first = last - (igt - 1)` | `src/sm64_events/inputs/store.py`, `src/sm64_events/inputs/track.py` | `uv run python tools/inspect_timeline.py --attempt N --frames a-b` | append edited frames with `db.inputs.append` (the shape `tests/test_inputs_track.py` uses) | frame 0 landing before the reset was pressed (5534: the fall counted as the dance) | a track read from a sibling worktree's journal |
| 3 | the timeline payload | `runs` on the capture axis (zero-based at the clip span's first captured frame), `lead_frames`, `attempt_frames`, `stretches` | `src/sm64_events/inputs/service.py` | `InputsService(...).timeline(id, span=(lo, hi))` offline, then `run_at(axis)` | none — pure over hop 2, so hop 2's injection reaches it unchanged | a run whose `stick_x` differs from hop 2's at the same raw frame | a payload built without the clip's span (the attempt alone; a different axis) |
| 4 | the pictures, each STAMPED with the frame that drew it | the wrapper plugin copies the tracker's address table out of RDRAM at the ProcessDList that draws frame N, reads the picture at the VI whose VI_ORIGIN changed (GL_FRONT directly, else the wrapped plugin's own ReadScreen — GLideN64_LINK_4.2 renders on a thread of its own, so for him it is always the latter), and publishes (picture, stamp) into the frame stream. `PluginVideoSource` decodes the stamp with the sampler's own decoder; the recorder files the row `exact: true` with `frame`, `pad`, `mario`, `igt_overall`. A grab with NO stamp is recorded by time only and names no frame. The sink then writes each picture ONCE into a NUT stream at its composition time, files the write in the feed log, and ffmpeg encodes passthrough — one video frame per picture at its own time (VFR), audio in the same stream on the same clock | `plugin/gfxwrap/gfxwrap.c`, `src/sm64_events/replay/framestream.py`, `src/sm64_events/replay/pluginsource.py`, `src/sm64_events/replay/recorder.py`, `src/sm64_events/replay/ledger.py`, `src/sm64_events/replay/ffmpeg_sink.py` | `/api/replay/status` `frame_source` + `frame_source_health`; the sidecar's `picture_ledger` rows; `uv run python tools/score_oracle.py --attempt N`; `tests/test_gfxwrap_host.py` drives the plugin with no emulator | the host's `--drive` writes a known counter into fake RDRAM and a known colour into the picture: the stamp and the pixels must come back equal | rows without `exact`, or `plugin_inexact_rows` climbing (a present that saw zero or two display lists) | a probe reading the ring segment instead of the extracted clip (segments start at pts 1.4) |
| 5 | the frame map: video slot -> game frame | `feed_map` matches frame k (at `start + frame_times[k]`) to the feed entry written at that moment, and the entry's row names the game frame; the map is `stamp - PLUGIN_PICTURE_LAG` (1: the picture the layer grabs at the VI whose origin changed IS the list before the one it just stamped — SM64 presents the buffer it rendered the iteration before), and `None` on an inexact row. `_take_the_stamps` then sets `frame_map_source: plugin`, fills `picture_igt` from the row whose frame the map names, and runs the pad-stamp audit | `src/sm64_events/replay/feedmap.py`, `src/sm64_events/replay/service.py` | sidecar `feed_match` (frames / matched / bias / residual) and `pad_stamp_agreement`; `uv run python tools/score_picture_offset.py --attempt N` sweeps the picture-to-stamp offset on two channels | hand `feed_map` a projector returning a known wrong value; the map must carry it verbatim, since nothing downstream corrects it | a peak off 0 in the offset sweep on TWO clips (the bar for touching `PLUGIN_PICTURE_LAG`), or `feed_match.matched` far below `frames` | the oracle scoring a clip whose HUD memory display was off, which reads as unreadable rather than as wrong |
| 6 | the clip's timestamps as the browser sees them, and the panel's lookup | frame k presents at `mediaTime = frame_times[k]`, every frame's own pts read off the cut (ffprobe) and carried in the view; then `slot = slotAtTime(mediaTime, clock)` — `frame.js::clipClock` builds the clock from the view — `raw = frame_map[slot]`, axis via `stretches`, pad = the run containing it | `src/sm64_events/replay/extract.py`, `src/sm64_events/ui/frame.js`, `src/sm64_events/ui/components/inputtimeline.js`, `src/sm64_events/ui/components/controllerpanel.js` | `uv run python tools/probe_clip_seek.py --attempt N --slots a-b` (serves the clip to headless Chromium, seeks with the shipped clip clock, reads `mediaTime` back); then drive the real page (uilab) and read `.input-inspector-frame` on a paused picture | that probe's `fixed` column IS the injection: the seek through the clip clock | a seek for slot k presenting k-1 (5782: the clip's first pts was 0.011 s), or the readout one frame behind the picture on screen | a server without Range support: every seek snaps to 0 and reads as "the clip cannot seek" |

## Counterfactual recipe

For a report of the shape "the screen shows X, the panel shows Y on the same
paused picture":

1. **Hop 5 first, with the oracle.** `tools/score_oracle.py --attempt N` reads
   the frame the game itself printed into each picture and compares it with the
   map. `off_by {0: N}` with 0 contradicted means the map is right and the
   fault is downstream; anything else names the map. Needs Usamune's HUD memory
   display of `0x8032D5D4` on.
2. **Hop 5's convention.** `tools/score_picture_offset.py --attempt N` sweeps
   which frame's pad each picture shows, on the stick digits and the button
   icons independently. A peak at 0 on both channels means
   `PLUGIN_PICTURE_LAG` is right for this clip.
3. **Hop 3.** Build the payload offline with the clip's span and read the run
   at the panel's axis. If it holds Y where hop 2 holds X, the fault is the run
   collapse or the axis; stop there.
4. **Hop 6.** `tools/probe_clip_seek.py --attempt N --slots a-b` around the
   picture. If Chrome presents slot k-1 for the seek meant for k, the fault is
   the clip's first pts and the panel's slot arithmetic, and no map change can
   hold.

## Failure catalogue

- **2026-09-05, THE ORACLE DIED IN THE CLEANUP AND ITS TESTS STAYED GREEN.**
  `oracleread.register` reached `BOX_H`, `BOX_W` and `_box_score` through
  three LAZY imports of `replay/timerread.py`, so deleting that module left
  the witness this whole chain leans on raising `ModuleNotFoundError` on
  every clip -- while `tests/test_oracleread.py` passed, because all nine of
  its tests were pure functions over lists and none touched a pixel. Two
  rules come out of it, both already written down in this repo and both
  broken here: an instrument that certifies everything else may not depend on
  anything a cleanup can remove (it owns those three constants now), and a
  suite that covers only the easy half of an instrument is a green light for
  the half that matters. `test_the_reader_can_actually_REGISTER_and_read_a_
  synthetic_row` now paints a row from the shipped alphabet at the reader's
  own geometry and drives registration, box extraction and matching with no
  clip and no ffmpeg; mutation-proved by moving `BOX_H`. THE HABIT that would
  have caught it in seconds: run the instrument on a real clip after touching
  anything near it, and READ its first line.
- **2026-09-05, THE THREE CERTIFIED CLIPS — the evidence the deletion rests
  on, and the ones to re-run against.** He played Shoot into the Wild Blue
  (attempt 7267), A-Maze-Ing Emergency Exit (7285) and Inside the Ancient
  Pyramid (7307) with the HUD memory display on, through a freshly set-up
  capture layer. `score_oracle`: 459/459, 499/499 and 758/758 oracle-known
  slots EXACT, 0 contradicted, 0 bridged, `off_by {0}` on all three.
  `score_picture_offset`: peak at 0 on both channels, button icons 91/91,
  116/116 and 259/259. `pad_stamp_agreement`: 328/328, 436/436 and 718/718
  pictures. `feed_match`: 476/477, 542/548 and 835/841. The pad READER on the
  same clips: 82%, 83% and 94%, with 66/56/30 of its own misreads — which is
  what retired it. Their clips and sidecars are cached under
  `data/replay_buffer/clips/`; any change to hops 3-5 should be scored against
  all three before it is believed.
- **2026-09-05, THE DELETION — one map path, because four derived ones each
  failed on the next clip.** Before the capture layer nothing told us which
  frame a picture showed, so five modules recovered it: `replay/frameclock.py`
  (the wall-clock series v1/v2, plus PJ64's host present counter as v4 via
  `memory/present.py`, hunted by signature every session and read on the 250 Hz
  poll tick), `replay/mapalign.py` (a per-glyph ink aligner, a windowed Viterbi
  path, a learned anchor store, a digit refit, the picture-run quantiser),
  `replay/pixelmap.py` (map v3, built and never wired), `replay/timerread.py`
  (the on-screen clock joined to a RAM counter pair), and the pad reader's own
  alignment. Each was verified live on the clip that produced it and wrong on
  the next — four hand-set constants in a row (-2 slots, +1, -3, +1) — and his
  verdict at the end of that road: *"Totally desynced now, it's even worse than
  when we started trying to improve this."* The layer makes the frame a READ,
  certified by the oracle at 1,716 of 1,716 readable slots across three clips
  (0 contradicted, `off_by {0}`), so all five were deleted along with ~5,000
  lines of their tests and eleven probe/score tools. **Do not rebuild any of
  them.** A clip with no stamps gets no map; that is the correct answer, not a
  gap to fill.
- **2026-09-05 hop 5, WHY `PLUGIN_PICTURE_LAG` IS 1.** MEASURED twice, and the
  second measurement is the one that explains it. First, an offset sweep on
  clip 7015 over two channels with the map as handed: the stick digits scored
  -1 at 85.8% against 66.8% at 0, and the A-button icon (templates learned
  under offset-0 labels, so the test leaned AGAINST the answer) scored 352 of
  352 lit slots at -1 against 311 at 0. Then the oracle settled the mechanism:
  the printed gGlobalTimer equals stamp - 1 on 1,863 of 1,864 readable slots
  across 7049/7090/7116, because SM64 presents the buffer it rendered the
  iteration before (`display_and_vsync` swaps
  `gFrameBuffers[sRenderedFramebuffer]`, then `gGlobalTimer++`). It is the
  game's own one-frame display latency, not padding. A picture's pad, IGT and
  Mario state are therefore the PREVIOUS row's. The bar for ever moving this
  constant: a peak off 0 in `score_picture_offset` on TWO clips, plus an oracle
  run that agrees.
- **2026-09-05 hop 4, THE FOURTH CLIP DISAGREED BY ONE MORE — still open.**
  Clip 7141, recorded at a different window size so the wrapped plugin's render
  cost differed, peaked at -1 on both channels where three others peaked at 0
  under the same constant. Which buffer the layer grabs depends on where
  GLideN64's render thread sits relative to the emulation thread, which is a
  timing rather than an order. The measurement owed: an import-table hook on
  the wrapped plugin's `SwapBuffers` counting swaps per capture, which turns
  the pairing into a number per picture and — since the hook runs on the render
  thread with the context current — is also where a capture could read the
  buffer being presented, paired to its list by queue order rather than by VI
  timing. **No constant moves until then**: a constant that is right on three
  clips and wrong on the fourth is the history this chain exists to end.
- **2026-09-05 hop 4, THE CAMERA CHOICE IS REVISITED.** The recorder attaches
  to PJ64's window before the ROM runs, so it can choose the desktop grab while
  the layer is still silent. `DesktopUntilLayerPresents` watches the heartbeat
  and ends itself like a lost window the moment a picture flows, so the attach
  loop's next factory call gets the plugin — the order he opens things in must
  not matter. `/api/replay/status` `frame_source_note` says why while a loaded
  layer refuses (asked again every 15 s; a layer whose heartbeat moved while it
  refused every picture held an empty ring for an hour).
- **2026-09-04 hop 4, THE PLUGIN'S FRESH-CONTEXT REVIEW.** Four faults the test
  host could not see, each now driven by it: a stamp entry above the committed
  RDRAM would have read past the allocation and taken the emulator down (now a
  structured-exception guard plus a `VirtualQuery` span); an ini naming the
  wrapper itself recursed until the stack died; the GL read assumed the
  window's framebuffer and no pack buffer were bound, which GLideN64 does not
  promise; and PJ64's status bar sits INSIDE the client rect with the wrapped
  plugin drawing above it, so the read starts that many rows up. Plus the rule
  that makes a row trustworthy: a row is `exact` only when exactly ONE display
  list ran between presents.
- **2026-09-02 hop 4, CAPTURED AND ENCODED IN LOCKSTEP.** His rule: *"We should
  always be encoding frames we captured… If I see a frame in my replay, as a
  user, I would expect to see the input capture for that frame as well."* The
  sink's queue used to shed its OLDEST entry on overflow — a picture the ledger
  had already recorded — so under load the ledger described frames the video did
  not contain (his Haunted Books run: 680 captured, 387 encoded, 178 matched,
  and the clip shipped with no map at all). Now the recorder asks
  `sink.has_room()` BEFORE `ledger.observe`, and the queue is bounded by bytes.
  A loaded machine yields a sparser clip, never a clip whose map is a lie. This
  is why the feed log now covers every clip it describes (476/477, 542/548,
  835/841 on his three certified clips) and why the picture-run fallback behind
  it could be deleted.
- **2026-09-02 hop 4, TWO CUTS ONE PATH.** His 100-coin replay played as a
  black video: two `view()` calls raced, both missed the `clip.exists()` check,
  and two ffmpeg processes wrote one output path. `ReplayService.view` takes a
  per-attempt lock now, and `ClipExtractor.extract` cuts to
  `<name>.cut<pid>.mp4`, probes THAT file, and `os.replace`s it into place, so
  a partial or racing write can never be served. The temp name keeps the .mp4
  suffix LAST: ffmpeg picks its muxer from the extension.
- **2026-09-02 hop 4, DUPLICATE PICTURES.** When the game lags the emulator
  re-presents the same render: two grabs, two ledger rows with advancing
  stamps, one picture on screen — 115 of 775 stored frames on his pyramid clip
  were pixel-identical to their predecessor. The stamps make this honest by
  construction: each row names the frame that drew it, so two rows for one
  picture is a fact about the emulator rather than an error to repair.
- **2026-09-01 hop 2 — "frame 0 is before the reset… frame 3 is the first frame
  I actually reset on."** The run ended at the first grab ACTION (a midair
  fall), so `first` landed four frames before the reset press. Fixed in
  `track.py`: the dance's first frame ends the run, and the fall is timed.
- **2026-09-01 hop 6 — "84 2 on screen, 84 in our tool, yet screen-checked
  100%"** (5782). The map was RIGHT; Chrome presented slot k-1 because the
  clip's first pts is 0.011 s (2/3 of a slot) and the seek targeted
  `(k + 0.5)/60`. The fix belongs at hop 6 (zero the video's first pts at
  extraction, carry `video_start_s` to the panel), never in a reader. Its probe
  caught the fix NOT working on the first run: `video_start_of` derived ffprobe
  by replacing "ffmpeg" in the WHOLE path, which renamed the install folder too
  — a fallback that returns the old assumption is indistinguishable from the fix
  working on a clip that never needed it.
