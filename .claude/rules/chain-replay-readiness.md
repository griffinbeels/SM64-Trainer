---
paths:
  - "src/sm64_events/replay/recorder.py"
  - "src/sm64_events/replay/fragmentmedia.py"
  - "src/sm64_events/replay/fragmentstore.py"
  - "src/sm64_events/replay/virtualmp4.py"
  - "src/sm64_events/server/replay_range.py"
  - "src/sm64_events/replay/extract.py"
  - "src/sm64_events/replay/service.py"
  - "src/sm64_events/server/replay_api.py"
  - "src/sm64_events/ui/components/replay.js"
---
# Chain: attempt completion to usable synchronized replay

- **Value:** elapsed time from attempt completion, and separately review click,
  to a playable picture with its checked input timeline.
- **Source truth:** recorded completion event plus retained ring media/input rows.
- **Sink:** the actual drawer's delivered video picture and populated timeline.
- **Clock:** monotonic durations per process; UTC identifies a shared attempt.
  Never subtract unrelated process clocks without an explicit clock correlation.

| # | hop | value is true here as | module | probe | inject | when broken | when probe is broken |
|---|---|---|---|---|---|---|---|
| 1 | attempt ends | recorded event and media span | src/sm64_events/replay/service.py | journal + requested span | completed attempt in scratch database | wrong attempt/span | stale checkout or missing event |
| 2 | tail becomes available | published contiguous fragments cover the attempt end | src/sm64_events/replay/fragmentstore.py | publication spans and both track bounds | retained fragment extents in scratch archive | wait after completion | measuring an already-cached call |
| 3 | native clip representation | headers reference original sample bytes and ticks | src/sm64_events/replay/virtualmp4.py | native-index span and exact payload/PTS checks | same-packet legacy cut vs virtual headers | header or byte-read cost | measuring a different source/encoder |
| 4 | map and cache | clip and associated captured state ready | src/sm64_events/replay/service.py | stage spans + cached response | exact cached payload for same attempt | mapping/cache dominates | skips source identity checks |
| 5 | HTTP response | replay payload received | src/sm64_events/server/replay_api.py | request elapsed time | fixture response using same clip/map | queue/serialization delay | timer starts after response |
| 6 | browser readiness | delivered picture and input inspector | src/sm64_events/ui/components/replay.js | in-page request-to-picture and timeline timing | serve retained payload immediately | decode/render delay | counts shell instead of usable media |

## Counterfactual recipe

Use a scratch fixture and immutable copies of the reported attempt. First serve
its already-produced clip and exact payload, then measure to the same usable
drawer sink. This removes hops 2–4 without changing footage or associations.
Then repeat with already-published fragments to remove only the tail wait. Probe
remaining header, range-read and map stages separately. Legacy segments and cuts
remain compatibility cases; do not mistake them for the normal recording path. Never inject into the live recorder.
If source segments are gone, record that gap instead of reconstructing a timing
claim from a cache hit. Score all retained cases, including resets and successes.

## Failure catalogue

- Native timer-based loop playback can deliver a picture beyond Out before a
  JavaScript callback seeks back. The opt-in fragmented-media experiment bounds
  admitted pictures; production audio and timestamp-offset gates remain open.

## Capture continuity before attempt completion

Readiness starts upstream of View: automatic idle must preserve a healthy GPU
run and a bounded encoded lead-in. `fragmentretention.py` expires only extents
born in the current idle epoch behind the published A/V frontier, with a full
predecessor extent retained. Reset/activity freezes the surviving tail as normal
history. Explicit pause remains capture-off. `tests/test_replay_idle_coverage.py`
checks the first independently decoded reset picture, exact source input key,
AAC and configured pre-roll against the same unfiltered encoded stream; its
synthetic clocks/inert GPU boundary are not live gameplay performance proof.
Runtime details and failure limits: [renderer GPU recording](../../docs/replay-gpu-runtime.md).
