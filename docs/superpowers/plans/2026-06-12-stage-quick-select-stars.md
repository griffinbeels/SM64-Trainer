# Stage Quick-Select Stars Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the player loads into one of the 15 main courses, show a banner at the top of the practice page with one button per star (name + last-strategy subtext) that sets the practice target in a single click.

**Architecture:** A broadcast-only `stage_changed` detector resolves the current course from `curr_level` (reusing the existing `course_for_level` bridge) and the store tracks it reactively; a data-driven `StageBanner` component renders the current course's stars from data the session view already ships and reuses `POST /api/target` for selection. No new memory reads, no new REST endpoint, no journaled events.

**Tech Stack:** Python 3.12 (uv, pytest, FastAPI), Preact + htm (vendored, no build step), SQLite.

---

## Prerequisite (read before starting)

**The level→course bridge already exists — do NOT build it.** The concurrent
"active-star retirement" work added `COURSE_BY_LEVEL` and
`course_for_level(level)` to `src/sm64_events/memory/addresses.py` (with the
drift-guard tests in `tests/test_addresses.py`). This plan **reuses**
`course_for_level`; the spec's "add LEVEL_TO_COURSE" task is obsolete.

`course_for_level` returns a course for Bowser courses (16–18) and secret-star
areas (19–24) too, so the detector in Task 1 gates to `1 ≤ course ≤ 15` to
honor "15 main courses only".

This is a **shared checkout**; that bridge may still be uncommitted in a
sibling session's working tree. Before starting:

- [ ] **Confirm the bridge is present and committed on this branch**

Run: `git log --oneline -5 -- src/sm64_events/memory/addresses.py`
Then run: `uv run pytest -q tests/test_addresses.py`
Expected: `test_course_by_level_is_consistent_with_the_name_tables` and `test_course_for_level_returns_none_for_hubs_and_unknown` PASS. If `course_for_level` is only an uncommitted working-tree change, coordinate so that work lands first — importing an uncommitted symbol on a shared checkout is fragile.

---

## File Structure

| File | Responsibility | New? |
|---|---|---|
| `src/sm64_events/detectors/stage.py` | Resolve current main course → emit `stage_changed` | **new** |
| `tests/test_stage.py` | Detector contract | **new** |
| `src/sm64_events/main.py` | Wire the detector into the poll loop | modify |
| `tests/test_composition.py` | Assert the detector is wired | modify |
| `src/sm64_events/tracking/service.py` | Make `stage_changed` broadcast-only; cache `current_stage` | modify |
| `tests/test_tracker_service.py` | Broadcast-only + cache contract | modify |
| `src/sm64_events/tracking/views.py` | Expose `stage` in the session payload (initial load) | modify |
| `tests/test_views.py` | `stage` present in view | modify |
| `src/sm64_events/ui/store.js` | Reactive `stage` field | modify |
| `src/sm64_events/ui/components/stagebanner.js` | The banner component | **new** |
| `src/sm64_events/ui/index.html` | Banner CSS | modify |
| `src/sm64_events/ui/components/practice.js` | Render the banner at the top of the list | modify |
| `README` · `CLAUDE.md` | Document the `stage_changed` event + module-map rows | modify |

Frontend (`*.js`, `index.html`) has **no automated test harness** in this
repo — per `CLAUDE.md`, the human verifies UI live. Those tasks are build +
manual verification (Task 8), not TDD. Python tasks are TDD.

---

## Task 1: StageChangeDetector

**Files:**
- Create: `src/sm64_events/detectors/stage.py`
- Test: `tests/test_stage.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stage.py`:

