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
two orders of magnitude rather than by tuning.

**AND THE PREFIX IS SUBTRACTED, NOT REFUSED** (round 25, the next day, off his
next session). Falling back to the delta is honest and is NOT what Usamune
shows — a delta counts the star dance and every paused frame, so his volcano
piece read `0'16"60` against the emulator's own `0'13"60` in the same
screenshot. `_banked_before_zero` remembers what the counter read on its way
out of an INVOLUNTARY zero (an `area_changed` deeper into a course, whose
payload carries it), and `_close` subtracts that from a carried igt instead of
discarding it: his grab reported 696, the volcano entry banked 289, and
696 − 289 = **407** against the 408 on screen — one display tick, the standing
relationship. Only an involuntary zero banks, so a run he began by resetting
INSIDE the subarea has nothing to subtract, which is the half he reported as
already correct.

**THE CO-FRAME LOAD RESET MUST NOT WIPE THE BANK**, and the first version let
it: an in-course area load fires a `practice_reset` on the SAME FRAME as the
area edge (anchors.py measured 496 of 825), so the bank was set and cleared
within one frame and the change measured as **zero rows moved on his entire
journal**. A zero at the same frame belongs to that load; only a later one is
him starting over.

**AND THE DISPLAY TICK SURVIVES THE SUBTRACTION.** Both numbers already carry
`IgtClock.DISPLAY_TICK`, so subtracting one from the other removes it twice and
the piece lands exactly one frame — 0.03 s — under Usamune. He reported it with
two samples on top of the first: *"12"93 in usamune, 12"90 in the practice
log... 24"76 for Hot-Foot-It Into the Volcano, but it's detected in our log as
24"73. Looks like we're one frame too early?"* Three independent readings, all
exactly one frame, so the term is the named constant rather than a fudge —
`igt - banked + DISPLAY_TICK`. Re-measured: 3 more rows move, every one by +1,
and `742 → 743` IS his `0'24"73 → 0'24"76`.

**Measured with `tools/measure_target_queue.py --before HEAD`, both journals:
0 target readings differ, 0 rows lost or gained.** Round 24's guard alone
moved 4 rows (676→379, 684→383, and two repo rows 1008→989); the subtraction
on top moves the same LLL rows again to their Usamune numbers — 498→**407**
(the reported one) and three others by exactly +1 frame, which is the
documented amount a delta runs cheap against an igt. Every half is
mutation-proved in `tests/test_segment_igt.py`: drop the guard and the carried
case comes back, drop the slack and ten tests go red, let the co-frame reset
clear the bank and the subtraction silently stops happening.

Forward-only for the pipe family: historical `warp_entered` rows carry no
`igt_frames` and the raw counter at those frames was never journaled, so
nothing can be backfilled — the pipe PBs saved before this (pb#139
`seg:bitdw-pipe` 1077, the four `BitS Pipe Entry` rows, pb#137/138) stand on
the old clock and are ~1–2 frames cheaper than anything set after it.
`tests/test_segment_igt.py` is the end-to-end proof: real snapshots through
`main.build_detectors()`, journaled the way `service.py` journals, projected
with the SHIPPED seed rows.
