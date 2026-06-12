# Segment Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Composed segments (LBLJ, MIPS Clip, Lakitu Skip, pipe entries, Bowser fights) become first-class practice targets, detected from journaled primitive events and configurable in a builder GUI.

**Architecture:** Detectors journal new primitive facts (`area_changed`, `warp_entered`, `key_grabbed`, `spawned`); a per-definition FSM in the tracking layer composes them into segment attempts, driven by DB-stored definitions — so re-projection makes new definitions retroactive. Target identity generalizes to a tagged key: `("star", c, s)` | `("segment", id)`.

**Tech Stack:** Python 3.12 + uv, FastAPI, pymem, pytest, SQLite; Preact/htm UI (no build step).

**Spec:** `docs/superpowers/specs/2026-06-11-segment-events-design.md` — read it first. Key invariants: journal = facts only; matcher closes BEFORE arming; guards re-evaluate on every arm; silent disarm on foreign `level_changed`; RTA-only timing.

**Run all tests:** `uv run pytest -q` from repo root. Must pass before every commit.

**Conscious deviation from spec:** the spec says validation errors return 400; this plan follows the codebase's established error taxonomy (`ValueError` → 409 via `_http()` in `server/api.py`). Intent (reject invalid definitions) is preserved.

**Replay-system interaction** (video replay merged to master 2026-06-11,
commit `4341d39`, AFTER this plan was first written — verified compatible):
`ReplayService` is pull-based and attempt-id-keyed — `view(attempt_id)` cuts
a clip from the ring buffer using the attempt's `started_utc`/`ended_utc`
span. Segment attempts carry both fields, so segment replays work with NO
changes; the UI replay toggle lives in `AttemptRow`, which `SegmentSection`
reuses. The big segment attempt ids (`jid + 10¹⁰·def_id`) pass the
`clip_attempt_\d+\.mp4` filename pattern. Two consequences for this plan:
(a) Task 7's `main.py` anchor is still exact — the replay wiring sits ABOVE
the unchanged `detectors = [...]` list; (b) saved-clip naming in
`replay/service.py` assumes star identity and IGT — Task 16b (new) makes it
segment-aware. `_attempt_json` now also emits `started_utc` (replay needs
it); Task 13's "gains `segment_id`" edit is unaffected.

---

## Task 1: Registry constants — level ids, action ids, area address

**Files:**
- Modify: `src/sm64_events/memory/addresses.py`

No detector logic yet — just the registry rows every later task imports.

- [ ] **Step 1: Add level-id table and segment-relevant constants**

Append to `src/sm64_events/memory/addresses.py` (near `CASTLE_LEVELS`, which already exists at ~line 156):

```python
# --- Segment-event primitives (spec: docs/superpowers/specs/2026-06-11) ----

# gCurrLevelNum LEVEL ids — decomp levels/level_defines.h DEFINE_LEVEL order
# (1-based). Cross-validated against three live-verified anchors we already
# had: WF=24, SSL=8, castle 6/16/26 — all consistent with this table.
# VERIFY (live gate): the ids the segments below depend on — 7 (HMC),
# 17 (BitDW), 19 (BitFS), 21 (BitS), 23 (DDD), 30/33/34 (Bowser arenas).
LEVEL_NAMES = {
    4: "Big Boo's Haunt", 5: "Cool, Cool Mountain", 6: "Castle Inside",
    7: "Hazy Maze Cave", 8: "Shifting Sand Land", 9: "Bob-omb Battlefield",
    10: "Snowman's Land", 11: "Wet-Dry World", 12: "Jolly Roger Bay",
    13: "Tiny-Huge Island", 14: "Tick Tock Clock", 15: "Rainbow Ride",
    16: "Castle Grounds", 17: "Bowser in the Dark World",
    18: "Vanish Cap Under the Moat", 19: "Bowser in the Fire Sea",
    20: "The Secret Aquarium", 21: "Bowser in the Sky",
    22: "Lethal Lava Land", 23: "Dire, Dire Docks", 24: "Whomp's Fortress",
    26: "Castle Courtyard", 27: "The Princess's Secret Slide",
    28: "Cavern of the Metal Cap", 29: "Tower of the Wing Cap",
    30: "Bowser 1 Arena", 31: "Wing Mario Over the Rainbow",
    33: "Bowser 2 Arena", 34: "Bowser 3 Arena", 36: "Tall, Tall Mountain",
}

LEVEL_BITDW, LEVEL_BITFS, LEVEL_BITS = 17, 19, 21
LEVEL_HMC, LEVEL_DDD = 7, 23
LEVEL_CASTLE_INSIDE, LEVEL_CASTLE_GROUNDS = 6, 16
BOWSER_1_ARENA, BOWSER_2_ARENA, BOWSER_3_ARENA = 30, 33, 34

# Key grabs enter the same star-dance actions as stars (see STAR_GRAB_ACTIONS
# comment above). In these two arenas the grab is a KEY, not a star — the
# key detector claims it and star_grab must ignore it (B3's grand star IS a
# star and stays with star_grab). VERIFY (live gate): key-grab behavior of
# gLastCompletedCourseNum/StarNum.
KEY_GRAB_LEVELS = frozenset({BOWSER_1_ARENA, BOWSER_2_ARENA})

# Warp-entry actions — decomp include/sm64.h, quoted verbatim from
# n64decomp/sm64 master, fetched 2026-06-11. VERIFY (live gate): which of
# these fires on the BitDW/BitFS pipe touch and the BitS funnel.
ACT_DISAPPEARED = 0x00001300       # generic "Mario left the world" (pipes, some warps)
ACT_TELEPORT_FADE_OUT = 0x00001336
WARP_ENTRY_ACTIONS = frozenset({ACT_DISAPPEARED, ACT_TELEPORT_FADE_OUT})

# Spawn actions — same decomp fetch. The file-select spawn on Castle Grounds
# plays the Lakitu intro (ACT_INTRO_CUTSCENE); leaving that action = player
# gains control. The SPAWN_* group covers non-intro spawn-ins. VERIFY (live
# gate): which edge fires on a fresh file-select spawn.
ACT_INTRO_CUTSCENE = 0x04001301
ACT_SPAWN_SPIN_AIRBORNE = 0x00001924
ACT_SPAWN_SPIN_LANDING = 0x00001325
ACT_SPAWN_NO_SPIN_AIRBORNE = 0x00001932
ACT_SPAWN_NO_SPIN_LANDING = 0x00001333
SPAWN_ACTIONS = frozenset({ACT_SPAWN_SPIN_AIRBORNE, ACT_SPAWN_SPIN_LANDING,
                           ACT_SPAWN_NO_SPIN_AIRBORNE,
                           ACT_SPAWN_NO_SPIN_LANDING})

# gCurrAreaIndex (s16) — castle lobby/upstairs/basement are AREAS of level 6,
# not levels. NO static source for this address: locate it live (Step 2)
# before trusting area events. VERIFY (live gate): address + castle area
# mapping (expected 1=lobby, 2=upstairs, 3=basement — confirm all three).
CURR_AREA = 0x0  # PLACEHOLDER-BY-DESIGN: replaced by Step 2's live hunt
CASTLE_AREA_NAMES = {1: "Lobby", 2: "Upstairs", 3: "Basement"}
```

- [ ] **Step 2: Locate `gCurrAreaIndex` live (needs human at the emulator)**

This is the one address with no decomp-derivable value. Use the existing playbook (docs/architecture.md → Memory hunting):

Run: `uv run python tools/hunt_value.py` hunting value `1` while standing in the lobby, re-filter with `2` upstairs, `3` in the basement (enter areas via the castle doors). Expect a handful of candidates; confirm with `uv run python tools/watch_timer.py ADDR:u16` across: pause (no change), level change (changes to new level's spawn area), savestate load (follows).

Replace `CURR_AREA = 0x0` with the found address + source comment. **If the human is not available now**: leave `0x0`, skip wiring the snapshot read in Task 2 Step 3 (keep the defaulted field), and do the hunt during Task 17's live gate — every other task proceeds, since detector/matcher tests use fake snapshots.

- [ ] **Step 3: Run full suite to confirm no import breakage**

Run: `uv run pytest -q`
Expected: all pass (constants only).

- [ ] **Step 4: Commit**

```bash
git add src/sm64_events/memory/addresses.py
git commit -m "feat: segment-primitive registry rows - level ids, warp/spawn actions, area address (VERIFY)"
```

---

## Task 2: Snapshot gains `curr_area`

**Files:**
- Modify: `src/sm64_events/core/snapshot.py`
- Test: `tests/test_snapshot.py` (create if absent)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snapshot.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot


def test_curr_area_defaults_to_zero_for_old_call_sites():
    s = GameSnapshot(wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
                     global_timer=1, mario_action=0, mario_action_timer=0,
                     num_stars=0, last_completed_course=0,
                     last_completed_star=0)
    assert s.curr_area == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_snapshot.py -q`
Expected: FAIL — `GameSnapshot` has no `curr_area`.

- [ ] **Step 3: Implement**

In `core/snapshot.py` add to the defaulted block of `GameSnapshot`:

```python
    curr_area: int = 0     # gCurrAreaIndex: per-level area (castle lobby/upstairs/basement) — see addresses.py
```

And in `SnapshotReader.read()` add (ONLY if Task 1 Step 2 pinned the address; otherwise leave the reader untouched until the live gate):

```python
            curr_area=m.read_s16(A.CURR_AREA),
```

- [ ] **Step 4: Run tests** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/snapshot.py tests/test_snapshot.py
git commit -m "feat: snapshot samples gCurrAreaIndex (defaulted field)"
```

---

## Task 3: `area_changed` detector

**Files:**
- Create: `src/sm64_events/detectors/area.py`
- Test: `tests/test_area.py`

Mirrors `detectors/level.py` exactly — read its docstring first; the
last-EMITTED discipline (establishing + corrective events) is load-bearing
for journal-derived state.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_area.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.area import AreaChangeDetector


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
        global_timer=1000, mario_action=0x0C400201, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        curr_level=6, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_area_change_emits_event_with_level_from_to():
    d = AreaChangeDetector()
    d.process(snap(curr_area=1), snap(curr_area=1))   # establish (1 event)
    events = d.process(snap(curr_area=1), snap(curr_area=2))
    assert len(events) == 1
    assert events[0].type == "area_changed"
    assert events[0].payload == {"level": 6, "from": 1, "to": 2}


def test_first_pair_emits_establishing_event_from_may_equal_to():
    events = AreaChangeDetector().process(snap(curr_area=1), snap(curr_area=1))
    assert len(events) == 1
    assert events[0].payload == {"level": 6, "from": 1, "to": 1}


def test_no_event_while_area_stable_after_establishing():
    d = AreaChangeDetector()
    d.process(snap(), snap())
    assert d.process(snap(), snap()) == []


def test_level_change_re_establishes_area_for_new_level():
    d = AreaChangeDetector()
    d.process(snap(), snap())                          # castle area 1
    events = d.process(snap(), snap(curr_level=17, curr_area=1))
    assert len(events) == 1                            # same area NUMBER, new level
    assert events[0].payload["level"] == 17
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_area.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# src/sm64_events/detectors/area.py
"""area_changed: (gCurrLevelNum, gCurrAreaIndex) edge. The segment matcher's
area_enter trigger (castle lobby/upstairs/basement are AREAS of level 6)
depends on journal-derived area state never running stale, so this detector
copies level.py's last-EMITTED discipline verbatim: establishing event on the
first pair (from may equal to), corrective event after attach gaps, keyed by
(level, area) so a level change re-establishes the new level's area."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot


class AreaChangeDetector:
    def __init__(self):
        self._last_emitted: tuple[int, int] | None = None  # (level, area)

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        key = (curr.curr_level, curr.curr_area)
        if key == self._last_emitted:
            return []
        prior = (self._last_emitted[1]
                 if self._last_emitted is not None
                 and self._last_emitted[0] == curr.curr_level
                 else prev.curr_area)
        self._last_emitted = key
        return [Event(type="area_changed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"level": curr.curr_level,
                               "from": prior, "to": curr.curr_area})]
```

- [ ] **Step 4: Run** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/detectors/area.py tests/test_area.py
git commit -m "feat: area_changed detector - castle areas are journal facts"
```

---

## Task 4: `warp_entered` detector

**Files:**
- Create: `src/sm64_events/detectors/warp.py`
- Test: `tests/test_warp.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_warp.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.warp import WarpDetector
from sm64_events.memory.addresses import ACT_DISAPPEARED

ACT_IDLE = 0x0C400201


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
        global_timer=2000, mario_action=ACT_IDLE, mario_action_timer=0,
        num_stars=8, last_completed_course=1, last_completed_star=1,
        curr_level=17, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_edge_into_warp_action_emits_warp_entered():
    events = WarpDetector().process(snap(), snap(mario_action=ACT_DISAPPEARED))
    assert len(events) == 1
    assert events[0].type == "warp_entered"
    assert events[0].payload == {"level": 17, "area": 1,
                                 "action": ACT_DISAPPEARED}


def test_no_event_while_warp_action_persists():
    d = WarpDetector()
    d.process(snap(), snap(mario_action=ACT_DISAPPEARED))
    assert d.process(snap(mario_action=ACT_DISAPPEARED),
                     snap(mario_action=ACT_DISAPPEARED)) == []
```

- [ ] **Step 2: Run** — FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# src/sm64_events/detectors/warp.py
"""warp_entered: edge into a warp-entry action (pipe touch, teleporter).
The community-comparable moment for 'entered the pipe' segments — the level
edge that follows adds constant fade time, so the matcher anchors on this.
Stateless edge on the already-sampled mario_action; level/area context rides
in the payload so triggers can scope it."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import WARP_ENTRY_ACTIONS


class WarpDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        entered = (curr.mario_action in WARP_ENTRY_ACTIONS
                   and prev.mario_action not in WARP_ENTRY_ACTIONS)
        if not entered:
            return []
        return [Event(type="warp_entered", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"level": curr.curr_level,
                               "area": curr.curr_area,
                               "action": curr.mario_action})]