```python
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.stage import StageChangeDetector


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 12, tzinfo=timezone.utc),
        global_timer=1000, mario_action=0, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        curr_level=6, curr_area=1)          # 6 = Castle Inside (no course)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_entering_a_main_course_emits_in_stage():
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))            # establish: castle
    events = d.process(snap(curr_level=6), snap(curr_level=8))   # -> SSL (course 8)
    assert len(events) == 1
    assert events[0].type == "stage_changed"
    assert events[0].payload == {"course_id": 8, "level": 8, "in_stage": True}


def test_first_pair_establishes():
    events = StageChangeDetector().process(snap(curr_level=8), snap(curr_level=8))
    assert len(events) == 1
    assert events[0].payload == {"course_id": 8, "level": 8, "in_stage": True}


def test_leaving_to_the_castle_emits_not_in_stage():
    d = StageChangeDetector()
    d.process(snap(curr_level=8), snap(curr_level=8))            # in SSL
    events = d.process(snap(curr_level=8), snap(curr_level=6))   # -> castle
    assert len(events) == 1
    assert events[0].payload == {"course_id": None, "level": 6, "in_stage": False}


def test_bowser_course_is_not_a_main_stage():
    # course_for_level(17) == 16 (a Bowser COURSE) — excluded by the 1..15 gate.
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))
    events = d.process(snap(curr_level=6), snap(curr_level=17))
    assert events[0].payload == {"course_id": None, "level": 17, "in_stage": False}


def test_secret_star_area_is_not_a_main_stage():
    # course_for_level(27) == 19 (Secret Slide course) — excluded.
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))
    events = d.process(snap(curr_level=6), snap(curr_level=27))
    assert events[0].payload["in_stage"] is False
    assert events[0].payload["course_id"] is None


def test_no_event_on_in_course_area_switch():
    # Keyed on course, not level/area: an SSL area switch (level stays 8) is
    # silent, unlike area_changed.
    d = StageChangeDetector()
    d.process(snap(curr_level=8, curr_area=1), snap(curr_level=8, curr_area=1))
    assert d.process(snap(curr_level=8, curr_area=1),
                     snap(curr_level=8, curr_area=2)) == []


def test_no_event_while_course_stable():
    d = StageChangeDetector()
    d.process(snap(curr_level=8), snap(curr_level=8))
    assert d.process(snap(curr_level=8), snap(curr_level=8)) == []


def test_reattach_gap_to_a_new_course_is_caught():
    # Keyed on last EMITTED course: a change across a detach gap still emits.
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))           # emitted: None
    events = d.process(snap(curr_level=9), snap(curr_level=9))  # reattached in BoB
    assert len(events) == 1
    assert events[0].payload == {"course_id": 1, "level": 9, "in_stage": True}


def test_frame_matches_curr_global_timer():
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))
    events = d.process(snap(curr_level=6), snap(curr_level=8, global_timer=4321))
    assert events[0].frame == 4321
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_stage.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.detectors.stage'`

- [ ] **Step 3: Write the detector**

Create `src/sm64_events/detectors/stage.py`:

```python
# src/sm64_events/detectors/stage.py
"""stage_changed: the COURSE the player is standing in, for the practice-page
quick-select banner. Resolves gCurrLevelNum -> course id via
addresses.course_for_level and keeps ONLY the 15 main courses (1-15): Bowser
courses (16-18), the secret-star areas (19-24), hub levels and Bowser arenas
all read as in_stage=False, so the banner shows for paintings/warps into a real
course and nothing else.

Broadcast-only (never journaled): stage is a live presentation signal, fully
recomputable from curr_level, with no historical-query value -- service.publish
caches it on current_stage and skips the journal (see service.py). Mirrors
level.py's last-EMITTED discipline so the first pair establishes and a course
change while detached still emits; keyed on the resolved course_id (NOT the raw
level) so an in-course area switch (SSL area 1<->2, both course 8) is silent.
course_id can legitimately be None (not in a main course), so the
'never-emitted-yet' sentinel is a distinct object, not None."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import course_for_level

_UNSET = object()


class StageChangeDetector:
    def __init__(self):
        self._last = _UNSET   # last EMITTED course_id (main-course int | None)

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        course = course_for_level(curr.curr_level)
        course_id = course if course is not None and 1 <= course <= 15 else None
        if self._last is not _UNSET and course_id == self._last:
            return []
        self._last = course_id
        return [Event(type="stage_changed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"course_id": course_id,
                               "level": curr.curr_level,
                               "in_stage": course_id is not None})]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_stage.py`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/detectors/stage.py tests/test_stage.py
