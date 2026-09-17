# The MSE review-player experiment (removed 2026-09-16)

An opt-in Media Source Extensions player (`ui/reviewstream.js` and the MSE half
of `ui/reviewsource.js`) was built to bound loop playback at Out. No server
code ever emitted the `review_media` descriptor it needed, so it never ran in
production, and it still lost the strict final hold. It was removed with its
tests (`tests/test_ui_review_stream.py`, `tests/test_ui_loop_cutoff.py`,
`tests/frontend/reviewstream.test.js`); the code is recoverable from commit
5cf5d3c7. The open loop-boundary defect is tracked in
`.claude/rules/chain-replay-readiness.md`. These notes were moved out of
`docs/replay-fragments.md` unchanged.

## Seek and A/V observations

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
