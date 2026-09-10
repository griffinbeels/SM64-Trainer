# Capture operations and their remaining cost

The plugin supplies the picture and its copied game state together. The active
path is GLideN64 ReadScreen → BGR shared ring → owned BGR → picture selection →
accepted BGRA → timestamped NUT packets → FFmpeg → H264/AAC segments.
Native extraction copies those compressed packets into the review MP4.

This audit preserves the existing pixel selection, dimensions, frame cadence,
quality, audio, timestamps and input associations. Removing an operation must
not silently change one of those contracts.

| Operation | On the plugin picture path? | Disposition |
| --- | --- | --- |
| Desktop/window capture and oversampling | No | `main.py` chooses the plugin when it produces pictures; desktop capture is the availability fallback. |
| Fixed-rate video filling and its pacing loop | No | Explicit legacy CFR/in-process fallback. The picture feeder waits for queued pictures and retains its sparse heartbeat. |
| Reading timer/controller glyphs from pixels | No | Offline diagnostic instruments; extraction uses the plugin's retained stamps. |
| Wrapped ReadScreen and its returned CPU buffer | Yes | The installed renderer's interface exposes CPU pixels. Direct plugin access does not currently give the recorder a GPU texture. |
| Native copy into the shared ring | Yes | Transfers ownership from the wrapped plugin's temporary allocation. |
| Python's stable slot copy and sequence check | Yes | Prevents a later producer write from overwriting pixels being inspected or encoded. Borrowing a ring slot requires a different lifetime protocol. |
| Small stride-8 picture sample | Yes | Preserves the current duplicate and same-stamp settling decisions, including counter resets and source changes. |
| Full BGR flip/alpha expansion | Accepted pictures only | `pixels.py` samples the canonical BGRA view first; rejected pictures skip full preparation. Preparation precedes ledger commitment so allocation failure leaves an identical retry eligible. |
| Copy into a padded AVFrame, then rawvideo encoding | Removed | The packed BGRA buffer is already the rawvideo packet payload. The sink wraps it directly, preserving packet flags and the 90 kHz clock. The old padding workaround and reusable frame allocation are gone. |
| NUT muxing, pipe transport and CPU-to-encoder upload | Yes | Preserve explicit video/audio timestamps. These remain costs to measure; the packet change does not remove the pipe or GPU upload. |
| Synthesized silence between normal PCM callbacks | Deferred on the picture feed | A 50 ms grace after real audio prevents speculative silence from occupying the next normal batch's timestamps. Truly quiet sources still get silence; legacy CFR pacing is unchanged. |
| H264 encoding | Yes, hardware when its probe passes | Compression already runs during play. NVIDIA quality settings are unchanged; the hardware selection below also covers AMD and Intel. |
| Re-encoding a native replay during extraction | No | Supported H264/AAC cuts copy compressed media with hidden pre-roll. Other sources retain the transcode fallback. |

The GPU readback → CPU transport → GPU encoding journey is the next architectural
candidate. Replacing it requires a renderer-side texture handoff with explicit
ownership and picture/stamp identity. Moving NumPy operations onto CUDA after
readback would leave those transfers in place. This audit does not establish the
cause of physical monitor flickering.

## Hardware selection

`encoder.pick_video_codec(ffmpeg)` checks the **executable the recorder will
actually use**, rather than assuming PyAV and the child have the same codecs.
`codecprobe.py` tries NVIDIA NVENC, AMD AMF, then Intel Quick Sync; x264 is the
software fallback. Each candidate runs one hidden child with an eight-second
timeout. Selection runs once at startup, never in the capture callback. A
failure is logged and advances to the next candidate. The no-FFmpeg PyAV
writer retains its separate NVENC/x264 selection.

The probe encodes six timestamped 640×480 BGRA pictures with the production
realtime quality settings and disabled B-frames. It independently decodes the
result, checking count, dimensions, exact 90 kHz timestamps (including a pair
one tick apart), and asymmetric per-picture color witnesses. Encoder presence
or a successful exit with malformed/retimed output does not pass. This is a
capability test, not a performance or perceptual-quality benchmark.

| Backend | Realtime / offline policy | Local hardware evidence |
| --- | --- | --- |
| NVIDIA NVENC | Existing p4/p6, CQ 20, high profile; unchanged rate limits | RTX 5090: picture identity, held pictures, native cuts and A/V checks passed |
| AMD AMF | Balanced/quality presets, CQP 18 for I/P/B, frame skipping disabled | Radeon(TM) Graphics, driver 32.0.21045.5002: same real pipeline checks passed |
| Intel Quick Sync | Medium/slow presets, ICQ 18, lookahead disabled | No working QSV device established here; hardware cases run on compatible hosts |
| CPU x264 | Existing ultrafast/veryfast, CRF 18 | Software pipeline tests |

These vendor quality scales are independent. AMD/Intel targets still need
representative footage and live resource measurements on those devices. Their
quality modes do not apply NVENC's peak bitrate cap; the ring's existing total
byte/disk guard still applies. Codec-specific settings live only in
`config.video_quality_args`; ring, transcode fallback and compilation use that
registry. `forced_idr_args` owns the differing hardware option spelling.