git commit -m "feat(detectors): stage_changed - current main course for the quick-select banner"
```

---

## Task 2: Wire the detector into the poll loop

**Files:**
- Modify: `src/sm64_events/main.py`
- Test: `tests/test_composition.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_composition.py` (after `test_detector_order_is_load_bearing`):

```python
def test_stage_detector_is_wired():
    src = (Path(sm64_events.__file__).parent / "main.py").read_text(encoding="utf-8")
    assert "StageChangeDetector()" in src
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/test_composition.py::test_stage_detector_is_wired`
Expected: FAIL — `assert 'StageChangeDetector()' in src`

- [ ] **Step 3: Wire it in**

In `src/sm64_events/main.py`, add the import alongside the other detectors (keep imports alphabetical — it goes right after the `spawn` import, before `star_grab`):

```python
from sm64_events.detectors.stage import StageChangeDetector
```

Then insert it into the `detectors = [...]` list right after `AreaChangeDetector()` (the level-derived family; it is broadcast-only and touches no attempt state, so its position is informational — keep it before `AnchorDetector` so the load-bearing reset/anchor/grab order is untouched):

```python
    detectors = [GameResetDetector(), LevelChangeDetector(),
                 AreaChangeDetector(), StageChangeDetector(), AnchorDetector(),
                 DeathDetector(), DustTrickDetector(), WarpDetector(),
                 KeyGrabDetector(), SpawnDetector(), StarGrabDetector()]
```

- [ ] **Step 4: Run the composition tests to verify they pass**

Run: `uv run pytest -q tests/test_composition.py`
Expected: PASS (both `test_detector_order_is_load_bearing` and `test_stage_detector_is_wired`)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/main.py tests/test_composition.py
git commit -m "feat(main): wire StageChangeDetector into the poll loop"
```

---

## Task 3: Make `stage_changed` broadcast-only and cache `current_stage`

**Files:**
- Modify: `src/sm64_events/tracking/service.py`
- Test: `tests/test_tracker_service.py`

Context: the Poller publishes every detector event through `service.publish()`,
which journals. `stage_changed` must NOT be journaled — handle it like the
segment notices (broadcast, then skip the journal) and cache the latest payload
so the session view can serve it on initial load.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tracker_service.py` (it already defines `make`, `make_rec`, `ev`):

```python
def test_stage_changed_is_broadcast_only_and_cached(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.publish(ev("stage_changed", 200,
                               {"course_id": 8, "level": 8, "in_stage": True})))
    # broadcast to clients...
    assert "stage_changed" in [e.type for e in sent]
    # ...but NEVER journaled (recomputable; no historical-query value)
    assert "stage_changed" not in [e.type for e in db.events()]
    # ...and cached for the session view's initial load
    assert svc.current_stage == {"course_id": 8, "level": 8, "in_stage": True}


