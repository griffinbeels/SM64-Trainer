# Native ghost audio experiment

This is an isolated feasibility probe, not shipped playback code. The synthesis
probe never attaches to an emulator. The separately invoked live sampler reads
memory only; neither tool sends input, writes memory, restarts, or installs
anything. Ghost audio remains an unfinished feature.

## Experiment plan

1. Verify the pinned libsm64 archive against `renderer/model/inputs.json` and
   compile an audio-only Win32 executable in an ignored private directory.
2. Read locally supplied stock-US and practice ROMs, validate the input size
   and byte order, and synthesize explicit Mario jump and landing commands.
   Write private PCM/WAV artifacts; never include ROM or extracted audio in git.
3. Measure initialization and per-tick synthesis, verify both stereo blocks
   from each tick, and independently inspect the resulting waveform lengths,
   non-silence and command-versus-silence differences.
4. Inspect original sound-request layout and instruction patterns in the ROMs
   and available symbol metadata. Keep address candidates separate from verified
   runtime capture. Do not add production addresses or claim a live gate passed.
5. Record results, reproduction commands, limitations, and the smallest next
   implementation contract. Keep source and probe documentation reviewable.

Builds, copied upstream source, ROM-derived waveforms and detailed measurements
belong under `.iteration/ghost-audio-spike/`, which is ignored by git.

## Result, 2026-09-22

**ROM-backed synthesis works; reliable recording of the complete Mario sound
state is still unproven. No production audio or volume control was added.**

The stock-US ROM (SHA256 `17ce077343c6133f8c9f2d6d6d9a4ab62c8cd2aa57c40aea1f490b4c8bb21d91`)
and local Usamune US ROM (`7be459e5383746e39e57e7afa48fa325de2bcbaa8d54925927781b65f409f025`)
both loaded successfully. Each is 8 MiB. Their complete audio-region hashes
differ, but the four exercised cues produced byte-identical PCM:

- `SOUND_MARIO_YAH_WAH_HOO` at 0.5 seconds.
- `SOUND_ACTION_TERRAIN_LANDING` at 1.5 seconds.
- `SOUND_MARIO_YAHOO_WAHA_YIPPEE` at 2.5 seconds.
- `SOUND_ACTION_METAL_LANDING` at 3.5 seconds.

These are explicit canonical commands, not guesses from recorded actions.
The WAV files have not received human listening verification. This experiment
does not establish compatibility for every sound, music sequence, or ROM hack.

| Witness | Observed result on both ROMs |
| --- | --- |
| Format and duration | Signed 16-bit stereo, 32 kHz, 192,000 frames, exactly 6.0 s |
| Data size | 768,000 PCM bytes, excluding WAV header |
| No-command control | Every sample zero |
| Four-cue result | 111,573 nonzero sample values; peak 20,014; RMS 1,958.23 |
| Four-cue PCM SHA256 | `df5b3b3bdabb1775beda6c926e97907941b9ae23265635888c6610096654b9bb` |
| Half volume | RMS 976.37; peak 10,007 |
| Native zero volume | Peak 1; RMS 0.115; **not bit-exact mute** |
| Initialization | Approximately 1–2 ms in these runs |
| Cue synthesis, 180 ticks | Mean about 0.0065 ms, p95 about 0.013 ms, max below 0.03 ms |

Timing is a short offline measurement on the development machine, including
silence between cues. It is not device latency, concurrent-ghost performance,
render-thread overhead, or a proof of a live 33.33 ms frame budget.

The native API returns 528 or 544, but writes **two blocks**, each containing
that many stereo frames. Thus a tick owns `4 * returned_count` int16 values.
The probe alternates 544/528/528 for an exact average of 32,000 stereo frames
per second at 30 Hz. Guard values after the expected range must remain intact;
the independent Python reader checks raw byte count, frame sum, waveform energy
and the silent control. Real playback must manage queue fill rather than copy
this deliberately fixed offline cadence.

## Exact source and build

