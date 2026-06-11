# Addendum: castle-opened attempts are misattributed to the last star

**Date:** 2026-06-11 · **Status:** implemented — commit 5c8e3d0 (projection castle rule) + follow-up (stateful level detector closes the stale-_level reattach gap)
**Slots in as:** Task 3.5 of `2026-06-11-garbage-runs-markers-progress-ui.md` (touches the same closers as Task 3 — land it AFTER Task 3 to avoid conflicts)

## User report

> When I exit a stage after grabbing a star successfully, it marks it as a
> RESET for the star I was working on. When we enter the castle, we are now
> working on castle movement, rather than the last star, so there should
> never be a reset entry for a star when we're in the castle.

## Root cause

The Projector attributes every failure-closure to `self.target` (the last
grabbed/declared star) with no awareness of where Mario is. Usamune's overall
IGT resets on **castle entry** and again on **painting entry**, so the
AnchorDetector fires `practice_reset` anchors at both moments. The
castle-entry anchor **opens an attempt that is really castle movement**, still
attributed to the stale star target; whatever closes it produces a junk row
for that star.

The main spec's §1 ("Why this is safe for castle/level-entry resets") only
reasoned about the *discard check* on the castle-entry anchor — "nothing is
open, so the check is a no-op". True, but it missed that the same anchor then
*opens* the next attempt. Task 3's no-activity rule does NOT cover this:
walking through the castle is real Mario activity, so `mario_acted` fires and
the castle attempt survives the activity discard.

## Event order (verified from `data/tracker.db`, session 20)

Stage exit and castle-entry anchor land on the **same tick** (detector order
in main.py guarantees LevelChange before Anchor); painting entry resets IGT
~3 s **before** the destination level loads (star-select screen), and course
load fires its own anchor ~1.7 s after `level_changed`:

```
grab (success, target := star)                         # ev 1004/1005-style
level_changed 22→6  +  practice_reset   same frame     # ev 956 + anchor 1485048
    → anchor OPENS attempt in castle, attributed to stale target
[castle movement, IGT runs in castle]
practice_reset (painting entry, level still 6)         # closes castle attempt
    → outcome="reset" for the star            ← THE BUG ROW (attempt 957)
[star select ~3 s]
level_changed 6→22                                     # closes 2nd castle attempt
    → outcome="abandoned" for the star        ← second junk row (attempt 959)
practice_reset (course load)                           # opens the real attempt
```

Confirmed junk rows, all opened by an anchor that fired while `curr_level`
was 6 (castle inside):

| attempt id | session | attributed star | outcome | duration | actually was |
|---|---|---|---|---|---|
| 826 | 19 | LLL-3 | reset | igt 148 | castle movement |
| 829 | 19 | LLL-3 | abandoned | rta 98 | star-select screen |
| 888 | 20 | LLL-3 | abandoned | rta 207 | castle movement |
| 957 | 20 | LLL-2 | reset | igt 148 | castle movement |
| 959 | 20 | LLL-2 | abandoned | rta 99 | star-select screen |

(Attempt 1024 — the 11.5-minute "reset" — is the separate AFK flavor, already
fixed going forward by the pause-streak discard in commit c48b738; its
historical row persists because old payloads lack `paused_frames_before`.)

## Fix design (user rule: open-time castle judgment)

An attempt opened while the current level is a castle level is castle
movement, never a star attempt. Discard it on EVERY non-success closure
(reset, abandoned, hard_reset, death), same discard path as no-op resets.
Successes always count (Toad/MIPS grabs carry their own course/star payload
and are unaffected — `_close_by_grab` never consults `target`).

1. **`memory/addresses.py`** — level-id registry (game facts live here; the
   gCurrLevelNum trap comment already documents level ids):

   ```python
   # gCurrLevelNum values for the three castle hub levels — decomp
   # levels/level_defines.h. 6 (inside) is live-evidenced by our own journal
   # (every stage exit logs level_changed to=6); 16/26 are decomp-sourced.
   CASTLE_LEVELS = frozenset({6, 16, 26})  # inside, grounds, courtyard
   ```

