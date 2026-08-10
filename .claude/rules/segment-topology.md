---
paths:
  - "src/sm64_events/tracking/topology.py"
  - "src/sm64_events/tracking/segments.py"
  - "tools/measure_topology_cancels.py"
  - "tools/why_cancelled.py"
  - "tools/topology_map.py"
  - "tools/measure_entrance_sweep.py"
---

# Topological segment validity — the world-graph rules

Lifted verbatim out of `.claude/rules/tracking-storage.md` on 2026-08-08 when
that file hit its size ceiling; the map row that points here lives in its
table (`tracking/segments.py`).

## Topological validity

**`SegmentEngine._flush_move`, spec `2026-08-01-topological-segment-validity`.**
Live report: standing in the **Bowser 1 arena**, `WF → SSL` read as ACTIVE
SEGMENT; standing **inside LLL**, `LLL → HMC` read "Step 1 of 1 · Waiting for
Enter Hazy Maze Cave". Both were the matcher, not a stale card — `armed_detail`
is re-derived from the armed set on every view fetch. The 56 seeded castle
movements are `loose`, and loose is transparent to everything but death,
`game_reset`, `session_started` and the staleness deadline, so a warp into
another course was invisible to it by design.

Two rules, judged against `world_connections()` — the SAME table the segment
builder's dropdown filtering uses, read for a second purpose:

1. **A move that is not an edge cancels every armed def.** The Usamune warp
   menu (or a savestate) fabricated it.
2. **A legal move that strictly INCREASES the hop count to a def's next
   required place cancels that def.** Basement → LLL is a real edge, so rule 1
   waves it through; what makes it a wrong turn is that HMC went from 1 hop
   away to 2. Strict increase only — equal is sideways, so a route with two
   shortest paths is never punished for picking either.

Both are SILENT (no attempt row, matching `_feed_waypoint`'s off-route cancel —
a movement that never happened must not bank a failure), and both exempt an arm
that began **at or after** the move, so warping somewhere to practise still
arms what lives there.

**This reverses `can_run_from`'s explicit refusal to consult that table.** Its
objection — "a check derived from that table could only ever be tested against
the table it came from" — is answered by `tools/measure_topology_cancels.py`,
which replays both real journals with the rules on and off and reports every
recorded SUCCESS they would have killed. **Any non-zero count is a missing edge
or a bug, examined one at a time, never a number to accept.**

### The three things that keep it from over-firing

- **`tracking/topology.py::node_for` — subareas count only inside the castle
  interior.** Courses have their own areas (SSL area 2 is the pyramid interior),
  and the graph does not model them. Keying on `(level, area)` everywhere read
  **97.9%** of the live journal's settled moves as off-graph against the true
  **54%** — a silent failure that reads exactly like a broken world table. Has
  its own mutation-proved test.