def test_current_stage_defaults_to_not_in_stage(tmp_path):
    db, svc = make(tmp_path)
    assert svc.current_stage["in_stage"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_tracker_service.py::test_stage_changed_is_broadcast_only_and_cached tests/test_tracker_service.py::test_current_stage_defaults_to_not_in_stage`
Expected: FAIL — `AttributeError: 'TrackerService' object has no attribute 'current_stage'`

- [ ] **Step 3: Implement**

In `src/sm64_events/tracking/service.py`, in `TrackerService.__init__`, add the cache field (after `self._projector = ...`):

```python
        self._current_stage = {"course_id": None, "level": None,
                               "in_stage": False}
```

Replace the top of `publish()` so `stage_changed` short-circuits after the
broadcast (this must run even when `db is None`, so it goes BEFORE the db
check):

```python
    async def publish(self, event: Event) -> None:
        seq = await self.broadcaster.publish(event)
        if event.type == "stage_changed":
            # Live presentation signal: cache for the session view's initial
            # load and NEVER journal it (recomputable from curr_level; a
            # journal row would only add replay/projection noise). Same
            # broadcast-only discipline as the segment notices.
            self._current_stage = dict(event.payload)
            return
        if self.db is None or self.session_id is None:
            return
        try:
            await self._track(event, seq)
        except Exception:
            log.exception("tracking pipeline failed for %s; event broadcast only",
                          event.type)
```

Add the property next to the other `@property` state accessors (e.g. after
`strat_by_segment`):

```python
    @property
    def current_stage(self) -> dict:
        """The main course the player is standing in (else in_stage=False),
        cached from the broadcast-only stage_changed event for the session
        view's initial load. See detectors/stage.py."""
        return self._current_stage
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_tracker_service.py`
Expected: PASS (all, including the two new tests)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/service.py tests/test_tracker_service.py
git commit -m "feat(service): stage_changed is broadcast-only; cache current_stage"
```

---

## Task 4: Expose `stage` in the session view payload

**Files:**
- Modify: `src/sm64_events/tracking/views.py`
- Test: `tests/test_views.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_views.py` (it already defines `make`, `ev`, and imports `build_session_view`):

```python
def test_view_includes_current_stage(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("stage_changed", 100,
                               {"course_id": 8, "level": 8, "in_stage": True})))
    view = build_session_view(db, svc, clock="igt")
    assert view["stage"] == {"course_id": 8, "level": 8, "in_stage": True}


def test_view_stage_defaults_to_not_in_stage(tmp_path):
    db, svc = make(tmp_path)
    view = build_session_view(db, svc, clock="igt")
    assert view["stage"]["in_stage"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_views.py::test_view_includes_current_stage tests/test_views.py::test_view_stage_defaults_to_not_in_stage`
Expected: FAIL — `KeyError: 'stage'`

- [ ] **Step 3: Add the field**

In `src/sm64_events/tracking/views.py`, in the dict returned by
`build_session_view`, add `"stage"` right after `"last_strat_by_star"`:

```python
        "last_strat_by_star": {f"{c}:{s}": v
                               for (c, s), v in service.strat_by_star.items()},
        "stage": service.current_stage,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_views.py`
Expected: PASS (all, including the two new tests)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/views.py tests/test_views.py
git commit -m "feat(views): expose current_stage in the session payload"
```

- [ ] **Step 6: Full suite checkpoint**

Run: `uv run pytest -q`
Expected: PASS (whole suite green before frontend work)

---

## Task 5: Add a reactive `stage` field to the store

**Files:**
- Modify: `src/sm64_events/ui/store.js`

No JS test harness — verified live in Task 8.

- [ ] **Step 1: Add the state declaration**

In `useTracker()`, after the `lastPinnedSeg` declaration, add:

```javascript
  // stage: the main course the player is currently in (or null / in_stage:false).
  // Driven by the broadcast-only stage_changed WS event; intentionally NOT in
  // REFRESH_ON — the view's catalog and last_strat_by_star don't depend on it,
  // so a full refetch would be wasted. Seeded from v.stage for initial load.
  const [stage, setStage] = useState(null);
```

- [ ] **Step 2: Seed `stage` from the view on every refresh**

In `refresh()`, right after `setView(v);`, add:

```javascript
      setStage(v ? v.stage : null);
```

- [ ] **Step 3: Update `stage` on the live WS event**

In `ws.onmessage`, extend the `segment_armed`/`segment_disarmed` branch chain
with a `stage_changed` arm:

```javascript
        } else if (ev.type === "stage_changed") {
          setStage(ev.payload);
        }
