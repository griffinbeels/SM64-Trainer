---
paths:
  - "src/sm64_events/tracking/segments.py"
  - "src/sm64_events/detectors/igt_clock.py"
  - "src/sm64_events/detectors/counter_epoch.py"
  - "tests/test_segment_igt.py"
  - "tests/test_clock_start.py"
---

# A segment's clock — where it starts, and what number it records

Lifted verbatim out of `.claude/rules/tracking-storage.md` on 2026-08-09 when
that file hit its size ceiling. **`.claude/rules/tracking-storage.md` loads
alongside this** and holds everything else about the matcher, the projector and
storage; `.claude/rules/segment-topology.md` holds the world-graph rules.

Two questions, one subject: WHEN the clock starts (`clock_start`) and WHAT
number is recorded when it stops (`_close`). They share a file because they
share a failure mode — both look right in every test and wrong on his screen.

## When the clock starts — "trigger" vs "move" (round 15 item 3, 2026-08-08)

`SegmentDef.clock_start`, migration adding the column (existing rows default
`"trigger"`). **The 56 seeded movements are `"move"` since 2026-08-08 —
priced first (15 of 88 live attempts re-time, 2 saved PBs, all faster,
biggest shift 3.3 s; one 59 s outlier in the repo journal), then HIS call:
*"I think we should flip to the move clock too, that sounds good."* One
stamp in `_movement_row` covers all 56; every live movement row was
seed_dirty=0, so reconcile refreshes them at the next startup. The 10 legacy tricks, the pipe trio and the 100-coin
engines stay `"trigger"`.** Under
`"move"` the start trigger DETECTS and the SECTION ENTRY it caused starts
the clock — his ruling verbatim: *"the timer doesn't actually START until
mario is able to finally move, aka when Usamune's timer actually resets to
0 when we go to the new section."* Mechanics: `feed` rebases an armed
move-def's `_Arm.clock_frame` to any counter zero within
`CLOCK_START_WINDOW_FRAMES` (90 — measured: his CCM door +51, a pipe +23,
a painting +77, the cage +74; latest in-window zero wins, a load settles
across several), and `_close` reads its origin from it: the closing
event's own igt verbatim when the last zero IS the rebase (= exactly what
Usamune displays; his CCM touches carry igt 77 where the trigger clock
recorded 127), else the delta from the rebase; no rebase = byte-identical
"trigger". The recorder saves `"move"` (with strict); the builder's blank
reads `vocab.clock_starts[0]` and its "Clock starts" select shows the
STORED value. Fixture: journal ids 3930-3933 verbatim in
`tests/test_clock_start.py`, window mutation-proved, PATCH path pinned by
the field-sample gate.

## A segment's time is Usamune's IGT

`SegmentEngine._close` takes the CLOSING EVENT's own `igt_frames` as the
segment's time when there is one, and the `close.frame - arm.start_frame`
delta only as a fallback. The events that carry one: `star_collected` and
`key_grabbed` (since 2026-06-12), `warp_entered` (since 2026-07-31), each
stamped from the shared `detectors/igt_clock.py`; plus `death`, whose payload
carries the RAW counter with no display tick — a known 1-frame inconsistency
against the other three, left alone because it also feeds every STAR death row
and `IgtClock.DISPLAY_TICK`'s applicability at a death has never been
live-gated.

**Live report 2026-07-31**: BitDW "No Reds" displayed `0'35"90` where Usamune
showed `0'35"96` (journal ids 23044→23061, attempt `50000023044`, `rta_frames`
1077 vs 1079). The delta is wrong for two independent reasons, neither of them
a constant you could correct for — the arm frame is where a 60 Hz poll caught
a 30 Hz counter drop (the zero frame, or one after it), and the delta counts
paused frames. Measured distribution over 626 real attempts, and the full
arithmetic: the `rta_frames` clause in `tracking/segments.py`'s module
docstring, and the "A `global_timer` delta is not the IGT" paragraph in
`docs/architecture.md` (the game-behaviour half).

**The precondition is CHECKED, not assumed**: `_last_igt_zero_frame ==
arm.start_frame` — Usamune's counter was zeroed on the very frame the segment
armed and has not been zeroed since. `_zeroes_usamune_igt` is what feeds that
frame: every real-edge `level_changed`/`area_changed`, every anchor, and
`game_reset` — **echoes included**, because a door crossing is invisible to
the MATCHER (the player did not choose it) while still zeroing the counter the
time is read from. This used to be a comment in the docstring saying "none
exists; revisit if one is created", and it was already false: replaying the
user's own journal against the gate moved 5 recorded rows, every one a case
the old code got wrong. `Toad Star (Basement)` had banked **311** frames for a
movement that really took **732** — a post-star save-prompt reload (echo shape
4) re-zeroed Usamune mid-segment, so the star's own igt measured from the
reload. And one `death` event closed both `WF → SSL` and `DDD → BitFS (sub)`
with the *identical* 1267, though they armed minutes apart; they now read 2451
and 3988. No saved PB row was affected (checked against a `Connection.backup`
snapshot).

**AND THE IGT MUST NOT BE LONGER THAN THE SPAN IT COVERS** (round 24,
2026-08-09). The zero-frame precondition is necessary and, since the star
clock started CARRYING a leg across an area warp and adding it back
(`IgtClock.carried_igt_at_xcam`, 2026-08-02), no longer sufficient: a grab
inside a subarea reports the WHOLE STAR, and a [[subsection]] armed on the
spawn into that subarea passes the zero test honestly — Usamune really did
zero on its arm frame — then banks the star's number as its own. His report:
*"it's incorrectly counting THE ENTIRE STAR TIME as the segment time... it
should be about ~13 seconds long."* The delta is an UPPER bound on any igt
measured from the same moment (it counts paused frames the counter does not),
so an igt exceeding it started earlier and the delta is the only honest number
left. `IGT_CARRY_SLACK_FRAMES` (5) keeps the legitimate over-run: his run has
the two cases 2 frames apart and 297 frames apart, so this separates them by
two orders of magnitude rather than by tuning. **Measured with
`tools/measure_target_queue.py --before HEAD` over both journals: 0 target
readings differ, 0 rows lost or gained, and exactly 4 rows re-time** — his two
"Inside the Volcano (Elevator Tour)" successes 676→379 and 684→383 (12.6 s and
12.8 s, which is the ~13 s he expected), and two repo-journal rows sharing one
close at 1008→989. Both halves mutation-proved in `tests/test_segment_igt.py`
(drop the guard and the carried case comes back; drop the slack and ten tests
go red).

Forward-only for the pipe family: historical `warp_entered` rows carry no
`igt_frames` and the raw counter at those frames was never journaled, so
nothing can be backfilled — the pipe PBs saved before this (pb#139
`seg:bitdw-pipe` 1077, the four `BitS Pipe Entry` rows, pb#137/138) stand on
the old clock and are ~1–2 frames cheaper than anything set after it.
`tests/test_segment_igt.py` is the end-to-end proof: real snapshots through
`main.build_detectors()`, journaled the way `service.py` journals, projected
with the SHIPPED seed rows.