2. **`tracking/projection.py`** — track the level, flag attempts at OPEN time:
   - `__init__`: `self._level: int | None = None` and `self._open_castle = False`
   - anchor branch of `_dispatch`: after `self._open = ev`, set
     `self._open_castle = self._level in CASTLE_LEVELS`
   - `level_changed` branch: close first (old level state judges the closing
     attempt), THEN `self._level = ev.payload["to"]`
   - helper: `_open_is_castle(self) -> bool: return self._open is not None and self._open_castle`
   - `_close_by_reset`, `_close`, `_close_by_death`: discard
     (`self._open = None; return []`) when `_open_is_castle()`. In
     `_close_by_death` this guard sits above the grab-only synthesis so
     anchorless deaths keep the "always meaningful" stance.
   - new docstring caveat: castle-opened attempts are never attributed to a
     star; judgment is at open time via level_changed tracking; `_level`
     starts None (unknown → attribute, so pre-level-detector journals replay
     unchanged).

3. **Back-compat / replay:** journals before session 14 have no
   `level_changed` → `_level` stays None → byte-identical replay. Sessions
   14+ will retroactively DROP the five junk rows above on the next
   reprojection — that is the desired correction, same mechanism as
   clear/restore.

4. **Dust accumulators:** no change needed — the closing boundary event
   zeroes them in `feed()` even when the closure is discarded, so castle
   rollouts/jumps no longer pollute the star's counts (attempt 1024 shows the
   current pollution: a castle rollout counted toward LLL-0).

## Failing tests to write first (`tests/test_projection.py` conventions)

```python
def lvl(id, frame, from_, to):
    return jev(id, "level_changed", frame, {"from": from_, "to": to})

# the user's exact report: grab → exit to castle → enter next painting
def test_castle_period_after_stage_exit_is_not_a_reset_for_the_star():
    attempts = project([
        star(1, 900),
        lvl(2, 1000, 22, 6),                 # stage exit (same tick as anchor)
        jev(3, "practice_reset", 1000, {"igt_frames_before": 900, "mario_acted": True}),
        jev(4, "practice_reset", 1150, {"igt_frames_before": 148, "mario_acted": True}),  # painting entry
    ])
    assert [a.outcome for a in attempts] == ["success"]

def test_star_select_period_is_not_an_abandon_for_the_star():
    attempts = project([
        star(1, 900),
        lvl(2, 1000, 22, 6),
        jev(3, "practice_reset", 1000, {"igt_frames_before": 900, "mario_acted": True}),
        jev(4, "practice_reset", 1150, {"igt_frames_before": 148, "mario_acted": True}),
        lvl(5, 1250, 6, 22),                 # star select ends, course loads
    ])
    assert [a.outcome for a in attempts] == ["success"]

def test_attribution_resumes_for_in_level_anchors():
    attempts = project([
        star(1, 900),
        lvl(2, 1000, 22, 6),
        jev(3, "practice_reset", 1000, {"igt_frames_before": 900, "mario_acted": True}),
        lvl(4, 1250, 6, 22),
        jev(5, "practice_reset", 1300, {"igt_frames_before": 0, "mario_acted": True}),   # course load
        jev(6, "practice_reset", 1700, {"igt_frames_before": 380, "mario_acted": True}), # L-reset
    ])
    assert [a.outcome for a in attempts] == ["success", "reset"]
    assert attempts[1].id == 5 and attempts[1].course_id == 2

def test_exit_mid_attempt_is_still_abandoned_for_the_star():
    # opened in-level, closed by the exit's level_changed: judged by OPEN level
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        lvl(3, 1600, 22, 6),
    ])
    assert attempts[1].outcome == "abandoned" and attempts[1].course_id == 2

def test_success_from_castle_anchor_still_counts():
    attempts = project([
        lvl(1, 900, 22, 6),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        star(3, 1500),                       # Toad/MIPS-style grab
    ])
    assert attempts[0].outcome == "success"

def test_no_level_events_keeps_legacy_attribution():
    # pre-level-detector journals: _level unknown → today's semantics
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "practice_reset", 1400, {"igt_frames_before": 380, "mario_acted": True}),
    ])
    assert attempts[1].outcome == "reset" and attempts[1].course_id == 2
```

Plus a death-closure variant (castle-opened + `death` → discarded;
anchorless death → kept).

## Why not implemented in this session

`tracking/projection.py` is a shared contract (CLAUDE.md: never edit in two
branches/sessions at once) and the concurrent plan-executor session committed
c48b738 to this checkout mid-investigation; its next task (Task 3) edits the
exact closers this fix touches. Land Task 3 first, then this.