```

(Add it as the final `else if` after the `segment_disarmed` block.)

- [ ] **Step 4: Return `stage` from the hook**

In the returned object, add `stage`:

```javascript
  return { view, clock, pickClock, scope, pickScope, feed, connected,
           refresh, paused: pauseState.paused,
           pauseReason: pauseState.reason, togglePause,
           armedSegs, armedOrder, lastPinnedSeg, stage };
```

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/store.js
git commit -m "feat(ui): track current stage in the store"
```

---

## Task 6: The StageBanner component

**Files:**
- Create: `src/sm64_events/ui/components/stagebanner.js`
- Modify: `src/sm64_events/ui/index.html` (CSS)

- [ ] **Step 1: Write the component**

Create `src/sm64_events/ui/components/stagebanner.js`:

```javascript
// src/sm64_events/ui/components/stagebanner.js
// Quick-select star row. When the player loads into one of the 15 main courses
// (t.stage.in_stage, set from the broadcast-only stage_changed event), surface
// that course's stars as one-click target buttons. 100% data-driven from the
// session view the store already holds: catalog.courses for names,
// last_strat_by_star for the subtext, target for the active highlight. One
// click POSTs /api/target carrying the star's last strategy -- the same
// endpoint the header TargetEditor uses, so the normal target_changed flow
// updates the header, the pinned active-star section, and this banner.
import { h } from "preact";
import htm from "htm";
import { send } from "../api.js";

const html = htm.bind(h);

export function StageBanner({ t }) {
  const v = t.view;
  const stage = t.stage;
  if (!v || !stage || !stage.in_stage) return null;

  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  if (!course) return null;

  const tgt = v.target || {};
  const lastStratFor = (i) =>
    v.last_strat_by_star[`${stage.course_id}:${i}`] || "";

  async function pick(i) {
    await send("POST", "/api/target", {
      course_id: stage.course_id, star_id: i,
      strat_tag: lastStratFor(i) || null,
    });
    t.refresh();
  }

  return html`<div class="starsec stagebanner">
    <div class="shead"><b>▸ ${course.name}</b>
      <span class="meta">tap a star to practice</span></div>
    <div class="stagebanner-row">
      ${course.stars.map((name, i) => {
        const active = tgt.kind !== "segment"
          && tgt.course_id === stage.course_id && tgt.star_id === i;
        const strat = lastStratFor(i);
        return html`<button key=${i}
                            class="stagebtn ${active ? "active-star" : ""}"
                            onclick=${() => pick(i)}>
          <span class="stagebtn-name">${name}</span>
          <span class="stagebtn-sub meta">${strat || "—"}</span>
        </button>`;
      })}
    </div>
  </div>`;
}
```

- [ ] **Step 2: Add the CSS**

In `src/sm64_events/ui/index.html`, in the `<style>` block (near the existing
`.active-star` rule), add:

```css
  .stagebanner { border-color: #2f6d8c; }
  .stagebanner-row { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .4rem; }
  .stagebtn { display: flex; flex-direction: column; align-items: flex-start;
              gap: .15rem; min-width: 7rem; padding: .35rem .55rem;
              text-align: left; cursor: pointer; }
  .stagebtn-name { font-weight: 600; line-height: 1.2; }
  .stagebtn-sub { font-size: .8em; }
  .stagebtn.active-star { border-color: #e0c36a; }
```

