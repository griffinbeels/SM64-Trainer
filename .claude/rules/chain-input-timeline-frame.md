---
paths:
  - "src/sm64_events/inputs/sampler.py"
  - "src/sm64_events/inputs/store.py"
  - "src/sm64_events/inputs/track.py"
  - "src/sm64_events/inputs/service.py"
  - "src/sm64_events/replay/recorder.py"
  - "plugin/gfxwrap/**"
  - "src/sm64_events/replay/pluginsource.py"
  - "src/sm64_events/replay/sourcefactory.py"
  - "src/sm64_events/replay/gpucapture*.py"
  - "src/sm64_events/replay/gpumedia.py"
  - "src/sm64_events/replay/channelencoder.py"
  - "src/sm64_events/replay/packetmux.py"
  - "src/sm64_events/replay/pixels.py"
  - "src/sm64_events/replay/oracleread.py"
  - "src/sm64_events/replay/ledger.py"
  - "src/sm64_events/replay/picturearchive.py"
  - "src/sm64_events/replay/ffmpeg_sink.py"
  - "src/sm64_events/replay/media.py"
  - "src/sm64_events/replay/ring.py"
  - "src/sm64_events/replay/extract.py"
  - "src/sm64_events/replay/feedmap.py"
  - "src/sm64_events/replay/padread.py"
  - "src/sm64_events/replay/service.py"
  - "src/sm64_events/replay/navigation.py"
  - "src/sm64_events/ui/frame.js"
  - "src/sm64_events/ui/videopicture.js"
  - "src/sm64_events/ui/replaykeys.js"
  - "src/sm64_events/ui/components/replay.js"
  - "src/sm64_events/ui/components/inputtimeline*.js"
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
- **Captured identity:** the counter and state copied by the capture layer inside Project64 at ProcessDList. `exact` means one display list since present; it does not independently prove returned pixels. Source-PTS transport tests and three independently decoded live timer/pad witnesses support the current same-row projection. The older predecessor convention was rejected; these witnesses do not certify untested renderers.
- **The independent witness:** THE ORACLE (`replay/oracleread.py`, `tools/score_oracle.py --attempt N`) — the frame number Usamune's HUD memory display of `0x8032D5D4` prints into the picture, read map-free through the +1 rule. It never ships to a user; it is how any claim about the map is settled.
- **Sink:** the panel under the timeline (FRAME n / N, the stick box, the button chips) on the picture the `<video>` is presenting.
- **Clock path:** `MediaRun` retains an encoder's first-picture origin and unique run ID. The sink assigns monotonic 90 kHz video PTS, preserves them through NUT/TS, and records each actual PTS with its captured row. The cut subtracts an explicit integer source tick; its `source_pts` restores that tick to each decoded slot. `feed_map` looks up `(run_id, source_pts)` exactly, without fitting a bias. `picture_rows` retains the matched occurrence, and `state_rows` resolves its state without crossing a counter reset. This proves media association only; the plugin's picture/state interpretation is a separate witness.
- **Browser picture:** `ui/videopicture.js` retains the last delivered `requestVideoFrameCallback.mediaTime` for each video, starting before autoplay. Both stepping and the input inspector read it; a paused template refresh cannot replace it with the requested seek's `currentTime`. `picture_ids` preserves capture occurrences for ordered stepping and heartbeat skips. Unknown input slots clear the readout; raw counters alone cannot resolve overlapping input epochs.
- **Picture state versus sampled track:** `replay/service.py` projects validated `state_rows` as `picture_states`. AttemptDrawer passes these through InputTimeline; with exact capture on, the button lanes and the stick/speed curves are drawn from those stamps (`stampedRuns` in `inputtimelinemodel.js`) and the inspector indexes the presented slot directly for pad/yaw/speed. The independently polled track only fills frames that have no picture, drawn hatched/dashed and labelled "polled, no picture" (the *polled fill*); the two sources can no longer disagree on one paused picture. The poll can never be authoritative for a picture: the stamp is copied inside the game's own display-list call after that frame's controller read, while the poll reads from another process at arbitrary phase. Missing slots remain unknown. The disagreement audit counts each captured occurrence once and can detect a stale poll, never a wrong stamp. A stamp whose controller bytes are not a pad (axes outside s8, button bits no controller sets) carries no pad, and a layout without a Mario address stamps no facing/speed. Tests: `test_replay_display_evidence.py`, `test_pluginsource.py`, `test_ui_replay_captured_pad.py`, `frontend/inputtimeline.test.js`.