AMF's direct BGRA input exposed a color-signaling problem on the local driver:
RGB (40,100,220) decoded as (47,101,205). Its GPU generated limited-range BT.709
but labeled the result full-range. `raw_picture_args` sets frame properties
with `setparams=range=limited:colorspace=bt709`, producing (37,99,217) without
another pixel conversion. Plain output color flags did not fix it; forcing
NV12 through CPU conversion also worked but was unnecessary. The frame-property
fix is confined to raw capture; it is not applied to already-decoded YUV clips.
The capability probe rechecks the pixels on each build/driver. The AMF encoder
also rejected the tiny 96px-high barcode fixture; hardware replay tests use
normal 640×480 capture dimensions while retaining the independent barcode IDs.

Future GPU-resident capture needs a renderer-side texture interface with
explicit lifetime and picture identity. NVIDIA documents DirectX/CUDA input
resources on Windows, but its direct OpenGL encoder device is Linux-only.
Simply passing GLideN64's OpenGL texture to the Windows NVENC interface is not
that implementation. See the [NVIDIA encoder API guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-video-encoder-api-prog-guide/index.html).
AMD and Intel settings follow [FFmpeg's AMF implementation](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/amfenc_h264.c)
and [QSV documentation](https://www.ffmpeg.org/ffmpeg-codecs.html#QSV-Encoders).

The plugin runs inside 32-bit Project64. NVIDIA excludes 32-bit CUDA programs
on RTX 50-series GPUs, so an in-plugin CUDA path is unsuitable for the 5090.
See [NVIDIA's support policy](https://nvidia.custhelp.com/app/answers/detail/a_id/5615).
A shared D3D11 texture handed to a 64-bit encoder is a candidate to prototype;
[WGL_NV_DX_interop2](https://registry.khronos.org/OpenGL/extensions/NV/WGL_NV_DX_interop2.txt)
defines the OpenGL/D3D11 resource bridge. Actual extension support on both
adapters, renderer-thread access, synchronization and exact pixel/stamp
identity remain unverified. No texture-sharing implementation ships here.

## Controlled measurements, 2026-09-08

Reference: saved Whomp's Fortress 100 Coins 1'29"96, 1600×1200, PyAV 17.1.0.
Offline comparisons used one CPU and four alternating before/after rounds.
Each selection batch kept the same 64 pictures; repeated captures exercised the
existing duplicate decision rather than dropping additional video pictures.

| Isolated operation | Before | After |
| --- | ---: | ---: |
| Selection/preparation, 64 changing captures | 226.6 ms CPU | 203.1 ms CPU |
| Selection/preparation, each picture captured twice | 414.1 ms CPU | 218.8 ms CPU |
| Selection/preparation, each picture captured eight times | 1,445.3 ms CPU | 242.2 ms CPU |
| Python NUT mux including PCM, 1600×1200, per picture | 1.71 ms CPU | 0.31 ms CPU |
| Same mux, 1190×1200 non-aligned width, per picture | 2.38 ms CPU | 0.31 ms CPU |

The mux benchmark discarded output in memory: it excludes pipe backpressure,
the child encoder and disk. These are stage improvements, not measured
whole-machine savings or replay-start latency. A 45-second live baseline had
1,350 captures but only 167 muxed pictures; it cannot represent continuously
changing gameplay. Other applications/tests remained running, as requested.
Use [the profiling procedure](profiling.md) for a matched live comparison.

After the human restarted onto the deferred-pixel/direct-packet changes, another
45-second live capture recorded 936 callbacks and 907 muxed pictures (897 fresh
full conversions; the feeder also emits sparse heartbeats). It had no new
capture drops, input skips or audit mismatches. Trainer CPU averaged 30.96% of
**one logical CPU**, and FFmpeg 14.05%; whole-machine CPU averaged 36.93% with
other applications/tests still running. The earlier capture encoded only 167
pictures, so the two samples do **not** establish a causal CPU reduction.
Conversion now occurs inside `replay.on_frame`; comparing that inclusive span
with its old conversion-excluded duration would also give a false regression.
The native graphics profile was unavailable; it is not a measured zero cost.

The expanded A/V check also exposed an existing pacing issue: the software
case once exceeded its unchanged 50 ms bound at +55.8 ms. A trace placed the
source click within 0.4 ms of the captured flash, but its mux timestamp was
32.9 ms later. `AudioPacer.tick` had filled the space between ordinary audio
batches, so the next real batch was pushed forward by the non-overlap rule.
The picture feed now waits 50 ms after real audio before synthesizing silence.
It preserves the real PCM data and source timestamps supplied to the pacer,
continues padding when the source goes quiet, and leaves legacy CFR behavior intact.
The unchanged flash/click check then measured −1.05 ms (x264), −0.90 ms
(NVENC), and −1.02 ms (AMF). All 35 focused pacing/picture-feed/held-picture
checks passed; three QSV cases were skipped because that hardware is unavailable.
These are synthetic A/V measurements, not a claim about every live workload.

`tests/test_replay_pixels.py` compares old/new recorder decisions, full accepted
pixel arrays, stamps and feeds across rejects, resets, unknown timestamps,
source changes and allocation failure. `tests/test_pluginsource.py` overwrites
and closes the shared ring before materializing its retained capture.
`tests/test_replay_mux_packets.py` decodes both old/new NUT streams and compares
all pixel bytes, video timestamps and PCM, including odd crops, padded source
rows, timestamp collisions and released caller references.

Real NVENC/software identity and held-picture tests, the independent audio
flash/click check, and browser picture/timer stepping passed for this change.
Live smoothness, complete machine cost and physical flicker need live evidence;
offline correctness does not substitute for that observation.