- **The ONE-FRAME DEFER.** Every castle entry loads the lobby for one poll
  before warping to the real area, all on ONE game frame, so `_pending_move` is
  judged only once the frame advances, taking the LAST candidate of that frame.
  Judged raw, a basement course exit reads as the non-edge "SSL → Lobby", and
  for an upstairs destination the transient lobby is CLOSER than the basement
  (2 hops vs 3) — both rules would fire on a move that never happened. This is
  the same per-frame collapse the design's measurement used, deliberately, so
  the number that justified the feature and the code implementing it cannot
  drift. Recorded on `area_changed` (whose payload names the level and settled
  area outright), never `level_changed` (where `ctx.area` is still the old
  level's); read with `.get()`, since a payload without `level` means position
  unknown and unknown declines to judge.
  **The defer needed a CLOCK, and not having one was worth 27.7 seconds
  (live report 2026-08-02).** "The frame advanced" was only ever observed
  through the next JOURNALED event, and standing still inside a course journals
  nothing — so entering Bowser in the Sky from Upstairs cancelled `Bowser 2 →
  WDW` correctly and the selector kept offering it, the card kept calling it
  ACTIVE SEGMENT, for **832 frames** (`tools/why_cancelled.py` on his own
  session; `data/ui_log.jsonl` has the chip drawn for 27.9 s, the same span).
  Earlier sightings were 96, 116 and 56 frames and read as noise. `SegmentEngine.
  settle(frame)` is the delivery half — `Projector.settle` → `TrackerService.
  settle_frame` → the poller's own `on_frame` hook, wired in `main.py`. Same
  verdict, same silence (no row), so a REPLAY reaching it at the next event
  lands in the same state; the only visible difference is the resurrection
  entry's expiry frame. The notice needs no view refetch to land: `store.js`
  keeps `armedSegs`/`armedOrder` from the notices themselves, and both the
  selector's chip (`stagecontext.armedSegments`) and the card's pin read that.
  The report arrived as "I think each segment just needs to know what conditions
  break it" — the rule that broke it already existed and already fired. **A
  verdict delivered late is indistinguishable, from the outside, from a rule
  that was never written.**
- **Two ways to be unconstrained, neither a special case.**
  `segments.step_node` answers None for a clause naming no place (`key_grabbed`,
  `warp_entered`, `star_grabbed`, `reset_game`, an unpinned `level_exit`), and
  `topology.hops` answers None for an unreachable target; `_next_step_hops`
  turns either into "no constraint". That is what exempts every Bowser fight and
  pipe entry without a maintained list. `segments.declared_nodes(d)` is the
  other: a node the definition names as a step of its own route is never a wrong
  turn, which is how a route that deliberately re-enters a place says so —
  Griffin's chosen discriminator over a dwell-time threshold. It is a SET, not a
  comparison against the arm's live `progress`: the waypoint match and the
  position judgement land on the same game frame but on DIFFERENT events, so a
  correctly-followed waypoint read as a move away from whatever came next and
  the declared re-entry cancelled itself.

### The one disarm the player can undo

`SegmentEngine._cancelled` — `{def id: (_Arm, expiry frame)}`. A topological
cancel remembers where the arm stood, and a REAL anchor at that position
re-arms it. Every other disarm in this engine is final.

Found by the measurement, not by design: journal ids 17926–17940, `Bowser 1 →
WF` armed by the arena exit into the lobby, a 7 s detour into BitDW, back to the
lobby, reset AT THE ARM POSITION, then lobby → WF in 16 s. Redoing that def's
start trigger (`level_exit from=30`) means redoing the whole fight, so the reset
IS how a castle movement is re-run — `_feed_loose` already reads an anchor at the
arm position that way for a live arm, and the rules were taking the loop away for
a cancelled one. Two bounds, both Griffin's:

- **FORFEIT** — a real anchor SOMEWHERE ELSE drops the memory permanently: *"if…
  in the middle of lobby -> wf, I decided to reset to bitdw, I think that's a
  genuine kill of the segment, because we've now gone out of order in a way that
  doesn't make sense for practicing… until I get back to Bowser 1 and trigger it
  from the beginning again"* (2026-08-01). Mirrors `_feed_strict`'s reading of a
  relocated anchor.
- **EXPIRY** — the same measured staleness budget a loose arm gets
  (`budget_frames`), applied to every mode here because a cancelled arm has no
  cancel rules left to bound it. Without a clock, a movement killed hours ago
  would re-arm the next time he happened to reset in the same room.

A normal arm pops the memory: the def is live again by its own start condition,
so the resurrection entry would only be a stale second door.

### Measured 2026-08-02

| journal | events | settled moves | off-graph | successes kept |
|---|---|---|---|---|
| installed exe | 17,424 | 419 | 221 (156 course→course) | **82 / 82** |
| repo checkout | 20,542 | 739 | 341 (235 course→course) | **110 / 112** |

Both losses were read back against the raw journal and ARE the live report,
banked as times: `LLL → HMC` (ids 13672–13687) exited LLL, warped BACK INTO LLL,
then warped LLL → HMC (22 → 7, not an edge either) — 23.8 s spanning the round
trip; `Bowser 2 → Upstairs` (ids 18355–18376) exited the arena to the basement,
spent 30 s inside BitFS, came out and went upstairs — 83 s mostly detour.

**NOT built, and named so it is not re-derived by guess:** the builder still
constrains each clause's own `from`/`to` against `connections` but not ACROSS
steps, so a segment is not yet a valid path by construction. That is Stage 2 of
the spec and carries one open decision — strict adjacency ("the only valid path
out of SSL is Basement") would make the 56 shipped two-step movements
un-editable.