**There is exactly one map path, and it is a read.** A clip whose rows are not
stamped carries `frame_map: None`, and the panel says "Frame-exact capture is
off. Set up" rather than showing a derived guess. That is his standard for this
surface: *"We need 100% accuracy for this. If it's wrong even once, then it
can't be relied on as a tool"* (2026-08-23).

| # | hop | value is true here as | module | probe (reads it) | inject (forces it) | when the hop is broken, the probe shows | when the probe itself is broken, it shows |
|---|-----|-----------------------|--------|------------------|--------------------|------------------------------------------|--------------------------------------------|
| 1 | the pad in RAM, per game frame | `InputFrame(stick_x, stick_y, buttons)` filed under the gGlobalTimer value read in the same poll | `src/sm64_events/inputs/sampler.py` | `uv run python tools/probe_inputs.py` (live) / `InputSampler.health()["edge_mismatches"]` | none — domain rule 6 forbids writing emulator memory | `edge_mismatches` > 0, or a `skips` count naming frames nobody read | a green health payload from a server attached to nothing |
| 2 | the chunk store, then the track | the same capture occurrence (chunk IDs/session retained), trimmed to the attempt: the run ends the frame BEFORE the first dance action, `first = last - (igt - 1)` | `src/sm64_events/inputs/store.py`, `src/sm64_events/inputs/track.py` | `uv run python tools/inspect_timeline.py --attempt N --frames a-b` | append edited frames with `db.inputs.append` (the shape `tests/test_inputs_track.py` uses) | frame 0 landing before the reset was pressed (5534: the fall counted as the dance) | a track read from a sibling worktree's journal |
| 3 | the timeline payload | `runs` on the capture axis (zero-based at the resolved span origin, retaining missing boundary samples as gaps), `lead_frames`, `attempt_frames`, `stretches` | `src/sm64_events/inputs/service.py` | `InputsService(...).timeline(id, span=(lo, hi))` offline, then `run_at(axis)` | none — pure over hop 2, so hop 2's injection reaches it unchanged | a run whose `stick_x` differs from hop 2's at the same raw frame | a payload built without the clip's span (the attempt alone; a different axis) |
| 4 | the pictures, each STAMPED with the frame that drew it | the capture wrapper copies the tracker's address table out of RDRAM at the ProcessDList that draws frame N and hands that stamp to the trainer's renderer (LINK's GLideN64 v4.2 with the SourceV2/ContextV1 overlay); when the renderer finishes the picture it submits an owned GPU snapshot plus the immutable stamp to the delivery worker without waiting, and the isolated encoder returns a compressed packet carrying the same occurrence. The recorder files the row `exact: true` with `frame`, `pad`, `mario`, `igt_overall`; a refused picture (pool full, no swap, no stamp) is counted, never guessed. Without the installed layer the recorder photographs the desktop by time only and names no frame | `plugin/gfxwrap/gfxwrap.c`, `plugin/gfxwrap/link_source_api.cpp`, `src/sm64_events/replay/gpucapture.py`, `src/sm64_events/replay/gpucapture_session.py`, `src/sm64_events/replay/recorder.py`, `src/sm64_events/replay/ledger.py` | `/api/replay/status` `frame_source` + `frame_source_health`; the sidecar's `picture_ledger` rows; `uv run python tools/score_oracle.py --attempt N`; `tests/test_gpu_delivery.py` and `tests/test_wrapper_runtime.py` drive the native worker with no emulator | the delivery host's pressure scenarios omit and refuse pictures on purpose: every surviving row must still carry its own stamp | rows without `exact`, or `refused` climbing in the native `gpu_summary` log while the player was not idle | a probe reading the ring segment instead of the extracted clip (segments start at pts 1.4) |
| 5 | the frame map: video slot -> captured state | `feed_map` retains the exact source-PTS capture occurrence in `picture_rows`; `_take_the_stamps` projects that SAME row into `frame_map`, `picture_igt`, and `state_rows`. No predecessor subtraction or raw-counter search. An inexact or missing capture stays unknown; legacy fitted maps remain unverified | `src/sm64_events/replay/feedmap.py`, `src/sm64_events/replay/service.py` | Independently decode a known video slot, compare its visible timer/pad with its matched capture row, then its served state; `tests/test_replay_captured_state.py` retains three live witnesses | Replace the state projection on a saved COPY and replay the same picture in the real drawer | matched capture row agrees with pixels but projected state differs | a green RAM-to-store audit without looking at pixels, or a fitted timestamp phase mistaken for capture identity |
| 6 | browser presentation, step and input inspector | the delivered video slot and its own timer; an input only when its map resolves uniquely | `src/sm64_events/ui/frame.js`, `src/sm64_events/ui/videopicture.js`, `src/sm64_events/ui/components/inputtimeline.js` | `tests/test_ui_replay_picture_steps.py` decodes picture IDs from canvas after real drawer clicks; component tests cover missing slots and paused refresh | scratch replay response with known encoded pictures and slot identities | backward steps revisit an earlier epoch, tiny slots skip, or the timer changes on a paused refresh | only checking requested currentTime, or never asserting a known input before and after a missing slot |

## The GPU route at hop 4

This is the only shipped route (the raw ReadScreen/frame-stream path was
deleted on 2026-09-16). `sourcefactory.py` selects
`GpuCapture` and its paired sink under recorder ownership. The wrapper's bounded
renderer command adapter retains the ProcessDList stamp through the original
UpdateScreen. A dedicated worker transfers an owned GPU snapshot and the same
selection sample/stamp; an isolated encoder returns compressed packets.
`channelencoder.py` and `gpumedia.py` transfer each completed compressed packet
and its exact source PTS/occurrence into the bounded `gpumediaworker.py` sink before
returning native credits. The sink persists selected rows/feeds before publishing
sample bytes and advances delivery receipts only after successful mux writes.
AAC/database/disk work never runs on the credit-return path. They do not start
the raw-picture encoder or infer frames from desktop timing. Failure remains
explicit missing coverage; queued bytes alone are not a delivery receipt.

Read [the runtime contract](../../docs/replay-gpu-runtime.md) and
[the boundary contract](../../docs/replay-renderer-boundary.md). Component and
connected-fixture witnesses do not certify real LINK gameplay's picture/input
association. Preserve the independent oracle and original media PTS path.

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
   the complete picture-to-state association agrees for the scored channels; it does not isolate the capture boundary by itself.
3. **Hop 3.** Build the payload offline with the clip's span and read the run
   at the panel's axis. If it holds Y where hop 2 holds X, the fault is the run
   collapse or the axis; stop there.
4. **Hop 6.** `tools/probe_clip_seek.py --attempt N --slots a-b` around the
   picture. If Chrome presents slot k-1 for the seek meant for k, the fault is
   the clip's first pts and the panel's slot arithmetic, and no map change can
   hold.

## Failure catalogue

- **2026-09-15 (round 48): two sources on one screen, and three ways a
  stamp could lie.** The lanes drew the polled track while the inspector drew
  the stamp, so a stale poll (his 100 Coins frame 3017: no successful poll in
  the last ~15 ms of the window) showed an empty R lane under an inspector
  chip that said R. Lanes now follow the stamps; the poll is a labelled fill.
  Also fixed: a garbage controller block rendered as a wild stick with every
  chip lit; a Mario-less layout stamped yaw 0 as a real bearing; and an origin
  change without a swap (config dialog open) paired the previous front buffer
  with the new stamp as `exact` (`SA_NO_SWAP` now omits it; the fake renderer
  models the swap count). A source refusal is a counted missing picture, not a
  dead run; the first picture after activation can now be exact because a
  bootstrap observation acknowledges the stamp.

- **2026-09-06: a tiny held-picture segment was misread as audio only.**
  The 1128-byte synthetic witness `tests/fixtures/replay_tiny_held.ts` contains
  H264 PTS369919, but PyAV and ffprobe autodetected MPEG-PS/MP2. Forcing
  MPEG-TS restored that exact frame without changing the bytes. The ring's
  format is known: `extract.py` now passes it to source-frame probing and the
  concat input; the independent test decoder does likewise. The real-file
  regression in `tests/test_replay_held_picture.py` covers probing and a cut
  whose entire source is this segment. No frame-map offset or tolerance changed.

- **2026-09-06: direct source identity still inherited the fitted map's
  predecessor subtraction.** Fresh Wild Blue7718 slot138, Pyramid7685 slot342
  and Books7739 slot76 were decoded independently. Their pixels match each
  matched capture row's own timer and visible pad; selecting the prior row
  causes all three reports. State projection now uses the same `picture_rows`
  occurrence for counter, timer and state. `tests/test_replay_captured_state.py`
  pins these witnesses; cached/saved tests retain legacy refusal. The historical
  oracle measurements below tested the OLD derived map including its fitted
  media join, not raw capture pairing under the current exact source-PTS path.
  They do not establish a universal one-frame plugin lag. No historical raw
  capture pairing has been reconstructed by this correction. Fresh live
  confirmation remains required; sampled pixel matches do not prove all render
  configurations or input-sampler freshness on every frame.

- **2026-09-06: replay wake left the previous picture on screen.** Wild
  Blue7639 held an old capture until259ms after the attempt anchor, despite
  a controller A press1.21s before fresh video resumed. Passive Mario action
  suppressed the activity tap, and plugin demand waited for a1s liveness poll.
  Already-sampled pad changes now notify recorder activity, and idle transitions
  immediately refresh source demand. Scope tests check the actual source flag,
  first returned frame, manual pause, stopped source, callback failure and
  pause/unpause during startup. Re-recording is necessary to verify the visible
  result; missing pictures cannot be restored from an old clip.

- **2026-09-06: held pictures looked like missing footage.** The real-encoder
  regression `tests/test_replay_held_picture.py` found a picture at 2.0135s
  filed as ending at 2.0974s although the next picture began at 3.0168s.
  Correcting coverage alone still cut an audio-only MP4: output `-ss` dropped
  the picture already on screen. `media.py::picture_duration_filter` preserves
  PTS and sets packet duration from the next PTS before segmentation. Extraction
  starts on the measured source picture and holds its last picture through the
  cut end. The test decodes independent barcodes through software/NVENC, a long
  hold and a delayed capture after heartbeat. `tests/test_ui_replay_held_picture.py`
  seeks the actual cut in the drawer. This does not prove plugin pixel/state lag.

- **2026-09-06: cached and saved sidecars bypassed the fresh-cut checks.**
  `replay/association.py` validates the retained encoder clock against clip
  start and picture times, plus capture references and traversed state types.
  `ReplayService._validated_meta` then rebuilds derived maps/timers on every
  fresh, cached and saved read. A stale derived map can recover when that
  source evidence survives; a legacy fitted map cannot acquire provenance by
  being copied or saved. Existing sidecar bytes stay unchanged, and fresh
  sidecars keep source evidence even when a map is withheld. Regression:
  `tests/test_replay_cached_identity.py` covers the valid saved twin, stale
  recovery and refusal paths. The timeline also cannot seek an unmapped video
  by an arithmetic anchor offset (`tests/frontend/inputtimeline.test.js`).
  These checks validate retained metadata, not the plugin's pixel/state
  convention or a sidecar's association with independently replaced video bytes.

- **2026-09-06: the media clock can shift while the cadence stays perfect.**
  `tests/test_replay_picture_identity.py` paints independent binary picture
  identities and reads them after the actual sink -> TS ring -> MP4 cut.
  Old NVENC cuts named picture+1 at some cut phases, while x264 named +2/+3;
  all could have sub-ms fitted residuals. Preserve the first-picture origin,
  `copyts`, TS `mpegts_copyts`, disabled negative-timestamp shifting and a
  90 kHz MP4 edit-list clock. Cut on an explicit integer source tick and add
  that same integer to decoded output ticks; rounded floating seconds can
  otherwise recover the wrong tick. Audio queued before picture zero shifted
  video by 125 ms even with those flags: trim pre-origin audio samples before
  NUT. Assign colliding video timestamps before muxing and log the assigned
  PTS, so FFmpeg cannot rewrite an identity invisibly. Six real-encoder cases
  cover software/NVENC, queued audio/catch-up, timestamp collisions and ten
  cut positions. Source identity is separate from game-counter identity.
  Encoder-run changes break concatenation even at the same frame size; old
  reader threads retain their own origin. Legacy cached maps, live plugin
  pairing and ordered input/browser identity still require the goal's remaining
  verification; this boundary fix does not certify the complete chain.

- **2026-09-06: green stamp audit with wrong pictures.** Wild Blue attempts
  7545/7556 have one-frame-early maps at six directly decoded witness slots;
  Pyramid 7571 matches at three. Both timer and stick confirm the discrepancy.
  Their pad-stamp audits pass 336/336, 328/328 and 551/551: that audit compares
  RAM/store values under the map's frame number, not pixels. Pyramid's nearest
  feed join chooses a different row phase, so adding one globally breaks the
  twin. Actual capture pairing versus cut-origin/feed association remains
  unresolved; retain historical oracle cases before changing the lag convention.

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
- **Historical 2026-09-05 hop 5 — retired lag interpretation (see correction above).** MEASURED twice, and the
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

## Pixel preparation at hop 4

`replay/pixels.py` retains the sequence-checked owned BGR slot copy. The ledger
samples the same top-down BGRA pixels before full conversion, including alpha
and odd edges. Preparation of an accepted picture occurs before ledger state or
archive mutation. The sink wraps contiguous BGRA as a raw NUT packet without
the former padded-frame/rawvideo-encode copies; source PTS, audio and feed
identity are unchanged. See [the operation audit](../../docs/replay-pipeline-cost.md)
for active/fallback paths and the parity witnesses.

## Browser boundary regression — 2026-09-06

Replay navigation and input extent are separate from picture identity.
`replay/navigation.py` excludes heartbeat copies when deriving `input_span`:
Pyramid #32 began with two copies of raw 140152 before fresh capture at
141550; raw min/max invented a 1,458-frame lead instead of 60 frames.
The original picture map and pre-buffer remain intact, and genuine holes
inside the captured extent remain holes. `tests/test_replay_navigation.py`
and the mounted timeline test pin that boundary.

Initial playback, Start and ArrowDown share `frame.js::attemptStartTime`.
The service supplies the first associated slot at/after the attempt anchor;
absent reset pictures are not fabricated. The retained Pyramid clip's first
verified post-reset picture is IGT 3 (`00"10`), not frame 0. Counter-reset
ambiguity declines raw-counter navigation; its wall-clock fallback never
creates an input association. `tests/test_ui_replay_start.py` checks actual
decoded pictures, pre-buffer access and typing/modifier guards.

`tests/test_ui_frame_step.py` reproduced five defects in the shipped helpers:
reset sequence `[100,100,101,101,99,100,100,101]` stepped to `[null,null,0,4]`
instead of slots `[4,2,5,4]`; the 0.1 ms search epsilon read 90 kHz slots
0 and 1 as slot 2; same-number picture identities were collapsed; unknown
slots were skipped; the 30 Hz end clamp moved VFR seeks into another picture.
Controls now traverse recorded slot order and actual intervals.

The mounted `InputTimeline` reproduced an unknown slot displaying frame 2's
A button, and the second raw-100 visit showing the first visit's `01"66`
instead of its own `00"10`. It now retains the presented slot, clears unknown
input/state, and does not substitute the attempt-axis count for missing video
IGT. The shared observer also keeps that slot across paused template refreshes.
`tests/frontend/inputtimeline.test.js` and `videopicture.test.js` pin these
behaviors; the real-browser barcode test checks pixels AND IGT on every step.

This boundary does not resolve the input store's overlapping epochs, missing
lead-axis samples, cached historical maps, or the plugin's picture/state lag.
Those are distinct upstream identity requirements, not reasons to guess here.

## Final-review regressions — 2026-09-07

- Temporary observation-spool failures must produce missing input provenance,
  not terminate polling. Creation, writes and finalization are separate failure
  sites (`tests/test_inputs_wrap_failures.py`). A read outage that splits source
  identity remains ambiguous even if observed counters later increase.
- The capture mapping/address table and scratch initialization belong to the
  recorder lock owner. A losing viewer cannot mutate shared capture state or
  erase footage. Composition tests use inert OS/plugin adapters and exercise
  two owners with different address tables (`tests/test_composition.py`).
- A fixed 63,000-row ledger discarded identity while whole-session footage was
  still available. Scratch identity now follows retained video, with bounded
  memory and eviction tied to removed source intervals. Its retention tests
  use synthetic timestamps and tiny data, not hours of live capture.
- Mapped exports retain captured states, VFR timestamps and final holds. Raw
  counters cannot select source state across resets. Discrepancy evidence also
  names decoded slots: unrepresented captures and ambiguous raw-only lookups
  cannot implicate a displayed picture (`tests/test_replay_display_evidence.py`).
- Reset seeks previously selected an earlier high counter through `>=`; the
  unique lower-counter occurrence now wins. One active replay owns keyboard
  shortcuts when several drawers are open. Padded labels subtract lead exactly
  once, while discrepancy clicks use the decoded slot directly. The real-browser
  two-drawer regression checks independent picture barcodes and both widths.

Capture-demand cleanup, the expiring plugin consumer lease and direct GL state
restoration are independently reviewed lifecycle fixes. They do not establish
the cause of Griffin's intermittent physical monitor flash.