(`.starsec`, `.shead`, `.meta`, and `.active-star` already exist and are
reused as-is.)

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/ui/components/stagebanner.js src/sm64_events/ui/index.html
git commit -m "feat(ui): StageBanner quick-select component + styles"
```

---

## Task 7: Render the banner on the practice page

**Files:**
- Modify: `src/sm64_events/ui/components/practice.js`

- [ ] **Step 1: Import the component**

At the top of `src/sm64_events/ui/components/practice.js`, add to the imports
(after the `Progress` import):

```javascript
import { StageBanner } from "./stagebanner.js";
```

- [ ] **Step 2: Render it above the pinned sections**

In the component's returned template, insert the banner right after
`<${ControlBar} ui=${ui} />` and before the `pinnedSegs.map(...)` line:

```javascript
    <${ControlBar} ui=${ui} />
    <${StageBanner} t=${t} />
    ${pinnedSegs.map((sec) => html`<${SegmentSection} key=${`seg:${sec.segment_id}`} sec=${sec} t=${t} ui=${ui} pinned=${true} freshIds=${freshIds} />`)}
```

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/ui/components/practice.js
git commit -m "feat(ui): show the StageBanner at the top of the practice list"
```

---

## Task 8: Live verification with the human

No automated coverage for the UI — verify in the browser with the emulator.

- [ ] **Step 1: Start the server**

Run: `uv run python -m sm64_events.main`
Open: `http://127.0.0.1:8064/`

- [ ] **Step 2: Verify each behavior**

- [ ] Warp into a course **via Usamune** → the banner appears titled with the course name, showing 7 buttons (6 named stars + "100 Coins"), each with a strategy subtext (or `—`).
- [ ] Enter a course **via its painting** → same banner appears (both paths drive `curr_level`).
- [ ] Click a star button → the header `Target:` updates to that course · star in one click; the pinned active-star section appears; the clicked button shows the gold `.active-star` border.
- [ ] A star with a previously-set strategy → clicking it carries that strategy (header shows `«strat»`).
- [ ] Walk/warp back to the castle → the banner disappears.
- [ ] Enter **Bowser in the Dark World** or a **secret slide/cap** → **no** banner (main courses only).
- [ ] Switch areas inside one course (e.g. SSL pyramid top) → the banner stays put, no flicker.
- [ ] Reload the page while standing in a course → the banner is present immediately (served from `view.stage`).

- [ ] **Step 3: Note any issues**

If a behavior is wrong, fix the relevant module and re-verify before closing.

---

## Task 9: Documentation

**Files:**
- Modify: `README` (the WS event list)
- Modify: `CLAUDE.md` (module map)

- [ ] **Step 1: Document the event in the README**

In the WebSocket events section of the README, add an entry:

```
- `stage_changed` — broadcast-only (never journaled). The main course the
  player is currently standing in, for the practice quick-select banner.
  payload: { "course_id": int | null, "level": int, "in_stage": bool }.
  in_stage is true only for the 15 main courses (1-15); Bowser courses,
  secret-star areas, hubs and arenas all report in_stage:false.
```

- [ ] **Step 2: Add the module-map rows in CLAUDE.md**

In the "Module map" table, add:

```
| Stage detection (current main course → quick-select banner) | `detectors/stage.py` — broadcast-only `stage_changed`; reuses `course_for_level` (addresses.py), gates to courses 1-15 |
| Stage quick-select banner | `ui/components/stagebanner.js` — one-click star target from the current course; data-driven from the session view; rendered atop `ui/components/practice.js` |
```

- [ ] **Step 3: Commit**

```bash
git add README CLAUDE.md
git commit -m "docs: document stage_changed event + stage quick-select banner"
```

- [ ] **Step 4: Final full-suite gate**

Run: `uv run pytest -q`
Expected: PASS (whole suite green)

---

## Done when

- `uv run pytest -q` passes (new Python behavior covered by `test_stage.py`,
  `test_composition.py`, `test_tracker_service.py`, `test_views.py`).
- Live verification (Task 8) all checked with the human.
- README + CLAUDE.md updated; the spec
  (`docs/superpowers/specs/2026-06-12-stage-quick-select-stars-design.md`)
  remains the design record (note: its "build the bridge" step was obsoleted by
  the pre-existing `course_for_level`).
