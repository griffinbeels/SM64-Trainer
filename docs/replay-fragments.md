# Shared fragments and native replay views

Normal picture-feed capture now configures `FfmpegAvSink` to publish one encoded
fragment stream into `FragmentArchive`. There is no parallel TS recording or
second encoder. The explicit CFR/no-FFmpeg fallback and existing saved MP4 files
retain their previous contracts. The native `<video>` player remains the default;
the separate MSE loop experiment below is still opt-in.

`FragmentMedia` owns run lookup and session retention. On View, `VirtualMp4`
builds only native MP4 sample tables and an edit list. Its `mdat` byte ranges
reference encoded samples in the existing extent files. `/api/replay/clips/…`
serves these ranges under leases, including seeks and cancellation. No complete
per-attempt video is copied, encoded, or probed on this path. Each opened URL
keeps a frozen source interval, independent of later padding settings/capture.
The first picture is the original held picture; its integer source PTS drives
the existing captured-input association. Future optional post-padding does not
delay View; any wait covers only the attempt's own end.

Save, manual PB marking's automatic save, and compilation use the same bytes.
Only explicit preservation/export materializes a complete MP4. Save stages its
media once with a free-space reserve, then publishes metadata and media through
the existing atomic/recoverable protocol. Saved files survive session cleanup.
Temporary extent eviction also queues removal of its source input provenance,
outside the ring lock. Active response leases retain both until the read ends.
The registry has a session-wide 131,072-sample budget across encoder restarts.

`AttemptHistory` tracks a configurable count of completed attempts (default 10),
independent of course, plus the earliest open star/segment/run. Imported times
do not consume this window. Tracking updates bounded metadata only; recorder
maintenance expires old shared extents off the polling thread. It retains padding
and a predecessor extent for GOP/AAC dependencies. Closed runs entirely before the
window can be removed in full. Reads/saves lease source spans before selection,
including fragments published while waiting for the tail. Saved exports stay
outside scratch eviction. The disk/free-space and sample caps still apply, so the
count is a history limit, not a promise of unlimited space for long attempts.

