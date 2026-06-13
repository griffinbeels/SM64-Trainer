# Stage Quick-Select Stars — one-click target from the current stage

Date: 2026-06-12 · Status: approved by user (brainstorming session)

## Problem

Telling the tracker which star you're practicing takes several clicks: open
the header `TargetEditor` popover, pick the course, pick the star, set it.
But the system already knows which **stage** you just loaded into — both a
manual painting entry and a Usamune warp drive `gCurrLevelNum` to that
course's level. We can collapse "signal my practice target" to **one click**
by surfacing the current stage's stars as buttons the moment you enter.

Goal: when you load into one of the 15 main courses, a row of buttons
appears — one per star, labeled with the star name, with a subtext showing
the most recently practiced strategy for that star. One click sets the
active target to that star (carrying its last strategy). The row exists to
make it trivial to declare the target *before* you grab the star.

## Scope (decided with user)

- **Stages:** the 15 main courses only (BOB→RR). Bowser levels, the castle,
  caps, slides, and secret-star areas never show the row.
- **Stars per stage:** all 7 (`star_count()` — six named + the 100-coin
  star at `star_id` 6, rendered as a normal trailing button).
- **Grabbed state:** none in v1. Every button is always live — it's a
  practice tool, you re-grab stars freely. No new memory reads, no per-star
  collected dimming. ("If you haven't grabbed it yet" is the *purpose*, not
  a rendered state.)
- **Lifecycle:** the row shows only while you're standing in a main course;
  it hides on leaving (return to castle / any non-course level) and
  re-appears on the next stage entry.
- **Placement:** a banner card at the top of the practice list (above the
  pinned-segment sections), scrolling with the content — practice tab only.

## Key finding — the level→course bridge does not exist yet

Star identity is `(course_id, star_id)`. Stage detection only yields
`curr_level`, a decomp **LEVEL id** (BOB=9, WF=24), which is a different
number space from the **COURSE id** (BOB=1, WF=2) with no offset. The
codebase deliberately never derives course from level — at grab time it
reads `gLastCompletedCourse` directly (`star_grab.py`), and `addresses.py`
carries an explicit trap note about a past misread. An exhaustive search
(src/, tools/, tests/, docs/) confirmed **no level↔course mapping exists**.

However, `LEVEL_NAMES` (by level id) and `COURSE_NAMES` (by course id)
contain **identical strings** for all 15 main courses. So the bridge is
*derived from the two existing tables by name*, not hand-authored — less
code and self-validating.

## Decision — Approach A (backend stage detector + derived static bridge)

Chosen over two alternatives:

- *Pure frontend derivation* (a level→course map in JS, stage reconstructed
  from the raw event feed): rejected — puts the domain mapping in a second
  source of truth, against this codebase's "speak only to the API / one
  registry" discipline.
- *Authoritative `gCurrCourseNum` memory read*: rejected — needs a
  live-verify gate AND reads stale outside courses (holds the last course
  while in the castle), so it would still need a level/area gate to know
  you're actually in a stage. More moving parts, no gain over the bridge.

Approach A adds no memory read and needs no live-verify gate; it rides the
already-verified `curr_level`. The "main-courses-only" and "hide on leave"
rules fall out of the bridge returning `None` for everything else.

## Design

### 1. The bridge (`memory/addresses.py`)

Placed directly below `LEVEL_NAMES` / `COURSE_NAMES`:

```python
_COURSE_BY_NAME = {name: cid for cid, name in COURSE_NAMES.items()
                   if 1 <= cid <= 15}
LEVEL_TO_COURSE = {lvl: _COURSE_BY_NAME[name]
                   for lvl, name in LEVEL_NAMES.items()
                   if name in _COURSE_BY_NAME}   # 15 entries; non-courses absent

def course_for_level(level_id: int) -> int | None:
    """Course id (1-15) for a main-course level, else None (castle, Bowser,
    caps, secret areas). The 'am I in a practiceable stage?' gate."""
    return LEVEL_TO_COURSE.get(level_id)
```

`test_addresses.py` pins the bridge: for every entry
`LEVEL_NAMES[lvl] == COURSE_NAMES[LEVEL_TO_COURSE[lvl]]`; exactly the 15
courses 1–15 are covered, each once; `course_for_level` returns `None` for a
castle level, a Bowser arena, and a cap/secret level. A future name edit
that breaks the correspondence fails this test rather than silently drifting.

### 2. Stage detector (`detectors/stage.py`, new)

`process(prev, curr) -> list[Event]`, mirroring `level.py`'s last-EMITTED
discipline. Computes `course_for_level(curr.curr_level)`; when the resolved
course changes from the last emitted value, emits one **`stage_changed`**:

