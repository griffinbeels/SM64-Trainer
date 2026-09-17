# Shared fragments and native replay views

Both capture routes publish one encoded fragment stream into `FragmentArchive`:
the GPU route through `PacketFragmentMux` (compressed packets from the NVENC
helper), the desktop grab through `FfmpegAvSink`. There is no parallel TS
recording or second encoder. The explicit CFR/no-FFmpeg fallback and existing
saved MP4 files retain their previous contracts. Replay plays in the native
`<video>` player.

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