The header writer follows the native sample-table/edit-list representation used
by [FFmpeg's MOV muxer](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/movenc.c).
It accepts only our already-validated H.264/AAC producer topology. Arbitrary
uploads continue through their existing media handling.

The connected native path passed independent source-picture/PTS, decoded audio,
input association, HTTP ranges, cancellation, Save and pressure tests with x264,
NVENC and AMF. `test_replay_virtual_mp4.py`, `test_replay_native_preroll.py`,
`test_replay_fragment_service.py` and `test_ui_fragment_replay.py` own these
contracts. The real drawer's presented barcodes matched its input inspector
through nonzero cuts and seeks. QSV was unavailable on this machine.

A native edit must start at the original held picture's PTS. An edit between
pictures can make MOV demuxers clamp the first picture to zero, breaking its
source association. The broker selects that held boundary and the header writer
rejects unaligned starts. Decoded AAC is bit-identical to the prior extractor;
both share its edit-demux startup difference from a whole-source decode. The
regression also requires unchanged packet payloads/ticks and exact whole-source
PCM when edit lists are ignored. No timing or audio tolerance was widened.

In a short matched recording fixture, full service View took 2.9-3.4 ms versus
125-134 ms for the old remux alone. View wrote zero media bytes and built about
3.2 KiB of headers. These single-sample results exclude live tail publication,
HTTP/browser latency and whole-machine resource cost. They do not establish
instant first-open or strict loop cutoff.

`replay/fragments.py` frames that stream. It does not encode, remux, select
pictures, modify timestamps, or replace saved/export MP4 files.

The accepted topology is `ftyp, moov, (moof, mdat)+, [mfra]`, including 64-bit
box sizes. Other layouts fail explicitly. `FragmentReader.feed()` publishes
immutable initialization, media, and trailer units with original byte offsets.
It retains at most one unit under the configured 16 MiB default limit; the
caller must drain returned units. `finish()` rejects an unfinished final box
or pair. A framing success does not validate sample references inside `trun`,
identify a random-access point, or establish A/V presentation behavior.

## Evidence and limits

The portable gate is `python tools/run_tests.py tests/test_replay_fragments.py`.
Its 26 cases passed on 2026-09-08. Arbitrary chunk sizes include split 32/64-bit
headers, partial sample data, oversized declarations and truncated EOF. The
actual PyAV/FFmpeg mux test publishes complete media while its encoder run is
still open, retains every original output byte, and decodes all 30 picture
timestamps exactly on their 90 kHz clock.

The continuing-child experiment now also uses the production NUT packet
wrapping, quality registry, picture-duration filter and AAC resampler. It feeds
60 distinct barcoded pictures, including adjacent 90 kHz ticks, then withholds
the input tail without closing stdin. One encoder tees its packets to MPEG-TS
and 100 ms fragmented MP4, so the format comparison cannot be confounded by a
second encode. Software x264, NVIDIA NVENC and AMD AMF all published a decodable
prefix before the tail was released, retaining all 60 picture identities/ticks,
identical decoded YUV pictures, and 96,256 identical overlapping PCM samples per
channel on the original 48 kHz sample clock. QSV was unavailable on this machine.
The smaller fragments did not require more frequent keyframes.

The reproducible gate is `tests/test_replay_fragment_producer.py`; its x264,
NVENC and AMF cases passed on 2026-09-09, with QSV skipped as unavailable. It
generates each picture and PCM chunk directly into the actual NUT pipe, without
a raw recording file. The dual output is a test witness, not a proposal to
duplicate production storage. The initial experiment also tried reduced
input-analysis limits; both variants published a prefix, so that tuning was not
adopted. These tests do not measure real-time capture load, browser audio
presentation, or attempt-completion-to-first-picture latency.

A separate local saved-recording experiment used FFmpeg packet-copy remux with
`delay_moov+default_base_moof+frag_keyframe`, 100 ms fragmentation,
`-copyts`, `-avoid_negative_ts disabled`, and a 90 kHz video track. After using
this reader to split the output, it retained exact PTS/DTS/payload identity for
203 video and 405 audio packets, all 196 visible decoded YUV pictures, and
375,200 overlapping decoded PCM samples per channel. Seven negative-time
reference pictures remained outside the visible interval. This local source
is not a redistributed test fixture; the portable test above proves framing,
not all of those source-specific media claims.

In headless Chromium, that local experiment rendered identical full RGBA
hashes on eight nonmonotonic source/MSE seeks. Recovering each callback to the
source's integer 90 kHz tick removed demuxer microsecond round/truncate
differences without fitting a correction. First presentation used 19 of 82
fragments (485,501 bytes including initialization), before appending the tail.
This proves incremental consumption of prebuilt bytes; it measures no live
completion-to-ready latency or machine savings.

The recovered flash/click test failed. A bounded shared-audio-context follow-up
also remained inconclusive: native/native relative timing matched, but the
clock baseline was invalid; native/MSE differed by about 49 ms, while scheduling
stalls made a one-second source interval span 2.9 seconds of wall time. These
results do not establish either correct native A/V sync or an MSE A/V defect.
The graph observed decoded input samples and emitted silence; it did not
record a speaker or display. No tolerance was widened to turn this into a pass.

A separate positive observation in the same synthetic fixture placed the last
active tone sample at 103,999 for the exclusive Out boundary 104,000 at 48 kHz
(65/30 seconds). This establishes that one AAC crossing-boundary case; it is
not a universal audio-cut guarantee or a passing A/V timing gate.

## Indexed archive connection

`fragmentindex.py` reads the producer's restricted H.264/AAC metadata once per
published fragment: original integer PTS/DTS/durations, track timebases, payload
byte ranges and random-access flags. It validates referenced bytes and refuses
reordered pictures or unsupported edits. It does not decode pictures, launch a
packet-probing process, or change the AAC priming origin. This is a producer
contract, not an arbitrary-upload parser.

`fragmentstore.py` appends complete units to shared extent files. The default
rotation targets are 8 MiB, 8 seconds, or 8,192 samples, at the next video keyframe; these
are rotation thresholds, not strict byte caps. A 131,072-sample index ceiling
evicts old extents from new queries. An encoder that cannot provide a bounded
random-access interval fails explicitly. Existing `SegmentRing` temporary-byte,
free-space and lease rules own disk eviction. Active writer/reader leases can
temporarily exceed the cap; saved media is outside these temporary groups.
Open readers retain their immutable selection when index pressure evicts it
from new queries, and release files before releasing leases on cancellation.
Declared sample counts are capped before allocating their per-sample objects.

Reads binary-search the extent and preceding keyframe, then visit only the
selected samples: O(log E + log K + S), for extents E, keys in an extent K, and
selected samples S. Each selected extent opens once; output uses 64 KiB chunks.
Reads create no per-attempt media file and do not expand the entire session's
sample index. Missing or unfinished intervals fail explicitly. A video keyframe
does not align AAC's grid: when the crossing audio packet is in the preceding
GOP, the reader includes that dependency with the same visible bounds. It does
not synthesize audio or shift clocks to hide missing data.

The existing capture sink now drives this path in
`tests/test_replay_fragment_connection.py`. Software x264, NVIDIA NVENC and AMD
AMF passed readable-prefix-before-capture-stop, independent painted-picture and
ledger-tick identity, nonzero random reads, exact keyframe starts across extent
boundaries, and metadata-pressure eviction with an active reader. The keyframe
start witness failed on all three before the crossing-audio dependency fix.
`tests/test_replay_fragment_archive.py` independently compares every indexed
packet's clock, flags and payload with PyAV, and covers cancellation, disk
pressure, truncation and malformed input. QSV was unavailable.

The native route, source-to-clip edit translation and standalone export are
now connected as described above. Publication errors appear in recorder status;
the sink drains the failed consumer's pipe and uses existing bounded child
recovery. Draining does not declare subsequent bytes successfully retained.
Publication, native indexing and selection spans are in [profiling](profiling.md).

## Separate MSE experiment contracts (not the native default)

1. Recorder startup owns one encoder run and its fragment publication. Attempt
   completion and View select existing bytes; neither starts a per-attempt
   encoder or full-clip preparation job. Emit packet/sample indexes as complete
   fragments arrive, with `run_id`, codec/init identity, track timebases,
   original PTS/DTS/durations, byte ranges and preceding random-access points.
   A codec, size, or run change starts a fresh initialization/decoder boundary.
2. Decoder dependencies and visible first/last picture bounds stay separate.
   The local negative-preroll source needs an explicit +1-second browser offset.
   Setting `appendWindowStart` to its visible start discarded its required GOP
   and produced no buffered video. The initial same-clock UI seam must refuse
   such a descriptor until every consumer translates the offset explicitly.
3. The same-clock descriptor supplies `timestamp_offset_s: 0`,
   `video_timescale: 90000`, decoder-safe media and logical picture times.
   `visible_end_s` is the exclusive start of the next picture, not the selected
   final picture's start. An arbitrary user Out needs translation by the owning
   source index; it must not guess a VFR picture duration.
4. Inputs join `(run_id, source_tick)`. Browser floating-point callback times
   recover that source tick using the declared track timebase; an unmatched
   tick stays unknown. The existing clip timeline cannot silently consume
   translated run timestamps as though they were zero-based clip times.
5. In the local experiment, `endOfStream()` on the currently published finite
   prefix enabled tail seeking that otherwise stalled. A later append reopened
   the source and preserved older picture identities. Reaching a prefix end
   still needs an explicit continuation policy. The exact next-picture cutoff
   followed by EOS held the selected final video picture in this source.
6. Storage owns fragment leases and total temporary-byte accounting. Reading,
   saving, comparison and compilation lease every dependency GOP. Eviction
   invalidates unavailable attempt descriptors honestly. Short synthetic run
   publication, archive random access and leases are tested above; long-session
   live cost, bounded browser eviction and run transitions remain unverified.
   Saving retains the established standalone atomic MP4 path until
   a separate packet-copy export gate proves its presentation/edit bounds.

The framing and MSE segment concepts follow the
[W3C ISO BMFF byte-stream format](https://www.w3.org/TR/mse-byte-stream-format-isobmff/).
The wall-clock instrument's limited interpretation follows the
[Web Audio output timestamp definition](https://www.w3.org/TR/webaudio/#dom-audiocontext-getoutputtimestamp).

## Storage direction and industry precedent

The default architecture uses one continuous encoder, bounded encoded storage,
and attempt descriptors referencing existing media intervals. View serves native
MP4 headers plus referenced sample bytes. Save, manual PB marking's automatic
save, and export materialize a standalone MP4. This keeps retention on disk
without copying the whole attempt merely to review it.

[OBS's replay buffer](https://github.com/obsproject/obs-studio/blob/master/plugins/obs-ffmpeg/obs-ffmpeg-mux.c)
retains encoded packets, purges through keyframe boundaries, and takes packet
references when saving. That supports retaining encoded media and its dependencies;
it does not require retaining a whole practice session in RAM.
[FFmpeg fragmentation](https://ffmpeg.org/ffmpeg-formats.html#Fragmentation)
publishes media and metadata before the output closes. Fragment duration can be
shorter than the keyframe interval. Forcing an IDR for each small fragment would
unnecessarily increase bitrate; seeking still needs the preceding GOP.

[Unreal's replay system](https://dev.epicgames.com/documentation/en-us/unreal-engine/using-the-replay-system-in-unreal-engine)
records replicated game state and reconstructs gameplay later. Our companion
does not control complete emulator, renderer, or audio state. Input-only
reconstruction would change the fidelity contract and cannot replace the exact
recording on that evidence.

The storage target is O(unique encoded bytes + attempt metadata), instead of
unique session media plus O(sum of reviewed clip bytes) temporary copies.
Short fragments should share bounded extent files with indexed byte ranges:
one file per 100 ms would create 36,000 files per hour and reintroduce filesystem
overhead. Extent rotation, decoder dependency leases, unavailable-attempt reporting,
saved-media protection, and the current free-space/byte limits must stay with the
storage owner. Browser retention must also have a bounded decoding window; merely
streaming a whole long session into MSE would move the storage problem into RAM.

## Browser prefix consumption

The opt-in same-clock reader now consumes a response incrementally, submitting
at most 256 KiB per append and awaiting each append before reading more. Replacing,
closing, or failing a source cancels its reader. It no longer retains a second
whole-response ArrayBuffer while appending the file. MSE's decoder buffers still
grow with the appended media, and changing Out currently refetches the source;
this is not yet a bounded range-fetch implementation.

`tests/test_ui_review_stream.py` withholds the response tail while the real drawer
presents and steps pictures from the prefix. This is a controlled test of prebuilt
bytes, not live attempt-completion latency. The native default does not emit
the experimental descriptor yet. Production audio, nonzero-origin translation,
strict variable-duration picture holds, and archive-to-service wiring remain
required gates before changing that default.

The strengthened prefix test passed with independently read barcode and matching
input-inspector identities at both an early picture and a released-tail picture.
It presented before 5,036 of 11,181 bytes arrived (about 19 ms after the fixture's
fetch began in that run). This tiny prebuilt source is not a live latency claim.

The strict VFR witness still fails: at the exclusive .3-second Out, the pre-app
EOS pixel capture sees the preceding picture rather than the required final
hold. Original and fragmented packets have identical PTS/DTS/durations, including
that final sample at tick 18,001 with duration 8,999. A distinguishing paused
seek to .25 seconds displays that final barcode correctly, and Chromium's media
log reports dropping only the two pictures at/after Out. Thus the sample is
available; investigate playback/EOS and the presentation instrument. A finite
native MP4 containing the same pre-Out packets also ended on the preceding
barcode in this fixture, so changing container admission alone has not solved
the hold. These video-only observations do not establish a browser defect or
production audio behavior. Passing the no-extra-picture check alone is
insufficient. No cutoff tolerance has been widened.

A standalone follow-up removes all application review handlers and reproduces
the same preceding-picture EOS in both finite native MP4 and bounded MSE. A
paused .25-second seek still displays the final barcode. In the optional-audio
cases, playback stayed at .1 seconds for the three-second observation despite
`paused=false` and `readyState=4`; these are stalled-clock observations, not A/V
fidelity evidence. The headless Chromium 149.0.7827.55 fixture does not establish
a browser defect or behavior on the user's browser/GPU. The final hold and
production audio criteria remain open.