```
type: "stage_changed"
payload: {"course_id": int | None, "level": int, "in_stage": bool}
```

Entering BOB → `{"course_id": 1, "level": 9, "in_stage": true}`; warping back
to the castle → `{"course_id": null, "level": 6, "in_stage": false}`.
Self-heals on `global_timer` backward jumps. No event while the course is
unchanged — crucially, an SSL area switch (area 1↔2, both course 8) produces
no spurious `stage_changed`.

`stage_changed` is **broadcast-only, not journaled.** Stage is a live
presentation signal, fully recomputable from `curr_level`, with no
historical-query value; journaling it would add replay/projection noise.
This is a deliberate departure from `level_changed`, which *is* journaled
because attempts close against it.

### 3. Initial-load state (`tracking/service.py`, `tracking/views.py`)

The detector's latest result is cached on the service as `current_stage`
(same shape as the event payload), exposed like `strat_by_star`, so a page
that loads *while already standing in a stage* (no recent transition) still
knows it. `views.py` adds `"stage": service.current_stage` to the session
payload.

### 4. Wiring (`main.py`)

Register `StageDetector` alongside the other detectors in the composition
root; route its events to the broadcaster (not the journal).

### 5. Store (`ui/store.js`)

A reactive `stage` field: initialized from `v.stage`, updated directly on
each `stage_changed` WS event (no full view refetch). `stage_changed` is
added to the WS event handler but **not** to `REFRESH_ON`.

### 6. Banner (`ui/components/stagebanner.js`, new)

Renders only when `t.stage?.in_stage`. Everything else comes from data the
session view already ships:

- title: `▸ {course_name} — tap a star to practice`
- one button per star, `i` in `0..star_count(course)-1`:
  - **name** = `star_name(course, i)` (full names, e.g. "In the Talons of
    the Big Bird"); the 100-coin star is the 7th button.
  - **subtext** = `v.last_strat_by_star["{course}:{i}"]` or a muted `—`.
  - **click** → `POST /api/target {course_id, star_id: i,
    strat_tag: <that star's last_strat>}`. One click; the existing
    `target_changed` flow updates header + banner.
  - the currently-targeted star (if it's in this course) renders highlighted
    (reuse the `isActiveStar` identity check from `practice.js`).

100% data-driven from `addresses.py` helpers via the catalog — no star
names or course list duplicated in JS.

### 7. Placement (`ui/components/practice.js`)

Render `<${StageBanner} t=${t} />` at the top of the list, above the pinned
segment sections (the B mockup the user selected).

## Data flow

Warp/walk into SSL → `curr_level` = 8 → `StageDetector` emits
`stage_changed {course_id: 8, in_stage: true}` → broadcast → store sets
`t.stage` → banner appears with SSL's 7 stars → click "Inside the Ancient
Pyramid" → `POST /api/target` → `target_changed` → header + banner reflect
the target. Leave to the castle → `stage_changed {in_stage: false}` → banner
unmounts. Both entry paths (painting, Usamune warp) move `curr_level`
identically, so both work with no special-casing.

## Testing

- `test_addresses.py` — bridge cross-check (names match; 15 courses each
  once; non-courses → `None`).
- `test_stage.py` (new) — enter a main course emits the right `course_id`;
  enter castle / Bowser / cap emits `in_stage: false`; **no** event across an
  SSL area switch (course unchanged); self-heal on `global_timer` reset;
  boot-transient level id 1 → `in_stage: false` (no garbage stage).
- `test_views.py` — session payload carries `stage`.
- Live check with the human: warp into several courses, confirm the row
  renders and one click sets the target; leave and confirm it hides.

## Files touched

`memory/addresses.py` · `detectors/stage.py` (new) · `tracking/service.py` ·
`tracking/views.py` · `main.py` · `ui/store.js` ·
`ui/components/stagebanner.js` (new) · `ui/components/practice.js` · their
tests · `README` (document the `stage_changed` event) · `CLAUDE.md` module
map (stage detector row).

## Risks / coordination

- **Shared checkout, concurrent session.** At spec time the working tree had
  uncommitted foreign edits to `memory/addresses.py`, `practice.js`, and
  `tests/test_addresses.py` — the exact shared-contract files this feature
  touches. `addresses.py` is a "never edit in two branches at once" contract
  per `CLAUDE.md`. The implementation session must re-check the branch and
  rebase/merge cleanly against whatever has landed before editing these
  files, and add to (not overwrite) the existing `addresses.py` /
  `test_addresses.py` changes.
- **Level-id coverage.** The bridge derives from `LEVEL_NAMES`; all 15
  main-course level ids there are already live-verified (per its comment),
  so no new live gate is required — but the cross-check test is the guard if
  a name is ever edited.