```

- [ ] **Step 4: Run** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/detectors/warp.py tests/test_warp.py
git commit -m "feat: warp_entered detector - pipe touch is the segment end anchor"
```

---

## Task 5: `key_grabbed` detector + star_grab key guard (latent-bug fix)

**Files:**
- Create: `src/sm64_events/detectors/key.py`
- Modify: `src/sm64_events/detectors/star_grab.py`
- Test: `tests/test_key.py`; modify `tests/test_star_grab.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_key.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.key import KeyGrabDetector
from sm64_events.memory.addresses import ACT_STAR_DANCE_EXIT, BOWSER_1_ARENA

ACT_IDLE = 0x0C400201


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
        global_timer=3000, mario_action=ACT_IDLE, mario_action_timer=0,
        num_stars=8, last_completed_course=1, last_completed_star=1,
        curr_level=BOWSER_1_ARENA, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_grab_action_in_bowser_arena_is_a_key():
    events = KeyGrabDetector().process(
        snap(), snap(mario_action=ACT_STAR_DANCE_EXIT))
    assert len(events) == 1
    assert events[0].type == "key_grabbed"
    assert events[0].payload == {"level": BOWSER_1_ARENA, "which": "bitdw"}


def test_grab_action_outside_arena_is_not_a_key():
    events = KeyGrabDetector().process(
        snap(curr_level=24), snap(curr_level=24,
                                  mario_action=ACT_STAR_DANCE_EXIT))
    assert events == []
```

And the regression in `tests/test_star_grab.py` (uses that file's existing
`snap()` fixture — add `curr_level` override support is already free since
`snap(**overrides)` passes through):

```python
def test_key_grab_in_bowser_arena_does_not_emit_star_collected():
    from sm64_events.memory.addresses import BOWSER_1_ARENA
    d = StarGrabDetector()
    events = d.process(snap(curr_level=BOWSER_1_ARENA),
                       snap(curr_level=BOWSER_1_ARENA,
                            mario_action=ACT_STAR_DANCE_EXIT))
    assert events == []
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_key.py tests/test_star_grab.py -q` → FAIL (module missing; star_grab emits a misattributed star).

- [ ] **Step 3: Implement**

```python
# src/sm64_events/detectors/key.py
"""key_grabbed: the star-dance actions fire for keys too (addresses.py,
STAR_GRAB_ACTIONS comment) — in the Bowser 1/2 arenas the grab IS a key.
This detector claims those; star_grab.py carries the inverse guard so a key
is never journaled as a misattributed star_collected (gLastCompleted* may be
stale from the previous star at that moment — VERIFY note in addresses.py).
B3's grand star is a real star and stays with star_grab."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import (BOWSER_1_ARENA, KEY_GRAB_LEVELS,
                                          STAR_GRAB_ACTIONS)


class KeyGrabDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        entered = (curr.mario_action in STAR_GRAB_ACTIONS
                   and prev.mario_action not in STAR_GRAB_ACTIONS)
        if not entered or curr.curr_level not in KEY_GRAB_LEVELS:
            return []
        which = "bitdw" if curr.curr_level == BOWSER_1_ARENA else "bitfs"
        return [Event(type="key_grabbed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"level": curr.curr_level, "which": which})]
```

In `star_grab.py` `_detect()`, directly after the `entered` check (before the
`star_id` read), add:

```python
        if curr.curr_level in KEY_GRAB_LEVELS:
            return []  # Bowser key, not a star — detectors/key.py owns it
```

with the import `from sm64_events.memory.addresses import KEY_GRAB_LEVELS, ...`
merged into the existing addresses import.

- [ ] **Step 4: Run** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/detectors/key.py src/sm64_events/detectors/star_grab.py tests/test_key.py tests/test_star_grab.py
git commit -m "feat: key_grabbed detector; fix star_grab misattributing Bowser keys as stars"
```

---

## Task 6: `spawned` detector + `num_stars` on star_collected

**Files:**
- Create: `src/sm64_events/detectors/spawn.py`
- Modify: `src/sm64_events/detectors/star_grab.py` (one payload field)
- Test: `tests/test_spawn.py`; one assert in `tests/test_star_grab.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_spawn.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.spawn import SpawnDetector
from sm64_events.memory.addresses import (ACT_INTRO_CUTSCENE,
                                          ACT_SPAWN_SPIN_AIRBORNE,
                                          LEVEL_CASTLE_GROUNDS)

ACT_IDLE = 0x0C400201


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
        global_timer=500, mario_action=ACT_IDLE, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
        curr_level=LEVEL_CASTLE_GROUNDS, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_leaving_intro_cutscene_emits_spawned_intro():
    events = SpawnDetector().process(
        snap(mario_action=ACT_INTRO_CUTSCENE), snap())
    assert len(events) == 1
    assert events[0].type == "spawned"
    assert events[0].payload == {"level": LEVEL_CASTLE_GROUNDS,
                                 "kind": "intro"}


def test_edge_into_spawn_action_emits_spawned_spawn():
    events = SpawnDetector().process(
        snap(), snap(mario_action=ACT_SPAWN_SPIN_AIRBORNE))
    assert events[0].payload["kind"] == "spawn"


def test_idle_to_idle_is_silent():
    assert SpawnDetector().process(snap(), snap()) == []
```

In `tests/test_star_grab.py`, extend the existing success-payload test (the
one asserting `course_id`/`star_id`) with one line:

```python
    assert ev.payload["num_stars"] == curr_num_stars_used_by_that_test