The archive is verified against the existing pin in
[`renderer/model/inputs.json`](../../renderer/model/inputs.json): libsm64
`fd11813208272b4271d92bd92feb8f3fdbe61be5`, archive SHA256
`693d94a895388e8f60dff09d65739f75c4628946b5460ecd0dadbebec6293578`.
`run.py` extracts the original `sm64_audio_init`, `sm64_audio_tick`,
`sm64_play_sound`, and `sm64_set_sound_volume` function bodies directly from
that archive. It compiles the upstream loader, audio engine and CPU mixer as an
isolated MSVC Win32 `/O2 /MT` executable. No simulation, Mario geometry, SDL,
audio device, new project dependencies, or installed renderer is involved.

Staging patches translate GCC alignment declarations to MSVC in `libaudio.h`,
`convTypes.h` and `macros.h`, select Windows little-endian behavior, and remove
DLL-import decoration for the static executable. The audio algorithms and ROM
bank offsets are unchanged. Two conversion helpers are extracted from upstream
`utils.c` to avoid bringing unrelated filesystem code into the executable.

The loader copies 8 MiB and reads CTL at ROM `0x57B720`, TBL at `0x593560`,
sequences at `0x7B0860`, and bank sets at `0x7CC621`. It intentionally leaks its
ROM copy and lacks full bounds validation. Running a fresh owned process for
each ROM/mode bounds that lifetime and prevents cross-ROM global state. A
production adapter needs validated regions and owned cleanup. Source licensing
does not make ROM sound assets part of the repository: none are bundled here.

Reproduce with existing MSVC and Python, setting `$stockRom` and `$practiceRom`
to local ROM paths first:

```powershell
python tools/ghost_audio_spike/run.py --archive "$env:LOCALAPPDATA\SM64Trainer\build-cache\model\libsm64.zip" --out .iteration/ghost-audio-spike/build --rom "stock=$stockRom" --rom "usamune=$practiceRom"
```

The archive is also available from the pinned URL in `inputs.json`; the runner
does not download it. The output includes compiler log, source/executable
manifest, raw per-tick observations, summary JSON, and private WAV/PCM files.
Temporary normalized ROM copies are removed after each run. Do not commit the
output directory or distribute its ROM-derived audio.

## Ring qualification and limited live evidence

