---
paths:
  - "src/sm64_events/inputs/sampler.py"
  - "src/sm64_events/inputs/store.py"
  - "src/sm64_events/inputs/track.py"
  - "src/sm64_events/inputs/service.py"
  - "src/sm64_events/replay/recorder.py"
  - "plugin/gfxwrap/gfxwrap.c"
  - "plugin/gfxwrap/stamp_adapter.*"
  - "plugin/gfxwrap/source_snapshot.*"
  - "plugin/gfxwrap/link_source_api.*"
  - "plugin/gfxwrap/gpu_delivery.cpp"
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
- **Clock path:** `MediaRun` retains an encoder's first-picture origin and unique run ID. The GPU sink assigns monotonic 90 kHz video PTS, `PacketFragmentMux` writes the compressed packets into fragments unchanged, and the sink records each actual PTS with its captured row. `virtualmp4.py` serves a clip as headers over those sample bytes with explicit media offsets; `source_pts` restores the source tick to each decoded slot. `feed_map` looks up `(run_id, source_pts)` exactly, without fitting a bias. `picture_rows` retains the matched occurrence, and `state_rows` resolves its state without crossing a counter reset. This proves media association only; the plugin's picture/state interpretation is a separate witness.
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
deleted on 2026-09-16), and it runs only on a practice ROM: on any other
cartridge the wrapper forwards without stamps, the poller serves nothing and
the recorder captures nothing (`plugin/gfxwrap/practice_rom.h`). `sourcefactory.py`
selects `GpuCapture` and its paired sink under recorder ownership. The wrapper's
stamp adapter retains the ProcessDList stamp through the renderer's
UpdateScreen. A dedicated worker transfers an owned GPU snapshot and the same
selection sample/stamp; an isolated encoder returns compressed packets.
`channelencoder.py` and `gpumedia.py` transfer each completed compressed packet
and its exact source PTS/occurrence into the bounded `gpumediaworker.py` sink before
returning native credits. The sink persists selected rows/feeds before publishing
sample bytes and advances delivery receipts only after successful mux writes.
AAC/database/disk work never runs on the credit-return path. No second encoder
runs and no frame is inferred from desktop timing. Failure remains
explicit missing coverage; queued bytes alone are not a delivery receipt.

Read [the runtime contract](../../docs/replay-gpu-runtime.md) and
[the boundary contract](../../docs/replay-renderer-boundary.md). Live acceptance of
the picture/input association on LINK gameplay: round 48, 2026-09-16 ("Input
timeline matches"). Preserve the independent oracle and original media PTS path.

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

- **2026-09-05, standing rules from the pre-capture-layer era** (full
  narratives: [history](../../docs/history/chain-input-timeline-frame-2026-09.md)).
  Four derived frame maps (`frameclock`, `mapalign`, `pixelmap`, `timerread`)
  were each verified on one clip and wrong on the next -- *"Totally desynced
  now, it's even worse than when we started trying to improve this."* They were
  deleted: **do not rebuild them**; a clip with no stamps gets no map. The oracle
  owns its own constants, so no cleanup can silently break it; run it on a real
  clip after touching anything near it. Clips 7267, 7285 and 7307 (oracle
  459/459, 499/499, 758/758 exact) are the regression set for hops 3-5.
- **2026-09-02, captured and encoded in lockstep:** *"If I see a frame in my
  replay, as a user, I would expect to see the input capture for that frame as
  well."* A picture gets a ledger row only when it is actually encoded; on the
  GPU route delivery receipts advance only after a successful mux write.