```

(match the `num_stars` the test's `snap()` uses — default fixture is 5).

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement**

```python
# src/sm64_events/detectors/spawn.py
"""spawned: Mario gained control at a spawn-in. Two observable shapes
(both VERIFY at the live gate — addresses.py):
- kind="intro": edge OUT of ACT_INTRO_CUTSCENE (file-select spawn; the
  Lakitu Skip start anchor — control begins when the cutscene action ends)
- kind="spawn": edge INTO a SPAWN_* action (non-intro spawn-ins)
Spurious grounds spawns (e.g. cannon exits) are harmless: segment starts
re-arm/disarm without recording rows."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import ACT_INTRO_CUTSCENE, SPAWN_ACTIONS


class SpawnDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if (prev.mario_action == ACT_INTRO_CUTSCENE
                and curr.mario_action != ACT_INTRO_CUTSCENE):
            kind = "intro"
        elif (curr.mario_action in SPAWN_ACTIONS
                and prev.mario_action not in SPAWN_ACTIONS):
            kind = "spawn"
        else:
            return []
        return [Event(type="spawned", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"level": curr.curr_level, "kind": kind})]
```

In `star_grab.py` `_detect()` payload dict, add one field (enables future
star-count guards; spec §Trigger vocabulary):

```python
                "num_stars": curr.num_stars,
```

- [ ] **Step 4: Run** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/detectors/spawn.py src/sm64_events/detectors/star_grab.py tests/test_spawn.py tests/test_star_grab.py
git commit -m "feat: spawned detector; star_collected carries num_stars for star-count guards"
```

---

## Task 7: Wire the four detectors into the poller

**Files:**
- Modify: `src/sm64_events/main.py`

- [ ] **Step 1: Add detectors to `build()`**

The current list (order is load-bearing — see the comment above it in
main.py):

```python
    detectors = [GameResetDetector(), LevelChangeDetector(), AnchorDetector(),
                 DeathDetector(), DustTrickDetector(), StarGrabDetector()]
```

becomes:

```python
    # New primitives slot between level and anchors: area follows level
    # (same establishing discipline); warp/key/spawn are stateless edges and
    # must precede grabs so a same-tick key claim beats star attribution.
    detectors = [GameResetDetector(), LevelChangeDetector(),
                 AreaChangeDetector(), AnchorDetector(), DeathDetector(),
                 DustTrickDetector(), WarpDetector(), KeyGrabDetector(),
                 SpawnDetector(), StarGrabDetector()]
```

with imports:

```python
from sm64_events.detectors.area import AreaChangeDetector
from sm64_events.detectors.key import KeyGrabDetector
from sm64_events.detectors.spawn import SpawnDetector
from sm64_events.detectors.warp import WarpDetector
```

- [ ] **Step 2: Run** — `uv run pytest -q` → all pass (wiring only; poller tests exercise the list generically).

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/main.py
git commit -m "feat: wire area/warp/key/spawn detectors into the poll loop"
```

---

## Task 8: Storage — migration v4, `Attempt.segment_id`, segment_defs CRUD, seeds

**Files:**
- Modify: `src/sm64_events/storage/db.py`, `src/sm64_events/tracking/projection.py` (dataclass field only)
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_storage.py`, using its existing `make_db`)

```python
def test_migration_v4_seeds_ten_segment_definitions(tmp_path):
    db = make_db(tmp_path)
    defs = db.segment_defs()
    assert len(defs) == 10
    lblj = next(d for d in defs if d["name"] == "LBLJ")
    assert lblj["enabled"] is True
    assert lblj["start_triggers"] == [{"type": "level_enter", "to": 6, "from": 16}]
    assert lblj["end_triggers"] == [{"type": "level_enter", "to": 17}]


def test_segment_def_crud_roundtrip(tmp_path):
    db = make_db(tmp_path)
    sid = db.insert_segment_def("Test", [{"type": "spawned"}],
                                [{"type": "level_enter", "to": 6}], [],
                                "2026-06-11T00:00:00Z")
    db.update_segment_def(sid, name="Test2", enabled=False)
    d = next(d for d in db.segment_defs() if d["id"] == sid)
    assert d["name"] == "Test2" and d["enabled"] is False
    db.delete_segment_def(sid)
    assert all(d["id"] != sid for d in db.segment_defs())


def test_attempts_roundtrip_preserves_segment_id(tmp_path):
    db = make_db(tmp_path)
    a = make_attempt(id=5, segment_id=3, course_id=None, star_id=None,
                     rta_frames=88)   # use/extend this file's attempt factory
    db.upsert_attempt(a)
    assert db.attempts()[0].segment_id == 3


def test_pb_accepts_segment_keying_and_null_course(tmp_path):
    db = make_db(tmp_path)
    db.insert_pb(course_id=None, star_id=None, strat_tag=None,
                 timer_mode="rta", frames=85, attempt_id=None,
                 saved_utc="2026-06-11T00:00:00Z", segment_id=1)
    row = db.pbs()[0]
    assert row["segment_id"] == 1 and row["course_id"] is None
```

(If `tests/test_storage.py` has no attempt factory, add one local helper that
fills every `Attempt` field with defaults and applies overrides — mirror the
dataclass field list.)

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement**

`tracking/projection.py` — append a defaulted field to `Attempt` (same
pattern as the dust-trick counters):

```python
    segment_id: int | None = None  # set => segment attempt; course/star None
```

`storage/db.py` — append migration v4 to `MIGRATIONS` (seed timestamps are
the migration date by design — seeds are fixtures, not user actions):

```python
    # v4 — segment events: definitions table, attempt linkage, kind-aware PBs
    """
    CREATE TABLE IF NOT EXISTS segment_defs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      start_triggers TEXT NOT NULL,
      end_triggers TEXT NOT NULL,
      guards TEXT NOT NULL DEFAULT '[]',
      created_utc TEXT NOT NULL
    );
    ALTER TABLE attempts ADD COLUMN segment_id INTEGER;
    CREATE TABLE pbs_v2 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      course_id INTEGER, star_id INTEGER, segment_id INTEGER, strat_tag TEXT,
      timer_mode TEXT NOT NULL, frames INTEGER NOT NULL,
      attempt_id INTEGER, saved_utc TEXT NOT NULL
    );
    INSERT INTO pbs_v2 (id, course_id, star_id, strat_tag, timer_mode,
                        frames, attempt_id, saved_utc)
      SELECT id, course_id, star_id, strat_tag, timer_mode, frames,
             attempt_id, saved_utc FROM pbs;
    DROP TABLE pbs;
    ALTER TABLE pbs_v2 RENAME TO pbs;
    INSERT INTO segment_defs (name, enabled, start_triggers, end_triggers, guards, created_utc) VALUES
      ('LBLJ', 1, '[{"type":"level_enter","to":6,"from":16}]', '[{"type":"level_enter","to":17}]', '[]', '2026-06-11T00:00:00Z'),
      ('MIPS Clip', 1, '[{"type":"level_exit","from":7,"to":6}]', '[{"type":"level_enter","to":23}]', '[]', '2026-06-11T00:00:00Z'),
      ('Lakitu Skip', 1, '[{"type":"spawned","level":16}]', '[{"type":"level_enter","to":6}]', '[]', '2026-06-11T00:00:00Z'),
      ('BitS Entry', 1, '[{"type":"area_enter","level":6,"area":2}]', '[{"type":"level_enter","to":21}]', '[]', '2026-06-11T00:00:00Z'),
      ('BitDW Pipe Entry', 1, '[{"type":"level_enter","to":17},{"type":"attempt_anchor","level":17}]', '[{"type":"warp_entered","level":17}]', '[]', '2026-06-11T00:00:00Z'),
      ('BitFS Pipe Entry', 1, '[{"type":"level_enter","to":19},{"type":"attempt_anchor","level":19}]', '[{"type":"warp_entered","level":19}]', '[]', '2026-06-11T00:00:00Z'),
      ('BitS Pipe Entry', 1, '[{"type":"level_enter","to":21},{"type":"attempt_anchor","level":21}]', '[{"type":"warp_entered","level":21}]', '[]', '2026-06-11T00:00:00Z'),
      ('Bowser 1', 1, '[{"type":"level_enter","to":30},{"type":"attempt_anchor","level":30}]', '[{"type":"key_grabbed","level":30}]', '[]', '2026-06-11T00:00:00Z'),
      ('Bowser 2', 1, '[{"type":"level_enter","to":33},{"type":"attempt_anchor","level":33}]', '[{"type":"key_grabbed","level":33}]', '[]', '2026-06-11T00:00:00Z'),
      ('Bowser 3', 1, '[{"type":"level_enter","to":34},{"type":"attempt_anchor","level":34}]', '[{"type":"star_grabbed"}]', '[]', '2026-06-11T00:00:00Z');
    """,
```

Extend `_ATTEMPT_COLS` (append `"segment_id"`) and `_attempt_params` (append
`a.segment_id`). Add CRUD + adjust `insert_pb`:

```python
    # -- segment definitions -------------------------------------------------
    def segment_defs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM segment_defs ORDER BY id").fetchall()
        return [{"id": r["id"], "name": r["name"],
                 "enabled": bool(r["enabled"]),
                 "start_triggers": json.loads(r["start_triggers"]),
                 "end_triggers": json.loads(r["end_triggers"]),
                 "guards": json.loads(r["guards"]),
                 "created_utc": r["created_utc"]} for r in rows]

    def insert_segment_def(self, name: str, start_triggers: list,
                           end_triggers: list, guards: list,
                           created_utc: str, enabled: bool = True) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO segment_defs (name, enabled, start_triggers,"
                " end_triggers, guards, created_utc) VALUES (?,?,?,?,?,?)",
                (name, int(enabled), json.dumps(start_triggers),
                 json.dumps(end_triggers), json.dumps(guards), created_utc))
            self._conn.commit()
            return cur.lastrowid

    def update_segment_def(self, def_id: int, **fields) -> None:
        cols = {"name": lambda v: v, "enabled": int,
                "start_triggers": json.dumps, "end_triggers": json.dumps,
                "guards": json.dumps}
        sets, vals = [], []
        for k, conv in cols.items():
            if k in fields:
                sets.append(f"{k}=?"); vals.append(conv(fields[k]))
        if not sets:
            return
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE segment_defs SET {','.join(sets)} WHERE id=?",
                (*vals, def_id))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"segment {def_id} not found")

    def delete_segment_def(self, def_id: int) -> None:
        with self._lock:
            cur = self._conn.execute("DELETE FROM segment_defs WHERE id=?",
                                     (def_id,))
            self._conn.execute("DELETE FROM pbs WHERE segment_id=?",
                               (def_id,))  # spec: cascade — nothing to refer to
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"segment {def_id} not found")
```

`insert_pb` gains a keyword (callers updated in Task 12): signature becomes
`course_id: int | None, star_id: int | None, ...` plus
`segment_id: int | None = None`, and the INSERT lists the extra column.

- [ ] **Step 4: Run** — `uv run pytest -q` → all pass (existing storage tests
  confirm the pbs rebuild preserved rows).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/storage/db.py src/sm64_events/tracking/projection.py tests/test_storage.py
git commit -m "feat: migration v4 - segment_defs + seeds, attempts.segment_id, kind-aware pbs"
```

---

## Task 9: Trigger vocabulary registry + definition validation

**Files:**
- Create: `src/sm64_events/tracking/segments.py`
- Test: `tests/test_segments.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_segments.py
import pytest

from sm64_events.storage.db import EventRow
from sm64_events.tracking.segments import (MatchContext, SegmentDef,
                                           validate_definition, vocab)

W = "2026-06-11T12:00:00Z"


def jev(id, type, frame, payload=None, session_id=1):
    # local copy of test_projection.py's factory (tests/ is not a package)
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=W, payload=payload or {})


def test_validate_accepts_a_seed_shaped_definition():
    validate_definition({
        "name": "LBLJ",
        "start_triggers": [{"type": "level_enter", "to": 6, "from": 16}],
        "end_triggers": [{"type": "level_enter", "to": 17}],
        "guards": []})  # no raise


def test_validate_rejects_unknown_trigger_type():
    with pytest.raises(ValueError, match="unknown trigger type"):
        validate_definition({"name": "x",
                             "start_triggers": [{"type": "nope"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": []})


def test_validate_rejects_missing_required_param():
    with pytest.raises(ValueError, match="level_enter"):
        validate_definition({"name": "x",
                             "start_triggers": [{"type": "level_enter"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": []})


def test_vocab_lists_triggers_guards_and_level_enum():
    v = vocab()
    keys = {t["key"] for t in v["triggers"]}
    assert {"level_enter", "level_exit", "area_enter", "warp_entered",
            "key_grabbed", "star_grabbed", "spawned",
            "attempt_anchor"} <= keys
    assert v["levels"]["17"] == "Bowser in the Dark World"
    assert {g["key"] for g in v["guards"]} == {"prev_level",
                                               "star_count_min",
                                               "star_count_max"}
```

- [ ] **Step 2: Run** — FAIL (module missing).

- [ ] **Step 3: Implement the registry half of `tracking/segments.py`**

```python
# src/sm64_events/tracking/segments.py
"""Segment trigger vocabulary + matcher engine (spec 2026-06-11).

ONE registry: TRIGGERS/GUARDS drive (a) definition validation at the API
boundary, (b) the matcher, (c) GET /api/segments/vocab that renders the
builder GUI. Adding a trigger type = one TriggerType row here.

Matcher invariants (spec §Matcher semantics — tests are the contract):
- closures (success/failure) process BEFORE arming; one event may close an
  attempt AND re-arm the next (practice_reset in an attempt_anchor segment)
- guards re-evaluate on EVERY arm and re-arm
- re-firing a start trigger while armed re-arms (timer restarts, no row)
- level_changed matching neither start nor end disarms silently (no row);
  area_changed and session_started never record rows
- failure rows only on practice_reset/state_loaded (reset), death,
  game_reset (hard_reset); AFK closures (paused >= 150 frames) discard
- rta_frames = close.frame - start_frame; a would-be-negative value
  discards the attempt (self-heal, domain rule 4)
