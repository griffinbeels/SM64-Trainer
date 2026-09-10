---
paths:
  - "src/sm64_events/replay/recorder.py"
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
| 2 | tail becomes available | closed contiguous segments cover span | src/sm64_events/replay/recorder.py | stage timings and ring segment bounds | retained closed segments in scratch ring | wait after completion | measuring an already-cached call |
| 3 | clip cut | checked MP4 and timestamps exist | src/sm64_events/replay/extract.py | profiling stage spans; offline cut wall time | supply exact existing clip to scratch service | encoding/probing dominates | measuring a different source/encoder |
| 4 | map and cache | clip and associated captured state ready | src/sm64_events/replay/service.py | stage spans + cached response | exact cached payload for same attempt | mapping/cache dominates | skips source identity checks |
| 5 | HTTP response | replay payload received | src/sm64_events/server/replay_api.py | request elapsed time | fixture response using same clip/map | queue/serialization delay | timer starts after response |
| 6 | browser readiness | delivered picture and input inspector | src/sm64_events/ui/components/replay.js | in-page request-to-picture and timeline timing | serve retained payload immediately | decode/render delay | counts shell instead of usable media |

## Counterfactual recipe

Use a scratch fixture and immutable copies of the reported attempt. First serve
its already-produced clip and exact payload, then measure to the same usable
drawer sink. This removes hops 2–4 without changing footage or associations.
Then repeat with closed source segments to remove only the tail wait. Probe
remaining cut and map stages separately. Never inject into the live recorder.
If source segments are gone, record that gap instead of reconstructing a timing
claim from a cache hit. Score all retained cases, including resets and successes.

## Failure catalogue

- Native timer-based loop playback can deliver a picture beyond Out before a
  JavaScript callback seeks back. The opt-in fragmented-media experiment bounds
  admitted pictures; production audio and timestamp-offset gates remain open.