The offline qualifier reads
[STROOP's pinned US map](https://raw.githubusercontent.com/SM64-TAS-ABC/STROOP/6771197ad0f50718d3d4a13387e01d1e6d9c0d1a/STROOP/Mappings/MappingUS.map),
SHA256 `d942ba9781aafb500c5bd7a599d15a41a191c6e9fe65b95e6c384429bec2d3b1`.
It derives the ROM offset from the map's `.main` section and checks the actual
producer instructions, rather than trusting symbol names alone. Stock and
Usamune contain the same 48-byte `play_sound` function, SHA256
`1c8030b66e81554a34ad2de254654341111204835c71fc45c6b4ef5aef4fd57f`.

The function loads an unsigned byte producer at candidate `0x80332F44`, indexes
candidate ring `0x80361498` with a three-bit shift, stores sound bits at +0 and
source pointer at +4, and publishes the incremented producer **after** both
stores. The map places the consumer byte at candidate `0x80332F40`. The ring has
256 eight-byte entries. These are experiment candidates, not production address
registry entries or completed human live gates.

The authorized 30-second read-only sample reused `Pj64Memory`, existing US
layout fields and byte-order readers. It checked the live producer's code hash,
then bracketed two identical ring snapshots with three equal producer reads
and equal game-clock reads. Results:

- 6,459 polls; 6,435 accepted snapshots; 24 refused as unstable.
- Game clock advanced 899 frames, but Mario remained `ACT_FIRST_PERSON`.
- Producer and consumer stayed at 153. **No new sound event was observed.**
- Seven retained entries pointed to current Mario object's `gfx.cameraToObject`
  at object +`0x54`: spin, grass landing, grass step and punch-voice commands.
- The source position was approximately `(0, -114, -200)` while Mario's world
  graphics position was `(2789, 256, 5143)`. The pointer identifies Mario, but
  its vector is camera-relative, not the ghost's world position.
- Maximum accepted-sample gap was 15.2 ms; mean read time 0.108 ms; maximum
  read time 7.18 ms. These are external Python sampling costs, not native costs.

Retained entries corroborate source identity and persistence after consumption.
They have no recoverable original timestamps. Zero producer movement makes this
run **inconclusive** for command loss, wrap frequency and gameplay scheduling.
Double snapshots cannot exclude a complete 256-command wrap, especially when
entries are overwritten with identical commands. Modulo deltas are lower bounds.

The original game runs its audio thread at 60 Hz; request processing is gated by
the game-loop tick. Original PCM is buffered ahead of output, so a captured
request timestamp is not the instant the original sound reaches the speakers.
Graphics-boundary capture latency and phase still need live moving-action
measurement. See the pinned original
[audio implementation](https://github.com/n64decomp/sm64/blob/06ec56df7f951f88da05f468cdcacecba496145a/src/audio/external.c)
and [sound thread](https://github.com/n64decomp/sm64/blob/06ec56df7f951f88da05f468cdcacecba496145a/src/game/sound_init.c).

Offline qualification and optional read-only observation:

```powershell
python tools/ghost_audio_spike/qualify_ring.py --map .iteration/ghost-audio-spike/MappingUS.map --rom "$practiceRom" --out .iteration/ghost-audio-spike/usamune-ring.json
python tools/ghost_audio_spike/sample_ring.py --qualification .iteration/ghost-audio-spike/usamune-ring.json --out .iteration/ghost-audio-spike/live-ring.json --version us --seconds 30
```

The sampler requires the existing project dependencies (`pymem`), a running US
Project64 instance and an explicit invocation. It exits and detaches after at
most 30 seconds. Silence or stationary play cannot verify moving-action capture.

## Next implementation contract

1. **Complete capture before UI.** A versioned event stream should bind ROM
   identity, clip identity, continuity/origin and monotonic capture sequence to
   each event. Preserve command order and resolved sound bits; normalize the
   source identity instead of persisting a process pointer as the playback key.
   Gaps, wraps, resets and unsupported state must be explicit availability data.
2. **Capture more than `play_sound`.** Mario's `adjust_sound_for_speed` updates
   `sSoundMovingSpeed` outside the request ring, changing slide/flying sound
   pitch/volume. Waking from sleep calls `stop_sound` directly for snoring.
   Capture moving-speed state and source-specific stop state with proven timing
   and identity. Do not substitute inferred action sounds when this is missing.
   Remaining mixer randomness/reverb means authentic cues are a narrower claim
   than waveform-identical original gameplay audio.
3. **Worker-owned dry audio.** Replay a complete command/state/stop stream into
   a ROM-backed synthesizer on a worker, caching a dry track by recording and
   synthesizer version. Bound cache/memory and preserve the exact frame-to-sample
   mapping. Never load ROM banks or synthesize on the render callback. Upstream
   audio globals and bank arbitration make independent cached tracks preferable
   to combining several Mario sources in one shared synthesizer.
4. **Native output with a real volume control.** Give each ghost an XAudio2
   source voice routed to a ghost submix. Master ghost gain must not change
   emulator sound; zero gain must produce true silence. Use the current camera
   and each ghost's world position for spatialization, not the recorded pan.
   A centered/dry mono bake and X3DAudio positioning require their own quality
   check. [XAudio2](https://learn.microsoft.com/en-us/windows/win32/xaudio2/xaudio2-introduction)
   supplies the native mixer; [X3DAudio](https://learn.microsoft.com/en-us/windows/win32/xaudio2/how-to--integrate-x3daudio-with-xaudio2)
   computes listener/emitter parameters.
5. **Use the visual timeline.** Share its origin, scene validity, nudge and clip
   generation. Pause without retriggering held events; stop and discard old
   buffers on rewind/seek/reset/source change. Seek into the cached track rather
   than bursting skipped commands. Measure output latency and simultaneous
   playback separately from the very small offline synthesis cost reported here.

Validation needed before shipping: live jump/land/footstep/slide/flight/sleep-wake
capture, discontinuities and wraps, matching event ordering, owned loader
cleanup, device loss, actual mute and per-ghost gain, pause/seek/reset, and
multi-ghost timing. This spike proves the synthesis route and narrows capture
risks; it does not close the audio feature.