"""
from dataclasses import dataclass
from typing import Callable

from sm64_events.memory.addresses import CASTLE_AREA_NAMES, LEVEL_NAMES

_AFK_PAUSE_FRAMES = 150  # mirrors the star-side AFK discard (projection.py)

# Segment attempt ids live in a disjoint namespace from star attempt ids
# (which are raw journal ids): id = arm-event journal id + OFFSET * def_id.
# Stable across rebuilds, unique across defs armed by the same event, and
# the underlying journal id (for recency ordering) is id % OFFSET.
SEGMENT_ATTEMPT_OFFSET = 10 ** 10


@dataclass(frozen=True)
class MatchContext:
    level: int | None        # tracked level AFTER this event applied
    prev_level: int | None   # tracked level BEFORE this event
    num_stars: int | None    # last star_collected payload num_stars; None = unknown


@dataclass(frozen=True)
class SegmentDef:
    id: int
    name: str
    enabled: bool
    start_triggers: list
    end_triggers: list
    guards: list


@dataclass(frozen=True)
class TriggerType:
    key: str
    label: str
    params: dict  # name -> {"kind": "level"|"area"|"course"|"star"|"int", "required": bool}
    match: Callable[[dict, object, MatchContext], bool]


def _real_edge(ev) -> bool:
    # establishing/corrective level & area events may carry from == to;
    # those are bookkeeping, not movement — never an anchor.
    return ev.payload.get("from") != ev.payload.get("to")


TRIGGERS: dict[str, TriggerType] = {t.key: t for t in [
    TriggerType("level_enter", "You enter level",
                {"to": {"kind": "level", "required": True},
                 "from": {"kind": "level", "required": False}},
                lambda p, ev, ctx: ev.type == "level_changed" and _real_edge(ev)
                and ev.payload["to"] == p["to"]
                and (p.get("from") is None or ev.payload["from"] == p["from"])),
    TriggerType("level_exit", "You exit level",
                {"from": {"kind": "level", "required": True},
                 "to": {"kind": "level", "required": False}},
                lambda p, ev, ctx: ev.type == "level_changed" and _real_edge(ev)
                and ev.payload["from"] == p["from"]
                and (p.get("to") is None or ev.payload["to"] == p["to"])),
    TriggerType("area_enter", "You enter area",
                {"level": {"kind": "level", "required": True},
                 "area": {"kind": "area", "required": True}},
                lambda p, ev, ctx: ev.type == "area_changed" and _real_edge(ev)
                and ev.payload["level"] == p["level"]
                and ev.payload["to"] == p["area"]),
    TriggerType("warp_entered", "You enter a warp/pipe",
                {"level": {"kind": "level", "required": True}},
                lambda p, ev, ctx: ev.type == "warp_entered"
                and ev.payload["level"] == p["level"]),
    TriggerType("key_grabbed", "You grab a Bowser key",
                {"level": {"kind": "level", "required": False}},
                lambda p, ev, ctx: ev.type == "key_grabbed"
                and (p.get("level") is None
                     or ev.payload["level"] == p["level"])),
    TriggerType("star_grabbed", "You grab a star",
                {"course": {"kind": "course", "required": False},
                 "star": {"kind": "star", "required": False}},
                lambda p, ev, ctx: ev.type == "star_collected"
                and (p.get("course") is None
                     or ev.payload["course_id"] == p["course"])
                and (p.get("star") is None
                     or ev.payload["star_id"] == p["star"])),
    TriggerType("spawned", "You spawn into the game",
                {"level": {"kind": "level", "required": False}},
                lambda p, ev, ctx: ev.type == "spawned"
                and (p.get("level") is None
                     or ev.payload["level"] == p["level"])),
    TriggerType("attempt_anchor", "Practice reset / savestate load in level",
                {"level": {"kind": "level", "required": True}},
                lambda p, ev, ctx: ev.type in ("practice_reset",
                                               "state_loaded")
                and ctx.level == p["level"]),
]}


@dataclass(frozen=True)
class GuardType:
    key: str
    label: str
    params: dict
    check: Callable[[dict, MatchContext], bool]


GUARDS: dict[str, GuardType] = {g.key: g for g in [
    GuardType("prev_level", "Previous level was",
              {"level": {"kind": "level", "required": True}},
              lambda p, ctx: ctx.prev_level == p["level"]),
    GuardType("star_count_min", "Star count at least",
              {"n": {"kind": "int", "required": True}},
              # historical events without num_stars conservatively FAIL
              lambda p, ctx: ctx.num_stars is not None
              and ctx.num_stars >= p["n"]),
    GuardType("star_count_max", "Star count at most",
              {"n": {"kind": "int", "required": True}},
              lambda p, ctx: ctx.num_stars is not None
              and ctx.num_stars <= p["n"]),
]}


def _check_clause(clause: dict, registry: dict, what: str) -> None:
    kind = clause.get("type")
    if kind not in registry:
        raise ValueError(f"unknown trigger type {kind!r} in {what}"
                         if registry is TRIGGERS
                         else f"unknown guard type {kind!r} in {what}")
    spec = registry[kind]
    for name, meta in spec.params.items():
        if meta["required"] and clause.get(name) is None:
            raise ValueError(f"{kind}: missing required param {name!r}")
        if clause.get(name) is not None and not isinstance(clause[name], int):
            raise ValueError(f"{kind}: param {name!r} must be an integer")
    extras = set(clause) - {"type"} - set(spec.params)
    if extras:
        raise ValueError(f"{kind}: unknown params {sorted(extras)}")


def validate_definition(d: dict) -> None:
    """Raises ValueError listing the first problem (API maps it to 409)."""
    if not str(d.get("name", "")).strip():
        raise ValueError("name is required")
    for side in ("start_triggers", "end_triggers"):
        clauses = d.get(side) or []
        if not clauses:
            raise ValueError(f"{side} needs at least one trigger")
        for c in clauses:
            _check_clause(c, TRIGGERS, side)
    for g in d.get("guards") or []:
        _check_clause(g, GUARDS, "guards")


def vocab() -> dict:
    """Registry serialized for the builder GUI — the UI renders from this."""
    return {
        "triggers": [{"key": t.key, "label": t.label, "params": t.params}
                     for t in TRIGGERS.values()],
        "guards": [{"key": g.key, "label": g.label, "params": g.params}
                   for g in GUARDS.values()],
        "levels": {str(k): v for k, v in sorted(LEVEL_NAMES.items())},
        "castle_areas": {str(k): v for k, v in CASTLE_AREA_NAMES.items()},
    }
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_segments.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/segments.py tests/test_segments.py
git commit -m "feat: segment trigger vocabulary - ONE registry drives matcher, validation, and GUI"
```

---

## Task 10: SegmentEngine — the matcher FSM

**Files:**
- Modify: `src/sm64_events/tracking/segments.py`
- Test: `tests/test_segments.py`

- [ ] **Step 1: Write the failing tests** (append; `jev` comes from
  test_projection — `jev(id, type, frame, payload)`)

```python
from sm64_events.tracking.segments import (SEGMENT_ATTEMPT_OFFSET,
                                           SegmentEngine)

LBLJ = SegmentDef(id=1, name="LBLJ", enabled=True,
                  start_triggers=[{"type": "level_enter", "to": 6, "from": 16}],
                  end_triggers=[{"type": "level_enter", "to": 17}], guards=[])
PIPE = SegmentDef(id=5, name="BitDW Pipe Entry", enabled=True,
                  start_triggers=[{"type": "level_enter", "to": 17},
                                  {"type": "attempt_anchor", "level": 17}],
                  end_triggers=[{"type": "warp_entered", "level": 17}],
                  guards=[])


def ctx(level=None, prev_level=None, num_stars=None):
    return MatchContext(level=level, prev_level=prev_level,
                        num_stars=num_stars)


def lblj_arm(engine, jid=10, frame=1000):
    return engine.feed(jev(jid, "level_changed", frame,
                           {"from": 16, "to": 6}), ctx(level=6, prev_level=16))


def test_arm_then_end_is_a_success_with_rta_delta():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(jev(11, "level_changed", 1085, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    [a] = closed
    assert a.outcome == "success" and a.segment_id == 1
    assert a.rta_frames == 85 and a.igt_frames is None
    assert a.course_id is None and a.star_id is None
    assert a.id == 10 + SEGMENT_ATTEMPT_OFFSET * 1
    assert a.anchor_type == "level_changed"


def test_restart_anchors_rearm_without_recording_a_row():
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    # walk out (silent disarm), walk back in (fresh arm at the new frame)
    closed, _ = e.feed(jev(11, "level_changed", 1200, {"from": 6, "to": 16}),
                       ctx(level=16, prev_level=6))
    assert closed == []
    lblj_arm(e, jid=12, frame=1300)
    closed, _ = e.feed(jev(13, "level_changed", 1390, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert closed[0].rta_frames == 90


def test_rearm_on_start_refire_restarts_the_timer():
    e = SegmentEngine([PIPE])
    e.feed(jev(20, "level_changed", 2000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    e.feed(jev(21, "practice_reset", 2500, {"igt_frames_before": 100}),
           ctx(level=17))                       # closes reset AND re-arms
    closed, _ = e.feed(jev(22, "warp_entered", 2600, {"level": 17, "area": 1,
                                                      "action": 0x1300}),
                       ctx(level=17))
    assert closed[0].rta_frames == 100          # timed from the reset, not entry


def test_practice_reset_closes_as_reset_then_rearms_via_attempt_anchor():
    e = SegmentEngine([PIPE])
    e.feed(jev(30, "level_changed", 3000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    closed, _ = e.feed(jev(31, "practice_reset", 3200,
                           {"igt_frames_before": 50}), ctx(level=17))
    [a] = closed
    assert a.outcome == "reset" and a.rta_frames == 200
    assert a.anchor_type == "level_changed"     # the attempt that FAILED was armed by entry


def test_afk_reset_discards_the_row_but_still_rearms():
    e = SegmentEngine([PIPE])
    e.feed(jev(40, "level_changed", 4000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    closed, _ = e.feed(jev(41, "practice_reset", 4500,
                           {"paused_frames_before": 200}), ctx(level=17))
    assert closed == []                          # AFK discard
    closed, _ = e.feed(jev(42, "warp_entered", 4600, {"level": 17, "area": 1,
                                                      "action": 0x1300}),
                       ctx(level=17))
    assert closed[0].rta_frames == 100           # re-armed by the reset anyway


def test_death_and_game_reset_close_with_their_outcomes():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(jev(11, "death", 1050, {"cause": "standing"}),
                       ctx(level=6))
    assert closed[0].outcome == "death"
    assert closed[0].outcome_detail == "standing"
    lblj_arm(e, jid=12, frame=2000)
    closed, _ = e.feed(jev(13, "game_reset", 2100, {}), ctx())
    assert closed[0].outcome == "hard_reset"


def test_foreign_level_change_disarms_silently():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(jev(11, "level_changed", 1500, {"from": 6, "to": 27}),
                       ctx(level=27, prev_level=6))
    assert closed == []
    closed, _ = e.feed(jev(12, "level_changed", 1600, {"from": 27, "to": 17}),
                       ctx(level=17, prev_level=27))
    assert closed == []                          # was not armed anymore


def test_establishing_level_event_from_equals_to_never_arms():
    e = SegmentEngine([LBLJ])
    closed, _ = e.feed(jev(10, "level_changed", 1000, {"from": 6, "to": 6}),
                       ctx(level=6, prev_level=6))
    assert e.armed_ids() == set()


def test_guards_reevaluate_on_every_arm():
    guarded = SegmentDef(id=2, name="g", enabled=True,
                         start_triggers=[{"type": "level_enter", "to": 6}],
                         end_triggers=[{"type": "level_enter", "to": 17}],
                         guards=[{"type": "prev_level", "level": 16}])
    e = SegmentEngine([guarded])
    e.feed(jev(10, "level_changed", 1000, {"from": 26, "to": 6}),
           ctx(level=6, prev_level=26))          # guard fails: from courtyard
    assert e.armed_ids() == set()
    e.feed(jev(11, "level_changed", 1100, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    assert e.armed_ids() == {2}


def test_negative_rta_discards_and_disarms():
    e = SegmentEngine([LBLJ])
    lblj_arm(e, frame=5000)
    closed, _ = e.feed(jev(11, "level_changed", 100, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert closed == []
    assert e.armed_ids() == set()


def test_armed_disarmed_notices_for_live_broadcast():
    e = SegmentEngine([LBLJ])
    _, notices = lblj_arm(e)
    assert notices == [{"event": "segment_armed", "segment_id": 1,
                        "name": "LBLJ", "frame": 1000}]
    _, notices = e.feed(jev(11, "level_changed", 1500, {"from": 6, "to": 27}),
                        ctx(level=27, prev_level=6))
    assert notices[0]["event"] == "segment_disarmed"
```

- [ ] **Step 2: Run** — FAIL (`SegmentEngine` missing).

- [ ] **Step 3: Implement** (append to `tracking/segments.py`)

```python
@dataclass(frozen=True)
class _Arm:
    jid: int            # journal id of the arming event -> attempt id
    start_frame: int
    started_utc: str
    anchor_type: str    # the arming event's type
    session_id: int


class SegmentEngine:
    """One IDLE<->ARMED FSM per enabled definition. Pure over journal
    events + MatchContext: same code path live and in replay."""

    def __init__(self, defs: list[SegmentDef]):
        self._defs = [d for d in defs if d.enabled]
        self._armed: dict[int, _Arm] = {}

    def armed_ids(self) -> set[int]:
        return set(self._armed)

    def feed(self, ev, ctx: MatchContext):
        """Returns (closed raw Attempts, notices). Closures before arming."""
        from sm64_events.tracking.projection import Attempt  # cycle-free at call time
        closed, notices = [], []
        for d in self._defs:
            arm = self._armed.get(d.id)
            starts = self._matches(d.start_triggers, ev, ctx)
            if arm is not None:
                if self._matches(d.end_triggers, ev, ctx):
                    a = self._close(Attempt, d, arm, ev, "success", None)
                    if a:
                        closed.append(a)
                    self._disarm(d, ev, notices)
                elif ev.type in ("practice_reset", "state_loaded"):
                    if ev.payload.get("paused_frames_before", 0) \
                            < _AFK_PAUSE_FRAMES:
                        a = self._close(Attempt, d, arm, ev, "reset", None)
                        if a:
                            closed.append(a)
                    self._disarm(d, ev, notices)
                elif ev.type == "death":
                    a = self._close(Attempt, d, arm, ev, "death",
                                    ev.payload.get("cause"))
                    if a:
                        closed.append(a)
                    self._disarm(d, ev, notices)
                elif ev.type == "game_reset":
                    a = self._close(Attempt, d, arm, ev, "hard_reset", None)
                    if a:
                        closed.append(a)
                    self._disarm(d, ev, notices)
                elif ev.type in ("level_changed", "session_started") \
                        and not starts:
                    self._disarm(d, ev, notices)   # silent: no row
            # arm / re-arm — guards re-evaluated every time (spec)
            if starts and all(GUARDS[g["type"]].check(g, ctx)
                              for g in d.guards):
                fresh = d.id not in self._armed
                self._armed[d.id] = _Arm(jid=ev.id, start_frame=ev.frame,
                                         started_utc=ev.wall_time_utc,
                                         anchor_type=ev.type,
                                         session_id=ev.session_id)
                if fresh:
                    notices.append({"event": "segment_armed",
                                    "segment_id": d.id, "name": d.name,
                                    "frame": ev.frame})
        return closed, notices

    def _matches(self, triggers, ev, ctx) -> bool:
        return any(TRIGGERS[t["type"]].match(t, ev, ctx) for t in triggers)

    def _disarm(self, d, ev, notices) -> None:
        if self._armed.pop(d.id, None) is not None:
            notices.append({"event": "segment_disarmed", "segment_id": d.id,
                            "name": d.name, "frame": ev.frame})

    def _close(self, Attempt, d, arm: _Arm, ev, outcome, detail):
        rta = ev.frame - arm.start_frame
        if rta < 0:
            return None  # timer anomaly: discard (self-heal, domain rule 4)
        return Attempt(
            id=arm.jid + SEGMENT_ATTEMPT_OFFSET * d.id,
            session_id=arm.session_id, course_id=None, star_id=None,
            strat_tag=None,  # projector fills from its strat memory
            anchor_type=arm.anchor_type, anchor_frame=arm.start_frame,
            outcome=outcome, outcome_detail=detail,
            igt_frames=None, rta_frames=rta,
            started_utc=arm.started_utc, ended_utc=ev.wall_time_utc,
            cleared=False, cleared_reason=None, segment_id=d.id)
```

(Note the success branch disarms via `_disarm` — that emits a
`segment_disarmed` notice after a success; the UI treats armed/disarmed as
the timer light, which should go off when the segment completes. Tests in
Step 1 assert the armed notice shape only for arm/foreign-disarm; extend
them if you want the success-disarm asserted too.)

- [ ] **Step 4: Run** — `uv run pytest tests/test_segments.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/segments.py tests/test_segments.py
git commit -m "feat: SegmentEngine - per-definition FSM, closures before arming"
```

---

## Task 11: Projection integration — tagged target + engine wiring

**Files:**
- Modify: `src/sm64_events/tracking/projection.py`
- Test: `tests/test_projection.py`

This is the contract-heavy task. Target identity changes from
`(course_id, star_id)` to tagged tuples; every consumer updates in Tasks
12–14. **Existing tests that assert `projector.target == (c, s)` must be
updated to `("star", c, s)` in this task** — grep `\.target ==` in tests/.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_projection.py`)

```python
def seg_defs():
    from sm64_events.tracking.segments import SegmentDef
    return [SegmentDef(id=1, name="LBLJ", enabled=True,
                       start_triggers=[{"type": "level_enter", "to": 6,
                                        "from": 16}],
                       end_triggers=[{"type": "level_enter", "to": 17}],
                       guards=[])]


def test_segment_success_is_projected_and_auto_follows_target():
    p = Projector(segments=seg_defs())
    p.feed(jev(1, "level_changed", 900, {"from": 16, "to": 16}))
    p.feed(jev(2, "level_changed", 1000, {"from": 16, "to": 6}))
    closed = p.feed(jev(3, "level_changed", 1085, {"from": 6, "to": 17}))
    segs = [a for a in closed if a.segment_id == 1]
    assert len(segs) == 1 and segs[0].outcome == "success"
    assert p.target == ("segment", 1)


def test_star_target_is_tagged_now():
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"course_id": 2, "star_id": 2}))
    assert p.target == ("star", 2, 2)


def test_segment_target_set_event_round_trips():
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 4}))
    assert p.target == ("segment", 4)


def test_cleared_segment_attempt_does_not_move_target():
    p = Projector(cleared={2 + 10**10 * 1: "mistake"}, segments=seg_defs())
    p.feed(jev(1, "target_set", 0, {"course_id": 2, "star_id": 2}))
    p.feed(jev(2, "level_changed", 1000, {"from": 16, "to": 6}))
    closed = p.feed(jev(3, "level_changed", 1100, {"from": 6, "to": 17}))
    assert closed[-1].cleared is True
    assert p.target == ("star", 2, 2)


def test_replay_signature_accepts_segments():
    from sm64_events.tracking.projection import replay
    attempts, projector = replay([
        jev(1, "level_changed", 1000, {"from": 16, "to": 6}),
        jev(2, "level_changed", 1100, {"from": 6, "to": 17}),
    ], segments=seg_defs())
    assert any(a.segment_id == 1 for a in attempts)
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement**

In `projection.py`:

1. **Tagged target.** Every site that reads/writes `self.target`:
   - `__init__`: comment becomes `# ("star", course_id, star_id) | ("segment", segment_id) | None`
   - `target_set` branch becomes:

```python
        if ev.type == "target_set":
            if ev.payload.get("kind") == "segment":
                self.target = ("segment", ev.payload["segment_id"])
            else:  # legacy payloads have no kind: star
                c, s = ev.payload["course_id"], ev.payload["star_id"]
                self.target = ("star", c, s)
                if "strat_tag" in ev.payload:
                    self.strat_by_star[(c, s)] = ev.payload["strat_tag"]
            return []
```

   - `_close_by_grab`'s auto-follow line becomes
     `self.target = ("star", *grabbed)`.
   - `strat_tag` property: star targets only —

```python
    @property
    def strat_tag(self) -> str | None:
        if self.target and self.target[0] == "star":
            return self.strat_by_star.get(self.target[1:])
        if self.target and self.target[0] == "segment":
            return self.strat_by_segment.get(self.target[1])
        return None
```

   - new dict in `__init__`: `self.strat_by_segment: dict[int, str | None] = {}`;
     the `strat_set` branch gains a segment arm:

```python
        if ev.type == "strat_set":
            if ev.payload.get("kind") == "segment":
                self.strat_by_segment[ev.payload["segment_id"]] = \
                    ev.payload.get("strat_tag")
            else:
                self.strat_by_star[(ev.payload["course_id"],
                                    ev.payload["star_id"])] \
                    = ev.payload.get("strat_tag")
            return []
```

2. **Engine wiring.** `__init__` gains
   `segments: list | None = None` →

```python
        from sm64_events.tracking.segments import SegmentEngine
        self._segments = SegmentEngine(segments or [])
        self.segment_notices: list[dict] = []  # live-broadcast queue, drained by service
        self._num_stars: int | None = None
```

   `feed()` becomes:

```python
    def feed(self, ev) -> list[Attempt]:
        from dataclasses import replace
        from sm64_events.tracking.segments import MatchContext
        prev_level = self._level
        closed = self._dispatch(ev)
        if ev.type == "star_collected" and "num_stars" in ev.payload:
            self._num_stars = ev.payload["num_stars"]
        seg_closed, self.segment_notices = self._segments.feed(
            ev, MatchContext(level=self._level, prev_level=prev_level,
                             num_stars=self._num_stars))
        for a in seg_closed:
            a = replace(a,
                        strat_tag=self.strat_by_segment.get(a.segment_id),
                        cleared=a.id in self._cleared,
                        cleared_reason=self._cleared.get(a.id))
            if a.outcome == "success" and not a.cleared:
                self.target = ("segment", a.segment_id)
            closed.append(a)
        if ev.type in BOUNDARY_EVENT_TYPES:
            self._rollouts_total = self._rollouts_dustless = 0
            self._jumps_total = self._jumps_dustless = 0
        return closed
```

   (`self._cleared` keys must distinguish "not cleared" from "cleared with
   null reason" — check how the star path reads it and mirror exactly; if it
   uses a sentinel-checking helper, reuse it.)

3. **`replay` and `project`** gain a `segments=None` keyword threaded into
   `Projector(...)` construction.

4. Add module-level helper used by views (Task 13):

```python
def journal_id(attempt_id: int) -> int:
    """Recency-comparable id across kinds: segment attempt ids carry a
    namespace offset (segments.SEGMENT_ATTEMPT_OFFSET); strip it."""
    from sm64_events.tracking.segments import SEGMENT_ATTEMPT_OFFSET
    return attempt_id % SEGMENT_ATTEMPT_OFFSET
```

5. **Update existing tests**: every `== (c, s)` target assert becomes
   `== ("star", c, s)`.

- [ ] **Step 4: Run** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/projection.py tests/test_projection.py tests/
git commit -m "feat: tagged target identity + SegmentEngine wired into projection"
```

---

## Task 12: Service — defs loading, CRUD, broadcasts, target kind

**Files:**
- Modify: `src/sm64_events/tracking/service.py`
- Test: `tests/test_service.py` (follow its existing async-test pattern)

- [ ] **Step 1: Write the failing tests** (adapt to the file's existing
  fixtures — it already builds a TrackerService with a tmp db and a fake
  broadcaster; reuse those)

```python
async def test_segment_crud_triggers_reprojection(service_with_db):
    svc, db, sent = service_with_db          # fake broadcaster records events
    await svc.create_segment({"name": "X",
                              "start_triggers": [{"type": "spawned"}],
                              "end_triggers": [{"type": "level_enter",
                                                "to": 6}],
                              "guards": []})
    assert any(e.type == "attempts_invalidated" for e in sent)
    assert any(d["name"] == "X" for d in db.segment_defs())


async def test_invalid_definition_raises_value_error(service_with_db):
    svc, db, sent = service_with_db
    import pytest
    with pytest.raises(ValueError):
        await svc.create_segment({"name": "X",
                                  "start_triggers": [{"type": "nope"}],
                                  "end_triggers": [], "guards": []})


async def test_set_target_accepts_segment_kind(service_with_db):
    svc, db, sent = service_with_db
    await svc.set_target_segment(1)          # seeded LBLJ
    assert svc.target == ("segment", 1)
    ev = next(e for e in sent if e.type == "target_set")
    assert ev.payload == {"kind": "segment", "segment_id": 1}


async def test_segment_attempt_completed_carries_segment_fields(service_with_db):
    svc, db, sent = service_with_db
    # LBLJ is seeded: grounds->castle then castle->BitDW
    await svc.publish(make_event("level_changed", 1000,
                                 {"from": 16, "to": 6}))
    await svc.publish(make_event("level_changed", 1085,
                                 {"from": 6, "to": 17}))
    done = [e for e in sent if e.type == "attempt_completed"
            and e.payload.get("kind") == "segment"]
    assert done and done[0].payload["segment_id"] == 1
    assert done[0].payload["segment_name"] == "LBLJ"
    assert done[0].payload["rta_frames"] == 85
    armed = [e for e in sent if e.type == "segment_armed"]
    assert armed and armed[0].payload["segment_id"] == 1
```

(`make_event` = the file's existing Event factory, or build `Event(...)`
inline with a fixed UTC timestamp.)

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement** in `service.py`:

1. `__init__` loads defs and threads them everywhere a Projector is born:

```python
        self._segment_defs = self._load_segment_defs()
        self._projector = Projector(segments=self._segment_defs)
```

```python
    def _load_segment_defs(self):
        from sm64_events.tracking.segments import SegmentDef
        if self.db is None:
            return []
        return [SegmentDef(**d) for d in
                ({k: v for k, v in row.items() if k != "created_utc"}
                 for row in self.db.segment_defs())]
```

   `_reproject()`'s replay call becomes
   `replay(db.events(), segments=self._segment_defs)`.

2. `_track()` — drain notices **immediately after the feed call, BEFORE the
   attempt loop**: publishing `attempt_completed` re-enters `_track`
   recursively, whose nested `feed()` resets `segment_notices` — draining
   after the loop would silently lose armed events. Broadcast-only (NOT via
   `self.publish`, which journals):

```python
    target_before = self._projector.target
    closed = self._projector.feed(row)
    for n in self._projector.segment_notices:   # capture BEFORE nested publishes
        await self.broadcaster.publish(Event(
            type=n["event"], frame=n["frame"],
            timestamp_utc=event.timestamp_utc,
            payload={"segment_id": n["segment_id"], "name": n["name"]}))
    for attempt in closed:
        ...existing body unchanged...
```

3. `_attempt_completed_event()` payload gains:

```python
                     "kind": "segment" if a.segment_id is not None else "star",
                     "segment_id": a.segment_id,
                     "segment_name": self._segment_name(a.segment_id),
                     "rta": format_igt(a.rta_frames) if a.rta_frames is not None else None,
```

```python
    def _segment_name(self, segment_id):
        if segment_id is None:
            return None
        return next((d.name for d in self._segment_defs
                     if d.id == segment_id), f"segment {segment_id}")
```

4. Target: `_target_payload()` becomes kind-aware (segment targets emit
   `{"kind": "segment", "segment_id": id, "segment_name": name}`; star
   targets keep today's exact shape PLUS `"kind": "star"` so existing
   consumers keep working). New command:

```python
    async def set_target_segment(self, segment_id: int,
                                 strat_tag: str | None = None) -> None:
        self._require_db()
        if all(d.id != segment_id for d in self._segment_defs):
            raise LookupError(f"segment {segment_id} not found")
        await self.publish(Event(type="target_set", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"kind": "segment",
                                          "segment_id": segment_id}))
```

   `service.target` property (if one exists; else direct `_projector.target`
   reads in views) now returns the tagged tuple — check `views.py` and
   `_target_payload` call sites compile.

5. CRUD commands (validation BEFORE write; reload + reproject after):

```python
    async def create_segment(self, d: dict) -> int:
        db = self._require_db()
        validate_definition(d)
        sid = db.insert_segment_def(d["name"], d["start_triggers"],
                                    d["end_triggers"], d.get("guards", []),
                                    _iso(_now()),
                                    enabled=d.get("enabled", True))
        await self._segments_changed()
        return sid

    async def update_segment(self, segment_id: int, d: dict) -> None:
        db = self._require_db()
        # partial patches (e.g. {"enabled": false}) must validate as the
        # MERGED definition, not in isolation
        current = next((r for r in db.segment_defs()
                        if r["id"] == segment_id), None)
        if current is None:
            raise LookupError(f"segment {segment_id} not found")
        validate_definition({**current, **d})
        db.update_segment_def(segment_id, **{
            k: d[k] for k in ("name", "enabled", "start_triggers",
                              "end_triggers", "guards") if k in d})
        await self._segments_changed()

    async def delete_segment(self, segment_id: int) -> None:
        db = self._require_db()
        db.delete_segment_def(segment_id)
        await self._segments_changed()

    async def _segments_changed(self) -> None:
        self._segment_defs = self._load_segment_defs()
        await self._reproject()
```

   with `from sm64_events.tracking.segments import validate_definition`.

6. **PB save path**: find the existing save_pb command; when the attempt has
   `segment_id`, require `timer_mode == "rta"` (raise
   `ValueError("segments are RTA-only")` otherwise) and call `insert_pb`
   with `course_id=None, star_id=None, segment_id=a.segment_id`.

- [ ] **Step 4: Run** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/service.py tests/test_service.py
git commit -m "feat: segment CRUD commands, broadcast-only armed events, kind-aware target"
```

---

## Task 13: Views — segment sections

**Files:**
- Modify: `src/sm64_events/tracking/views.py`
- Test: `tests/test_views.py`

- [ ] **Step 1: Write the failing tests** (use the file's existing
  service/db fixtures)

```python
def test_view_lists_segment_sections_with_rta_stats(view_fixture):
    # journal: LBLJ success at 85 RTA frames, then one reset failure
    view = build_view_after_events([
        ("level_changed", 1000, {"from": 16, "to": 6}),
        ("level_changed", 1085, {"from": 6, "to": 17}),
    ])
    sec = next(s for s in view["segments"] if s["name"] == "LBLJ")
    assert sec["kind"] == "segment" and sec["segment_id"] == 1
    assert sec["attempts"][0]["outcome"] == "success"
    assert sec["attempts"][0]["rta_frames"] == 85
    assert sec["pb"]["rta"] is None            # nothing saved yet
    assert sec["timeline"]["points"][0]["frames"] == 85


def test_segment_target_section_is_always_present(view_fixture):
    # set segment target with zero attempts -> section still pinned
    view = build_view_with_segment_target(segment_id=3)
    assert view["target"]["kind"] == "segment"
    assert any(s.get("segment_id") == 3 for s in view["segments"])
```

(Shape the helpers to the file's existing test style — it already has a way
to feed journal events through a real service; reuse it rather than
inventing new plumbing.)

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement** in `views.py`:

1. Generalize the two derived-graph helpers to take a frame extractor:
   `_timeline(history, frames_of)` — replace the
   `TIMELINE_OUTCOMES[a.outcome](a)` call with `frames_of(a)` gated on
   `a.outcome in TIMELINE_OUTCOMES`; star call sites pass
   `lambda a: a.igt_frames`, segment sections pass `lambda a: a.rta_frames`.
   Apply the same parameter to `_progress` (its success times come from the
   same per-attempt frames — same substitution).
2. Build segment sections after the star loop. Segment attempts are the ones
   with `a.segment_id is not None` (exclude them from the star `seen` scan —
   their `course_id` is None, so today they'd land in `unassigned`; add
   `and a.segment_id is None` to that branch):

```python
    seg_sections = []
    seg_defs = {d.id: d for d in service.segment_defs}   # expose list on service
    seen_segs: dict[int, None] = {}
    for a in scoped:
        if a.segment_id is not None:
            seen_segs[a.segment_id] = None
    if service.target and service.target[0] == "segment":
        seen_segs.setdefault(service.target[1], None)
    for seg_id in seen_segs:
        d = seg_defs.get(seg_id)
        history = [a for a in all_attempts if a.segment_id == seg_id]
        in_section = [a for a in history if a in scoped_set]
        rta_of = lambda a: a.rta_frames
        pb_row = pbs.get(("segment", seg_id, "rta"))
        seg_sections.append({
            "kind": "segment", "segment_id": seg_id,
            "name": d.name if d else f"segment {seg_id} (deleted)",
            "broken": d is None,
            "pb": {"rta": ({"frames": pb_row["frames"],
                            "display": format_igt(pb_row["frames"])}
                           if pb_row else None)},
            "attempts": [_attempt_json(a, pbs, "rta") for a in in_section],
            "stats": _stats_for(history, stat_menu, "rta"),  # extract the existing chip loop into _stats_for(history, stat_menu, clock)
            "last_strat": service.strat_by_segment.get(seg_id),
            "timeline": _timeline(history, rta_of),
            "markers_by_strat": _markers_for(markers_state, "seg", seg_id),
            "progress": _progress(in_section, pb_ids, session_meta, rta_of),
        })
```

   - Extracting `_stats_for(...)` from the star section's inline chip loop is
     the DRY move — both call sites use it (star passes the view `clock`,
     segments force `"rta"`).
   - `_current_pbs` keying: change keys from `(course_id, star_id, mode)` to
     `("segment", segment_id, mode)` when `row["segment_id"]` is set, else
     `(course_id, star_id, mode)` — and update `_attempt_json`'s pb lookup
     the same way (`("segment", a.segment_id, clock)` when segment).
   - `_markers_for` key prefix for segments: `f"seg:{seg_id}:{strat or ''}"`
     (mirrors the star key shape).
   - `_attempt_json` gains `"segment_id": a.segment_id` in its dict.
3. Add `"segments": seg_sections` to the returned view dict; star sections
   list stays under its existing key, untouched.
4. Section recency ordering: wherever max-attempt-id ordering is computed,
   wrap ids with `journal_id(...)` from projection (Task 11.4).
5. Expose on service: `segment_defs` property returning
   `self._segment_defs`, and `strat_by_segment` proxying
   `self._projector.strat_by_segment`.

- [ ] **Step 4: Run** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/views.py src/sm64_events/tracking/service.py tests/test_views.py
git commit -m "feat: segment sections in the session view - rta-clocked, target-pinned"
```

---

## Task 14: API — segments CRUD, vocab, target kind

**Files:**
- Modify: `src/sm64_events/server/api.py`
- Test: `tests/test_api.py` (follow its existing TestClient pattern)

- [ ] **Step 1: Write the failing tests**

```python
def test_get_segments_lists_seeds(client):
    r = client.get("/api/segments")
    assert r.status_code == 200
    assert any(d["name"] == "LBLJ" for d in r.json())


def test_vocab_endpoint_shape(client):
    v = client.get("/api/segments/vocab").json()
    assert "triggers" in v and "levels" in v and "guards" in v


def test_post_invalid_segment_is_409(client):
    r = client.post("/api/segments", json={
        "name": "x", "start_triggers": [{"type": "nope"}],
        "end_triggers": [{"type": "spawned"}]})
    assert r.status_code == 409


def test_segment_crud_roundtrip(client):
    r = client.post("/api/segments", json={
        "name": "Custom", "start_triggers": [{"type": "spawned"}],
        "end_triggers": [{"type": "level_enter", "to": 6}]})
    sid = r.json()["id"]
    assert client.put(f"/api/segments/{sid}",
                      json={"enabled": False}).status_code == 200
    assert client.delete(f"/api/segments/{sid}").status_code == 200
    assert client.delete(f"/api/segments/{sid}").status_code == 404


def test_target_accepts_segment_kind(client):
    r = client.post("/api/target", json={"kind": "segment", "segment_id": 1})
    assert r.status_code == 200
    r = client.post("/api/target", json={"kind": "segment",
                                         "segment_id": 9999})
    assert r.status_code == 404
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement** in `api.py` (inside `create_api_router`,
  following the existing endpoint idiom exactly):

```python
class SegmentBody(BaseModel):
    name: str
    start_triggers: list[dict]
    end_triggers: list[dict]
    guards: list[dict] = []
    enabled: bool = True


class SegmentPatch(BaseModel):
    name: str | None = None
    start_triggers: list[dict] | None = None
    end_triggers: list[dict] | None = None
    guards: list[dict] | None = None
    enabled: bool | None = None


@router.get("/segments")
def segments():
    if service.db is None:
        raise HTTPException(503, "database unavailable")
    return service.db.segment_defs()


@router.get("/segments/vocab")
def segments_vocab():
    return vocab()


@router.post("/segments")
async def create_segment(body: SegmentBody):
    try:
        sid = await service.create_segment(body.model_dump())
    except (LookupError, ValueError, RuntimeError) as e:
        raise _http(e)
    return {"ok": True, "id": sid}


@router.put("/segments/{segment_id}")
async def update_segment(segment_id: int, body: SegmentPatch):
    try:
        await service.update_segment(
            segment_id, {k: v for k, v in body.model_dump().items()
                         if v is not None})
    except (LookupError, ValueError, RuntimeError) as e:
        raise _http(e)
    return {"ok": True}


@router.delete("/segments/{segment_id}")
async def delete_segment(segment_id: int):
    try:
        await service.delete_segment(segment_id)
    except (LookupError, ValueError, RuntimeError) as e:
        raise _http(e)
    return {"ok": True}
```

(`from sm64_events.tracking.segments import vocab` at the top. Route order:
declare `/segments/vocab` BEFORE `/segments/{segment_id}` — FastAPI matches
in declaration order; see the fastapi-patterns skill.)

`TargetBody` gains `kind: str = "star"` and `segment_id: int | None = None`;
the target endpoint branches:

```python
@router.post("/target")
async def target(body: TargetBody):
    try:
        if body.kind == "segment":
            if body.segment_id is None:
                raise ValueError("segment target needs segment_id")
            await service.set_target_segment(body.segment_id, body.strat_tag)
        else:
            await service.set_target(body.course_id, body.star_id,
                                     body.strat_tag)
    except (LookupError, ValueError, RuntimeError) as e:
        raise _http(e)
    return {"ok": True}
```

(`TargetBody.course_id/star_id` become `int | None = None` so segment bodies
validate; star path raises ValueError when they're missing.)

`MarkersBody` gains `segment_id: int | None = None`; the PUT handler's key
becomes `f"seg:{body.segment_id}:{body.strat_tag or ''}"` when `segment_id`
is set, else today's star key.

- [ ] **Step 4: Run** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/server/api.py tests/test_api.py
git commit -m "feat: /api/segments CRUD + vocab; kind-aware target and markers"
```

---

## Task 15: UI — Segments tab with builder

**Files:**
- Create: `src/sm64_events/ui/components/segments.js`
- Modify: `src/sm64_events/ui/app.js`, `src/sm64_events/ui/store.js`, `src/sm64_events/ui/index.html` (one CSS rule)

No JS test infra exists — verification is the frontend-smoke-test skill
(Task 16 Step 4 covers both UI tasks). Match the codebase idioms exactly:
`htm.bind(h)`, `getJSON`/`send` from `../api.js`, store `t` prop.

- [ ] **Step 1: store.js — armed-segment tracking**

In `useTracker()` add state + WS handling (armed/disarmed must NOT refetch
the whole view):

```javascript
  const [armedSegs, setArmedSegs] = useState(new Set());
```

inside `ws.onmessage` after the feed update:

```javascript
        if (ev.type === "segment_armed") {
          setArmedSegs((s) => new Set(s).add(ev.payload.segment_id));
        } else if (ev.type === "segment_disarmed") {
          setArmedSegs((s) => { const n = new Set(s);
            n.delete(ev.payload.segment_id); return n; });
        }
```

and `armedSegs` in the returned object. (REFRESH_ON already refetches on
`attempt_completed`/`attempts_invalidated`/`target_changed` — segment CRUD
and completions are covered without changes.)

- [ ] **Step 2: app.js — the tab**

`TABS` becomes `["Practice", "Segments", "Routes", "Live feed"]`; render
branch:

```javascript
      ${tab === "Practice" ? html`<${Practice} t=${t} />`
        : tab === "Segments" ? html`<${Segments} t=${t} />`
        : html`<${Feed} t=${t} />`}
```

with `import { Segments } from "./components/segments.js";`.

- [ ] **Step 3: segments.js — list + builder**

```javascript
// src/sm64_events/ui/components/segments.js — definition list + builder.
// The form is 100% vocab-driven: GET /api/segments/vocab supplies trigger
// types, param schemas, and level/area enums; adding a trigger type in
// tracking/segments.py appears here with zero UI changes.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

function ParamInput({ schema, name, value, vocab, onChange }) {
  if (schema.kind === "level") {
    return html`<select value=${value ?? ""}
        onchange=${(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}>
      <option value="">${schema.required ? "— pick level —" : "(any)"}</option>
      ${Object.entries(vocab.levels).map(([id, n]) =>
        html`<option value=${id}>${n}</option>`)}
    </select>`;
  }
  if (schema.kind === "area") {
    return html`<select value=${value ?? ""}
        onchange=${(e) => onChange(Number(e.target.value))}>
      <option value="">— pick area —</option>
      ${Object.entries(vocab.castle_areas).map(([id, n]) =>
        html`<option value=${id}>${n}</option>`)}
    </select>`;
  }
  return html`<input type="number" style="width:5rem" value=${value ?? ""}
      placeholder=${name}
      onchange=${(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />`;
}

function ClauseRow({ clause, types, vocab, onChange, onRemove }) {
  const spec = types.find((t) => t.key === clause.type) || types[0];
  return html`<div class="segclause">
    <select value=${clause.type}
        onchange=${(e) => onChange({ type: e.target.value })}>
      ${types.map((t) => html`<option value=${t.key}>${t.label}</option>`)}
    </select>
    ${Object.entries(spec.params).map(([name, schema]) => html`
      <${ParamInput} schema=${schema} name=${name} vocab=${vocab}
        value=${clause[name]}
        onChange=${(v) => onChange({ ...clause, [name]: v })} />`)}
    <button onclick=${onRemove}>✕</button>
  </div>`;
}

function Builder({ vocab, initial, onSaved, onCancel }) {
  const blank = { name: "", enabled: true,
    start_triggers: [{ type: "level_enter" }],
    end_triggers: [{ type: "level_enter" }], guards: [] };
  const [d, setD] = useState(initial || blank);
  const [err, setErr] = useState(null);
  const edit = (k, i, clause) => setD({ ...d,
    [k]: d[k].map((c, j) => (j === i ? clause : c)) });
  const add = (k, types) => setD({ ...d, [k]: [...d[k], { type: types[0].key }] });
  const drop = (k, i) => setD({ ...d, [k]: d[k].filter((_, j) => j !== i) });

  async function save() {
    try {
      setErr(null);
      if (initial && initial.id != null) {
        await send("PUT", `/api/segments/${initial.id}`, d);
      } else {
        await send("POST", "/api/segments", d);
      }
      onSaved();
    } catch (e) { setErr(String(e)); }
  }

  const clauses = (k, types) => html`
    ${d[k].map((c, i) => html`<${ClauseRow} clause=${c} types=${types}
        vocab=${vocab} onChange=${(cl) => edit(k, i, cl)}
        onRemove=${() => drop(k, i)} />`)}
    <button class="meta" onclick=${() => add(k, types)}>+ alternate trigger</button>`;

  return html`<div class="segbuilder">
    <div><input placeholder="Segment name" value=${d.name}
        onchange=${(e) => setD({ ...d, name: e.target.value })} /></div>
    <div class="label">Starts when any of</div>
    ${clauses("start_triggers", vocab.triggers)}
    <div class="label">Ends when any of</div>
    ${clauses("end_triggers", vocab.triggers)}
    <div class="label">Guards (optional)</div>
    ${clauses("guards", vocab.guards)}
    ${err && html`<div class="badx">${err}</div>`}
    <div>
      <button onclick=${save}>Save — history recomputes automatically</button>
      <button onclick=${onCancel}>Cancel</button>
    </div>
  </div>`;
}

export function Segments({ t }) {
  const [defs, setDefs] = useState(null);
  const [vocabData, setVocabData] = useState(null);
  const [editing, setEditing] = useState(null);   // null | "new" | def object
  const load = async () => setDefs(await getJSON("/api/segments"));
  useEffect(() => { load();
    getJSON("/api/segments/vocab").then(setVocabData); }, []);
  if (!defs || !vocabData) return html`<div class="meta">loading…</div>`;

  const tgt = (t.view && t.view.target) || {};
  async function setTarget(d) {
    await send("POST", "/api/target", { kind: "segment", segment_id: d.id });
    t.refresh();
  }
  async function toggle(d) {
    await send("PUT", `/api/segments/${d.id}`, { enabled: !d.enabled });
    load(); t.refresh();
  }
  async function remove(d) {
    if (!window.confirm(`Delete "${d.name}" and its history/PBs?`)) return;
    await send("DELETE", `/api/segments/${d.id}`);
    load(); t.refresh();
  }

  return html`<div>
    ${defs.map((d) => html`<div class="segrow">
      <b>${d.name}</b>
      ${t.armedSegs.has(d.id) && html`<span class="chip good">⏱ armed</span>`}
      ${tgt.kind === "segment" && tgt.segment_id === d.id
        && html`<span class="chip">★ target</span>`}
      <span style="flex:1"></span>
      <button onclick=${() => setTarget(d)}>set target</button>
      <button onclick=${() => toggle(d)}>${d.enabled ? "disable" : "enable"}</button>
      <button onclick=${() => setEditing(d)}>edit</button>
      <button onclick=${() => remove(d)}>delete</button>
    </div>`)}
    ${editing
      ? html`<${Builder} vocab=${vocabData}
          initial=${editing === "new" ? null : editing}
          onSaved=${() => { setEditing(null); load(); t.refresh(); }}
          onCancel=${() => setEditing(null)} />`
      : html`<button onclick=${() => setEditing("new")}>+ New segment</button>`}
  </div>`;
}
```

- [ ] **Step 4: index.html CSS** — add beside the `.starsec` rules:

```css
  .segrow { display: flex; gap: .5rem; align-items: center; border: 1px solid #2c3140; border-radius: 8px; padding: .4rem .6rem; margin-bottom: .4rem; }
  .segclause { display: flex; gap: .4rem; margin: .2rem 0; }
  .segbuilder { border: 1px solid #3a4150; border-radius: 8px; padding: .6rem; margin-top: .6rem; }
```

- [ ] **Step 5: Run backend suite (UI is served per request — no build)**

Run: `uv run pytest -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/ui/components/segments.js src/sm64_events/ui/app.js src/sm64_events/ui/store.js src/sm64_events/ui/index.html
git commit -m "feat: Segments tab - vocab-driven builder, list, set-target, armed indicator"
```

---

## Task 16: UI — segment sections on the practice page

**Files:**
- Modify: `src/sm64_events/ui/components/practice.js`, `src/sm64_events/ui/components/header.js`

- [ ] **Step 1: SegmentSection component** (in practice.js, beside
  StarSection — a sibling, not a generalization: segments have no strat
  dropdown v1, no links, RTA-only)

```javascript
function SegmentSection({ sec, t, ui, pinned }) {
  const [visible, setVisible] = useState(10);
  const rows = sec.attempts
    .filter((a) => !a.cleared)
    .filter((a) => !(ui.hideResets
      && (a.outcome === "reset" || a.outcome === "hard_reset")))
    .slice().sort(comparator(ui.sort, "rta"));
  return html`<div class="starsec ${pinned ? "active-star" : ""}">
    ${pinned && html`<div class="active-tag">★ ACTIVE SEGMENT</div>`}
    <div class="shead">
      <b>${sec.name}</b>
      ${t.armedSegs.has(sec.segment_id)
        && html`<span class="chip good">⏱ armed</span>`}
      <span class="pbtag">${sec.pb.rta ? `PB ${sec.pb.rta.display} (rta)` : "no PB yet"}</span>
    </div>
    <${Timeline} tl=${sec.timeline} sec=${sec} t=${t} />
    <${Progress} prog=${sec.progress} clock="rta" />
    <${AttemptTable} attempts=${sec.attempts} rows=${rows.slice(0, visible)} t=${t} />
    ${rows.length > visible && html`<button class="meta"
        onclick=${() => setVisible(visible + 20)}>show more</button>`}
    <div class="chips">
      ${sec.stats.map((s) => html`
        <span class="chip" title=${s.key}>${s.label} ${s.display ?? "–"}</span>`)}
    </div>
  </div>`;
}
```

(Check `AttemptTable`/`AttemptRow`: they read `t.clock` for which time to
show — segment attempts have `igt` null, so when `t.clock === "igt"` the
time cell falls back to blank. Make `AttemptRow` prefer `a.rta` when
`a.segment_id != null`: `const time = a.segment_id != null ? a.rta : (t.clock === "igt" ? a.igt : a.rta);`
and the same for `frames`. `Timeline` posts markers keyed by
course/star — pass-through: when `sec.kind === "segment"`, the marker PUT
body sends `{segment_id: sec.segment_id, strat_tag: sec.last_strat, markers}`;
update the `send("PUT", "/api/markers", ...)` call site in timeline.js to
branch on `sec.kind`.)

- [ ] **Step 2: Pin + render in the Practice component**

The active-section logic becomes kind-aware:

```javascript
  const tgt = v.target || {};
  const segs = v.segments || [];
  const isActiveStar = (sec) => tgt.kind !== "segment"
    && sec.course_id === tgt.course_id && sec.star_id === tgt.star_id;
  const isActiveSeg = (sec) => tgt.kind === "segment"
    && sec.segment_id === tgt.segment_id;
  const activeStar = tgt.course_id != null ? v.stars.find(isActiveStar) : undefined;
  const activeSeg = segs.find(isActiveSeg);
  const restStars = v.stars.filter((sec) => sec !== activeStar);
  const restSegs = segs.filter((sec) => sec !== activeSeg);
```

Render `activeSeg` (pinned) above `activeStar`, then `restSegs` under a
`SEGMENTS` listhead, then the existing star list:

```javascript
    ${activeSeg && html`<${SegmentSection} key=${`seg:${activeSeg.segment_id}`}
        sec=${activeSeg} t=${t} ui=${ui} pinned=${true} />`}
    ${activeStar && html`<${StarSection} key=${`${activeStar.course_id}:${activeStar.star_id}`}
        sec=${activeStar} t=${t} ui=${ui} pinned=${true} />`}
    ${restSegs.length > 0 && html`<div class="listhead">Segments</div>`}
    ${restSegs.map((sec) => html`<${SegmentSection} key=${`seg:${sec.segment_id}`}
        sec=${sec} t=${t} ui=${ui} pinned=${false} />`)}
```

- [ ] **Step 3: header.js target display** — the target line branches:

```javascript
      ${tgt && tgt.kind === "segment"
        ? html` <b>⏱ ${tgt.segment_name}</b>`
        : tgt && tgt.course_id !== null
          ? html` <b>${tgt.course_name} · ${tgt.star_name}</b>`
          : html` <span class="meta">none (grab a star or set one)</span>`}
```

- [ ] **Step 4: Frontend smoke test (mandatory gate)**

Run the server (`uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064`
from repo root), then run the **frontend-smoke-test skill**: open
`http://127.0.0.1:8064`, check console for errors on: Practice tab, Segments
tab, opening the builder, creating a definition (spawned → enter Castle
Inside), setting it as target, deleting it. Fix until clean.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/components/practice.js src/sm64_events/ui/components/header.js src/sm64_events/ui/components/timeline.js
git commit -m "feat: segment sections on the practice page - pinned target, armed light, rta clock"
```

---

## Task 16b: Segment-aware replay clip naming

**Files:**
- Modify: `src/sm64_events/replay/service.py`
- Test: `tests/test_replay_service.py`

Depends on Task 13 (the `segment_defs` property on TrackerService). Without
this task, saving a segment replay produces
`attempt_…_no-course_no-star_no-igt.mp4`.

- [ ] **Step 1: Write the failing test** (append to
  `tests/test_replay_service.py`; reuse its existing attempt factory for the
  pure-function test, and extend its existing `save()` test pattern for the
  integration assert)

```python
def test_slug_filename_for_segment_attempt_uses_rta_and_segment_name():
    from sm64_events.replay.service import slug_filename
    a = make_attempt(id=10_000_000_005, segment_id=1, course_id=None,
                     star_id=None, igt_frames=None, rta_frames=85,
                     outcome="success")
    assert slug_filename(a, "LBLJ", "") == \
        "attempt_10000000005_lblj_0m02s83-rta.mp4"
```

Plus one assert in the existing save-path test style: saving a segment
attempt's clip yields a filename containing `lblj` and `-rta` (build the
fake tracker so `tracker.segment_defs` returns a `SegmentDef(id=1,
name="LBLJ", ...)`).

- [ ] **Step 2: Run** — `uv run pytest tests/test_replay_service.py -q` → FAIL.

- [ ] **Step 3: Implement** in `replay/service.py`:

`slug_filename` becomes kind-aware (existing star behavior unchanged —
both parts non-empty joins identically):

```python
def slug_filename(a, course: str, star: str) -> str:
    """Human-readable filename for a saved clip.

    IGT display format from format_igt is M'SS"CC (Usamune style).
    We replace ' -> m and " -> s so the filename is filesystem-safe:
    e.g. 0'11"43 -> 0m11s43. Segment attempts are RTA-only (spec
    2026-06-11): their time gets an explicit -rta marker.
    """
    if a.igt_frames is not None:
        igt = format_igt(a.igt_frames).replace("'", "m").replace('"', "s")
    elif a.rta_frames is not None:
        igt = format_igt(a.rta_frames).replace("'", "m").replace('"', "s") + "-rta"
    else:
        igt = "no-igt"
    suffix = "" if a.outcome == "success" else f"_{a.outcome}"
    parts = [p for p in (_slug(course), _slug(star)) if p]
    return f"attempt_{a.id:04d}_{'_'.join(parts)}_{igt}{suffix}.mp4"
```

and `save()`'s naming block branches on kind:

```python
        if a.segment_id is not None:
            c_name = next((d.name for d in self.tracker.segment_defs
                           if d.id == a.segment_id),
                          f"segment-{a.segment_id}")
            s_name = ""
        else:
            c_name = course_name(a.course_id) if a.course_id is not None else "no-course"
            s_name = (star_name(a.course_id, a.star_id)
                      if a.star_id is not None and a.course_id is not None else "no-star")
        dest = dest_dir / slug_filename(a, c_name, s_name)
```

- [ ] **Step 4: Run** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/replay/service.py tests/test_replay_service.py
git commit -m "feat: segment-aware replay clip names - segment name + rta time"
```

---

## Task 17: Docs, verify tool, live gate

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `tools/verify_addresses.py`, `src/sm64_events/memory/addresses.py`

- [ ] **Step 1: README** — event table gains rows for `area_changed`,
  `warp_entered`, `key_grabbed`, `spawned`, `segment_armed`,
  `segment_disarmed` (mark the last two broadcast-only / not journaled);
  `attempt_completed` row notes the `kind`/`segment_id`/`segment_name`
  fields; endpoint table gains the five `/api/segments*` rows and the
  kind-aware `/api/target` + `/api/markers` shapes. Document that segment
  attempts are RTA-only.

- [ ] **Step 2: CLAUDE.md module map** — add rows:

```markdown
| Segment defs, trigger vocabulary, matcher FSM | `tracking/segments.py` — ONE registry (TRIGGERS/GUARDS) drives validation, matching, and the /api/segments/vocab endpoint; docstring carries the FSM invariants |
| area_changed / warp_entered / key_grabbed / spawned | `detectors/area.py` · `detectors/warp.py` · `detectors/key.py` · `detectors/spawn.py` |
| Segments builder UI | `ui/components/segments.js` |
```

- [ ] **Step 3: tools/verify_addresses.py** — add the new addresses/actions
  to the gate following the file's existing check pattern: CURR_AREA reads
  1/2/3 across the castle hub; WARP_ENTRY_ACTIONS fires on a pipe touch;
  SPAWN edge on file-select; KEY_GRAB_LEVELS behavior of
  `gLastCompletedCourseNum/StarNum` on a key grab (record what the game
  writes — addresses.py VERIFY note); B3 grand-star attribution.

- [ ] **Step 4: LIVE GATE with the human (blocking merge — domain rule 1)**

Run: `uv run python tools/verify_addresses.py` with PJ64 + the Usamune ROM.
The tool now prints the full checklist at startup. Step-by-step:

1. **CURR_AREA address** — Phase 1 prints `[SKIP]` because `CURR_AREA = 0x0`.
   FIRST step: run `uv run python tools/hunt_value.py` while standing in the
   castle lobby (hunt value `1`); re-filter from upstairs (`2`), then basement
   (`3`). Confirm with `watch_timer.py ADDR:u16`. Replace `CURR_AREA = 0x0` in
   `addresses.py` and re-run the tool — Phase 1 should now `[PASS]`.
   Fix `CASTLE_AREA_NAMES` if the mapping differs from 1/2/3.
   Fix the BitS Entry seed's `area` param if the upstairs area id is not 2.

2. **Pipe touch** — walk into the BitDW (level 17) pipe and the BitFS (level 19)
   pipe while watching the Phase 2 live output. `warp_entered` must fire with
   a `[LIVE-GATE]` annotation. Note the action id printed — if different from
   `ACT_DISAPPEARED (0x00001300)` or `ACT_TELEPORT_FADE_OUT (0x00001336)`,
   add the observed id to `WARP_ENTRY_ACTIONS` in `addresses.py`. Also
   confirm BitS funnel (level 21) fires similarly.

3. **File-select spawn** — from the file-select screen, load the save.
   `spawned` must fire. Note `kind` in the printout: if `kind="intro"` the
   `ACT_INTRO_CUTSCENE` edge is correct; if `kind="spawn"` the intro action
   fires differently — adjust the detector logic or the action id.

4. **Key grab, B1** — enter Bowser 1 arena (level 30), defeat Bowser, touch
   the key. `key_grabbed` must fire; NO `star_collected` may fire.
   The printout shows `last_completed course=X star=Y` at the touch frame —
   record those values in a VERIFY note in `addresses.py`'s `KEY_GRAB_LEVELS`
   comment (they confirm gLastCompleted* is stale from a prior star, not
   the key).

5. **B3 grand star** — defeat Bowser 3 (level 34), touch the grand star.
   **Resolved at the 2026-06-12 live gate**: `key_grabbed` which=grand fires
   (NOT `star_collected` — the grand star enters `ACT_JUMBO_STAR_CUTSCENE`
   (0x1909), never a star-dance action; numStars unchanged (stayed 17);
   `gLastCompleted*` untouched). The Bowser 3 seed's `end_triggers` was
   amended to `[{"type": "key_grabbed", "level": 34}]`.

6. **Level ids walk-in** — walk into each of levels 7, 17, 19, 21, 23, 30, 33,
   34. Each `level_changed` payload in the Phase 2 watch must match the
   expected id. Fix `LEVEL_NAMES` entries in `addresses.py` if any id differs.
   Note: level ids are shown in the action stream (`level <N>` column).

7. **End-to-end LBLJ** — practice one real LBLJ (Castle Grounds → Castle Inside
   via the lobby entrance → enter Bowser in the Dark World pipe).
   Confirm in Phase 2 output:
   - `segment_armed` notice when entering Castle Inside from Grounds (level 16→6)
   - `attempt_completed` with `kind=segment` and a plausible RTA (sub-30 s for
     a successful LBLJ means rta_frames < 900)
   - The Segments tab in the UI shows the history

Flip each verified `VERIFY` comment in `addresses.py` to a live-verified
note with today's date (follow the existing annotation style).

**Key fact to record (for the next session):** the `key_grabbed` touch frame
is the same frame the star-dance action edges — `STAR_GRAB_ACTIONS` detects
the edge, so the detector fires one frame AFTER the last non-grab action.
This is identical to star_collected stamping. No special calibration needed.

**hard_reset / game_reset while a segment is armed:** the engine closes with
outcome `"hard_reset"` and `rta_frames = frame - arm_frame`. Since
`game_reset` carries no meaningful frame position relative to the arm, the
rta value is technically valid but not meaningful for a game reset — it is
recorded but never surfaces as a PB candidate (only `success` qualifies for
`save_pb`). No code change needed; document this behavior if it causes
confusion in the UI.

- [ ] **Step 5: Full suite + commit**

Run: `uv run pytest -q` → all pass.

```bash
git add README.md CLAUDE.md tools/verify_addresses.py src/sm64_events/memory/addresses.py
git commit -m "docs: segment events - README/module map; live gate passed, VERIFY flags flipped"
```

---

## Self-review checklist (run after writing, fixed inline)

- Spec coverage: decisions 1–6 → Tasks 9–11 (model, matcher, timing), 8
  (storage/seeds), 12–14 (first-class target, API), 15–16 (builder GUI),
  17 (VERIFY items). Out-of-scope list untouched. ✓
- Every step has real code/commands; the one intentional unknown
  (`CURR_AREA`) is an explicit live-hunt step with fallback, not a TBD. ✓
- Type consistency: `SegmentDef` fields match `segment_defs()` dict keys
  (minus `created_utc`, stripped in `_load_segment_defs`); `MatchContext`
  built only in `Projector.feed`; `Attempt.segment_id` appended last in
  dataclass, `_ATTEMPT_COLS`, and `_attempt_params`. ✓
