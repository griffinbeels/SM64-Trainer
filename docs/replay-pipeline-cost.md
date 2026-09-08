# Capture operations and their remaining cost

The plugin supplies the picture and its copied game state together. The active
path is GLideN64 ReadScreen → BGR shared ring → owned BGR → picture selection →
accepted BGRA → timestamped NUT packets → FFmpeg/NVENC → H264/AAC segments.
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
| H264 encoding | Yes, NVENC when selected | Compression already runs during play. Encoding quality settings are unchanged. |
| Re-encoding a native replay during extraction | No | Supported H264/AAC cuts copy compressed media with hidden pre-roll. Other sources retain the transcode fallback. |

The GPU readback → CPU transport → GPU encoding journey is the next architectural
candidate. Replacing it requires a renderer-side texture handoff with explicit
ownership and picture/stamp identity. Moving NumPy operations onto CUDA after
readback would leave those transfers in place. This audit does not establish the
cause of physical monitor flickering.

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
