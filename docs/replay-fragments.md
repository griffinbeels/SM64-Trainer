# Incremental replay media: experimental boundary

`replay/fragments.py` frames one FFmpeg fragmented-MP4 byte stream. It is an
unused building block, not a new recording or View path. It does not encode,
remux, select pictures, modify timestamps, or replace saved/export MP4 files.

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

## Contract required before producer integration

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
   invalidates unavailable attempt descriptors honestly. Long-session random
   access, bounded browser eviction, live publication and run transitions remain
   unverified. Saving retains the established standalone atomic MP4 path until
   a separate packet-copy export gate proves its presentation/edit bounds.

The framing and MSE segment concepts follow the
[W3C ISO BMFF byte-stream format](https://www.w3.org/TR/mse-byte-stream-format-isobmff/).
The wall-clock instrument's limited interpretation follows the
[Web Audio output timestamp definition](https://www.w3.org/TR/webaudio/#dom-audiocontext-getoutputtimestamp).
