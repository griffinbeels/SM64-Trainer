# Garbage-Run Discard, Timeline Markers, Progress Graph, Practice-View Layout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discard AFK/no-behavior runs automatically, add per-strategy timeline annotations, a completion-time-over-time progress graph, and a pinned-active-star practice layout with sort/hide-resets controls.

**Architecture:** All five features ride existing seams: the `AnchorDetector` gains a pause-streak counter and a new `mario_acted` journal event; the pure projection gains two discard policies; markers live in the existing `ui_state` KV; the session view gains `markers_by_strat`/`progress` payloads and recency ordering; the Preact UI gains one new component and a control bar. No new memory addresses, no schema migration.

**Tech Stack:** Python 3.12 (uv, never pip), FastAPI + pydantic, pytest, Preact + htm (vendored, no build step).

**Spec:** `docs/superpowers/specs/2026-06-11-garbage-runs-markers-progress-ui-design.md` — read it first; the decision log there is authoritative.

**Commands you will use constantly** (run from repo root):

```bash
uv run pytest -q                      # full suite — must pass before every commit
uv run pytest tests/test_anchors.py -q   # one file
uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064   # live server (UI tasks)
```

**House rules that apply to every task** (from CLAUDE.md): tests first; commit messages explain WHY in imperative mood; UTC timestamps; the UI is served per request (edit + refresh, no restart); detectors must self-heal when global_timer jumps backward.

---

## File structure

| File | Role in this plan |
|---|---|
| `src/sm64_events/detectors/anchors.py` | MODIFY — pause streak, `mario_acted` event, two new payload keys |
| `src/sm64_events/tracking/projection.py` | MODIFY — `PAUSE_DISCARD_FRAMES` policy + acted-rule for all closures |
| `src/sm64_events/tracking/views.py` | MODIFY — `markers_by_strat`, `progress`, section ordering, target guarantee |
| `src/sm64_events/server/api.py` | MODIFY — `PUT /api/markers` |
| `src/sm64_events/ui/components/timeline.js` | MODIFY — annotation flags, click-to-place, chip editor |
| `src/sm64_events/ui/components/progress.js` | CREATE — completion-time-over-time SVG scatter |
| `src/sm64_events/ui/components/practice.js` | MODIFY — pinned active star, control bar, sort/filter |
| `src/sm64_events/ui/index.html` | MODIFY — three CSS classes |
| `tests/test_anchors.py`, `tests/test_projection.py`, `tests/test_views.py`, `tests/test_api.py` | MODIFY — new tests + a few updated assertions |
| `README.md`, `CLAUDE.md`, `docs/architecture.md` | MODIFY — Task 10 |

Tasks 1–6 are backend (pytest-gated). Tasks 7–9 are UI (no JS test harness in this repo — gated by the live smoke test in Task 10). Task 10 is docs + gates.

---

### Task 1: AnchorDetector — pause streak + `mario_acted` event

**Files:**
- Modify: `src/sm64_events/detectors/anchors.py`
- Test: `tests/test_anchors.py`

The detector already tracks an `_acted` flag and stamps it into anchor payloads. This task adds: (a) a pause-streak counter (game frames where `global_timer` advanced but `igt_overall` didn't — that is "game logic stopped", i.e. the Usamune pause menu); (b) a `mario_acted` event emitted once per anchor period at the first non-passive action; (c) `paused_frames_before` and `acted_tracking` keys on both anchor payloads.

- [ ] **Step 1: Add the new failing tests to `tests/test_anchors.py`** (append at end of file):

```python
# ---------------------------------------------------------------------------
# Pause-streak tests (AFK rule, spec §1)
# ---------------------------------------------------------------------------

def test_pause_streak_stamped_on_practice_reset():
    d = AnchorDetector()
    # paused: global_timer advances, igt frozen at 500
    assert d.process(snap(1000, igt=500), snap(1100, igt=500)) == []
    assert d.process(snap(1100, igt=500), snap(1200, igt=500)) == []
    events = d.process(snap(1200, igt=500), snap(1202, igt=0))
    assert events[0].type == "practice_reset"
    assert events[0].payload["paused_frames_before"] == 200


def test_pause_streak_resets_when_igt_advances():
    d = AnchorDetector()
    d.process(snap(1000, igt=500), snap(1100, igt=500))   # +100 paused
    d.process(snap(1100, igt=500), snap(1101, igt=501))   # igt moved -> 0
    events = d.process(snap(1101, igt=501), snap(1103, igt=0))
    assert events[0].payload["paused_frames_before"] == 0


def test_pause_streak_stamped_on_state_loaded():
    d = AnchorDetector()
    d.process(snap(1000, igt=500), snap(1100, igt=500))   # +100 paused
    events = d.process(snap(1100, igt=500), snap(900, igt=120))  # backward, mid-range
    assert events[0].type == "state_loaded"
    assert events[0].payload["paused_frames_before"] == 100


def test_console_reset_path_resets_pause_streak():
    d = AnchorDetector()
    d.process(snap(1000, igt=500), snap(1100, igt=500))      # +100 paused
    assert d.process(snap(1100, igt=500), snap(50, igt=5)) == []  # boot range: no anchor
    assert d.process(snap(50, igt=5), snap(80, igt=5)) == []      # +30 paused
    events = d.process(snap(80, igt=5), snap(82, igt=0))
    assert events[0].payload["paused_frames_before"] == 30        # not 130


def test_equal_global_timer_does_not_grow_streak():
    d = AnchorDetector()
    d.process(snap(1000, igt=500), snap(1000, igt=500))   # same frame polled twice
    events = d.process(snap(1000, igt=500), snap(1002, igt=0))
    assert events[0].payload["paused_frames_before"] == 0


def test_streak_resets_after_anchor_fires():
    d = AnchorDetector()
    d.process(snap(1000, igt=500), snap(1200, igt=500))   # +200 paused
    d.process(snap(1200, igt=500), snap(1202, igt=0))     # anchor: stamps 200, resets
    events = d.process(snap(1202, igt=400), snap(1204, igt=0))
    assert events[0].payload["paused_frames_before"] == 0


# ---------------------------------------------------------------------------
# mario_acted event tests (spec §2)
# ---------------------------------------------------------------------------

def test_first_nonpassive_action_emits_mario_acted_event():
    d = AnchorDetector()
    events = d.process(snap(1000, igt=100), snap(1001, igt=101, action=ACT_WALKING))
    assert [e.type for e in events] == ["mario_acted"]
    assert events[0].frame == 1001
    assert events[0].payload == {}


def test_mario_acted_emitted_once_per_anchor_period():
    d = AnchorDetector()
    d.process(snap(1000, igt=100), snap(1001, igt=101, action=ACT_WALKING))
    assert d.process(snap(1001, igt=101),
                     snap(1002, igt=102, action=ACT_WALKING)) == []


def test_mario_acted_re_emitted_after_anchor():
    d = AnchorDetector()
    d.process(snap(1000, igt=100), snap(1001, igt=101, action=ACT_WALKING))
    d.process(snap(1001, igt=500), snap(1002, igt=0))     # anchor resets the period
    events = d.process(snap(1002, igt=1), snap(1003, igt=2, action=ACT_WALKING))
    assert [e.type for e in events] == ["mario_acted"]


def test_anchor_payloads_carry_acted_tracking_marker():
    events = AnchorDetector().process(snap(1000, igt=500), snap(1002, igt=0))
    assert events[0].payload["acted_tracking"] is True
    events = AnchorDetector().process(snap(5000, igt=900), snap(3000, igt=120))
    assert events[0].payload["acted_tracking"] is True
```

- [ ] **Step 2: Update the two existing payload-EQUALITY assertions** in `tests/test_anchors.py` (they compare full dicts and will fail when keys are added). In `test_igt_drop_to_zero_emits_practice_reset` replace:

```python
    assert ev.payload == {"igt_frames_before": 500, "mario_acted": False}
```

with:

```python
    assert ev.payload == {"igt_frames_before": 500, "mario_acted": False,
                          "paused_frames_before": 0, "acted_tracking": True}
```

In `test_backward_global_timer_emits_state_loaded` replace:

```python
    assert ev.payload == {"igt_frames_restored": 120, "mario_acted": False}
```

with:

```python
    assert ev.payload == {"igt_frames_restored": 120, "mario_acted": False,
                          "paused_frames_before": 0, "acted_tracking": True}
```

- [ ] **Step 3: Run the tests — verify the new ones fail**

Run: `uv run pytest tests/test_anchors.py -q`
Expected: ~12 failures (KeyError `paused_frames_before` / `acted_tracking`, missing `mario_acted` event, dict mismatch). The pre-existing tests not touching payload equality still pass.

- [ ] **Step 4: Implement.** Replace the `AnchorDetector` class in `src/sm64_events/detectors/anchors.py` (keep module docstring, imports, and the three constants; ADD the docstring paragraphs shown):

Append to the module docstring (after the `mario_acted` paragraph, before `VERIFY`):

```
Pause streak: consecutive game frames where global_timer advanced but the
  overall IGT did not — game logic stopped, i.e. the Usamune pause menu (or a
  dialog time-stop). Stamped on anchors as paused_frames_before; the tracking
  layer discards reset-closures after long pauses (AFK rule). Emulator pause
  freezes BOTH clocks, so it never grows the streak (documented limitation).
mario_acted event: emitted once per anchor period at Mario's first
  non-passive action, so the tracking layer can judge activity for closures
  that are NOT anchors (death/abandon/hard reset). Anchors additionally carry
  acted_tracking: true so old journals (no such events) keep legacy semantics.
```

New class body:

```python
class AnchorDetector:
    def __init__(self):
        self._acted = False
        self._acted_reported = False
        self._pause_streak = 0

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        events = self._classify(prev, curr)
        if events:
            # the action transition ON the anchor tick is swallowed — it
            # belongs to the warp/spawn, not to either attempt
            self._acted = False
            self._acted_reported = False
            self._pause_streak = 0
            return events
        self._update_pause_streak(prev, curr)
        if curr.mario_action not in PASSIVE_ACTIONS:
            self._acted = True
            if not self._acted_reported:
                self._acted_reported = True
                return [Event(type="mario_acted", frame=curr.global_timer,
                              timestamp_utc=curr.wall_time_utc, payload={})]
        return []

    def _update_pause_streak(self, prev: GameSnapshot, curr: GameSnapshot) -> None:
        if curr.global_timer < prev.global_timer:
            self._pause_streak = 0   # boot-range backward jump (no anchor fired)
        elif curr.igt_overall != prev.igt_overall:
            self._pause_streak = 0   # game logic is running
        elif curr.global_timer > prev.global_timer:
            self._pause_streak += curr.global_timer - prev.global_timer
        # equal global_timer: polled faster than one frame — no information

    def _classify(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.global_timer < prev.global_timer:
            if curr.global_timer < BOOT_TIMER_MAX:
                return []  # console reset — GameResetDetector owns this
            return [Event(type="state_loaded", frame=curr.global_timer,
                          timestamp_utc=curr.wall_time_utc,
                          payload={"igt_frames_restored": curr.igt_overall,
                                   "mario_acted": self._acted,
                                   "paused_frames_before": self._pause_streak,
                                   "acted_tracking": True})]
        if (curr.igt_overall < prev.igt_overall
                and curr.igt_overall <= NEAR_ZERO_IGT
                and prev.igt_overall < IGT_WRAP_CEILING):
            return [Event(type="practice_reset", frame=curr.global_timer,
                          timestamp_utc=curr.wall_time_utc,
                          payload={"igt_frames_before": prev.igt_overall,
                                   "mario_acted": self._acted,
                                   "paused_frames_before": self._pause_streak,
                                   "acted_tracking": True})]
        return []
```

Note: `mario_acted` events flow through `TrackerService.publish` like any
detector event — they are journaled automatically; the projector handles them
in Task 3 (until then `feed()` falls through to the default `return []`).

- [ ] **Step 5: Run the file, then the full suite**

Run: `uv run pytest tests/test_anchors.py -q` → all pass.
Run: `uv run pytest -q` → all pass (other suites never assert anchor payload equality; `tests/test_poller_isolation.py` and others are unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/detectors/anchors.py tests/test_anchors.py
git commit -m "feat: anchor pause streak + mario_acted event (AFK + activity groundwork)

Pause streak counts frames where global_timer runs but IGT is frozen (the
Usamune menu); anchors stamp it as paused_frames_before so the projection
can discard AFK-then-reset runs. mario_acted fires once per anchor period
so non-anchor closures (death/abandon) can judge activity; acted_tracking
on anchors versions the rule per-attempt for old-journal stability."
```

---

### Task 2: Projection — AFK pause discard

**Files:**
- Modify: `src/sm64_events/tracking/projection.py`
- Test: `tests/test_projection.py`

- [ ] **Step 1: Add failing tests** (append to `tests/test_projection.py`):

```python
# -- AFK pause discard (spec §1) ----------------------------------------------

def test_pause_then_reset_discards_closed_attempt():
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "practice_reset", 1600,
            {"igt_frames_before": 380, "mario_acted": True,
             "paused_frames_before": 150}),
        star(4, 1900, igt=95),
    ])
    # the attempt opened at 2 vanished (closed after a >=5 s pause);
    # the anchor at 3 still opened the attempt the grab closes.
    assert [a.outcome for a in attempts] == ["success", "success"]
    assert attempts[1].id == 3


def test_pause_below_threshold_keeps_reset():
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "practice_reset", 1600,
            {"igt_frames_before": 380, "mario_acted": True,
             "paused_frames_before": 149}),
    ])
    assert attempts[1].outcome == "reset"


def test_pause_discard_applies_to_state_loaded_closures():
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "state_loaded", 800,
            {"igt_frames_restored": 120, "mario_acted": True,
             "paused_frames_before": 300}),
        star(4, 1100, igt=95),
    ])
    assert [a.outcome for a in attempts] == ["success", "success"]
    assert attempts[1].id == 3
```

The first test also proves the user's "always discard" decision: `mario_acted` is True (real play happened) and the run is still dropped.

- [ ] **Step 2: Run — verify they fail**

Run: `uv run pytest tests/test_projection.py -q`
Expected: 3 failures (outcomes contain `"reset"` where `"success"` expected / wrong outcome).

- [ ] **Step 3: Implement.** In `src/sm64_events/tracking/projection.py`, add below `ANCHOR_EVENT_TYPES`:

```python
# AFK rule (spec 2026-06-11): a reset arriving after >=5 s of pause (the
# Usamune menu freezes IGT while gGlobalTimer keeps running) closes a run the
# player walked away from — that is AFK, not a practice reset. Discard applies
# even when the attempt had real activity before the pause (user decision).
PAUSE_DISCARD_FRAMES = 150  # 5 s x 30 fps
```

Then change `_close_by_reset` to check the pause FIRST:

```python
    def _close_by_reset(self, ev) -> list[Attempt]:
        if ev.payload.get("paused_frames_before", 0) >= PAUSE_DISCARD_FRAMES:
            # AFK-then-reset: throw the run out (old journals lack the
            # key -> 0 -> kept). The anchor still opens the next attempt.
            self._open = None
            return []
        if not ev.payload.get("mario_acted", True):
            # no-op reset spam: the player never acted, so the closed
            # attempt isn't a real attempt — drop it (anchor still opens
            # the next one). Old journals lack the key -> default True.
            self._open = None
            return []
        igt = ev.payload.get("igt_frames_before") if ev.type == "practice_reset" else None
        return self._close(ev, outcome="reset", igt_frames=igt)
```

Add to the module docstring caveat 5 (after its last sentence):

```
   Additionally, reset-closures with paused_frames_before >=
   PAUSE_DISCARD_FRAMES are dropped the same way: a long Usamune-menu pause
   immediately before the reset means the player went AFK and came back —
   discarded even when the attempt had real activity (user decision).
```

- [ ] **Step 4: Run the file, then the full suite**

Run: `uv run pytest tests/test_projection.py -q` → pass.
Run: `uv run pytest -q` → pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/projection.py tests/test_projection.py
git commit -m "feat: discard AFK pause-then-reset closures (>=5 s menu pause)

A reset right after a long pause means the player went away and came back,
not a failed practice attempt. Threshold is a projection-policy constant;
old journals lack the payload key and rebuild unchanged."
```

---

### Task 3: Projection — no-activity discard for ALL closure types

**Files:**
- Modify: `src/sm64_events/tracking/projection.py`
- Test: `tests/test_projection.py`

Attempts opened by an `acted_tracking` anchor are judged by whether a `mario_acted` event arrived during them; every NON-success closer drops un-acted attempts. Legacy anchors (no marker) keep today's exact semantics.

- [ ] **Step 1: Add failing tests** (append to `tests/test_projection.py`):

```python
# -- activity rule for all closure types (spec §2) ------------------------------

def tracking_anchor(id, frame, igt_before=0):
    """Anchor as the NEW detector emits it (acted_tracking marker)."""
    return jev(id, "practice_reset", frame,
               {"igt_frames_before": igt_before, "mario_acted": False,
                "acted_tracking": True, "paused_frames_before": 0})


def test_unacted_death_is_discarded_for_tracking_anchors():
    attempts = project([
        star(1, 900),
        tracking_anchor(2, 1000),
        jev(3, "death", 1300, {"cause": "quicksand", "igt_frames": 290}),
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_acted_event_keeps_death():
    attempts = project([
        star(1, 900),
        tracking_anchor(2, 1000),
        jev(3, "mario_acted", 1100),
        jev(4, "death", 1300, {"cause": "quicksand", "igt_frames": 290}),
    ])
    assert attempts[1].outcome == "death"
    assert attempts[1].id == 2


def test_unacted_abandon_is_discarded_for_tracking_anchors():
    attempts = project([
        star(1, 900),
        tracking_anchor(2, 1000),
        jev(3, "level_changed", 1600, {"from": 24, "to": 6}),
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_unacted_hard_reset_is_discarded_for_tracking_anchors():
    attempts = project([
        star(1, 900),
        tracking_anchor(2, 1000),
        jev(3, "game_reset", 50),
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_unacted_reset_closure_uses_event_not_closer_payload():
    # closer claims mario_acted True, but the OPENING anchor tracks events
    # and none arrived -> still dropped (event-based rule wins).
    attempts = project([
        star(1, 900),
        tracking_anchor(2, 1000),
        jev(3, "practice_reset", 1400,
            {"igt_frames_before": 380, "mario_acted": True,
             "acted_tracking": True, "paused_frames_before": 0}),
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_success_is_never_discarded():
    attempts = project([
        tracking_anchor(1, 1000),
        star(2, 1350),                       # no mario_acted event, still counts
    ])
    assert attempts[0].outcome == "success"


def test_acted_state_resets_per_attempt():
    attempts = project([
        star(1, 900),
        tracking_anchor(2, 1000),
        jev(3, "mario_acted", 1100),
        jev(4, "death", 1300, {"cause": "standing", "igt_frames": 250}),  # kept
        tracking_anchor(5, 1400),
        jev(6, "death", 1700, {"cause": "standing", "igt_frames": 250}),  # dropped
    ])
    assert [a.outcome for a in attempts] == ["success", "death"]


def test_legacy_anchor_death_closure_is_kept():
    # old journals have no acted_tracking marker and no mario_acted events:
    # death/abandon closures keep today's semantics (always counted).
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(3, "death", 1300, {"cause": "standing", "igt_frames": 290}),
    ])
    assert attempts[1].outcome == "death"
```

- [ ] **Step 2: Run — verify failures**

Run: `uv run pytest tests/test_projection.py -q`
Expected: 6 failures (the unacted-* and event-precedence tests; legacy/success/acted tests may already pass).

- [ ] **Step 3: Implement.** In `src/sm64_events/tracking/projection.py`:

In `Projector.__init__`, after `self._open = None`:

```python
        self._open_acted = False  # mario_acted event seen during the open attempt
```

In `_dispatch`, set the flag on anchor-open and handle the new event. The anchor branch becomes:

```python
        if ev.type in ANCHOR_EVENT_TYPES:
            closed = self._close_by_reset(ev)
            self._open = ev
            self._open_acted = False
            return closed
```

and add ABOVE the `if ev.type == "rollout":` branch (NOT in
`BOUNDARY_EVENT_TYPES` — a `mario_acted` event must not zero the rollout
accumulator):

```python
        if ev.type == "mario_acted":
            self._open_acted = True
            return []
```

Add a helper after `_dispatch`:

```python
    def _unacted_open(self) -> bool:
        """No-behavior rule (spec §2): the open attempt came from an
        acted-tracking anchor and no mario_acted event arrived during it.
        Legacy anchors (no marker) never match — old journals keep their
        original semantics."""
        return (self._open is not None
                and self._open.payload.get("acted_tracking", False)
                and not self._open_acted)
```

In `_close_by_reset`, replace the activity check (the `if not ev.payload.get("mario_acted", True):` line) with:

```python
        if self._unacted_open() or not ev.payload.get("mario_acted", True):
```

(comment stays; the OR keeps legacy closer-payload semantics for old journals).

In `_close_by_death`, add at the very top:

```python
        if self._unacted_open():
            self._open = None
            return []
```

(deaths WITHOUT an anchor have `self._open is None` → helper returns False →
the existing "a death is always meaningful" synthesis stands).

In `_close`, after the `if self._open is None:` early return:

```python
        if self._unacted_open():
            self._open = None
            return []
```

Update module docstring caveat 5 — append:

```
   For attempts opened by an acted_tracking anchor the judgment is
   event-based (a mario_acted journal event during the attempt) and applies
   to EVERY non-success closure: reset, death, abandoned, hard_reset.
   Successes always count.
```

- [ ] **Step 4: Run the file, then the full suite**

Run: `uv run pytest tests/test_projection.py -q` → pass.
Run: `uv run pytest -q` → pass. (`tests/test_tracker_service.py` publishes legacy-style payloads → unaffected.)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/projection.py tests/test_projection.py
git commit -m "feat: drop no-behavior attempts on every closure type

Death/abandon/hard-reset closures of attempts where Mario never acted are
garbage, same as no-op resets (user rule). Judgment is event-based and
gated on the opening anchor's acted_tracking marker so pre-feature
journals replay byte-identical."
```

---

### Task 4: Timeline markers — storage, API, view payload

**Files:**
- Modify: `src/sm64_events/server/api.py`, `src/sm64_events/tracking/views.py`
- Test: `tests/test_api.py`, `tests/test_views.py`

- [ ] **Step 1: Add failing API tests** (append to `tests/test_api.py`):

```python
# -- timeline markers ----------------------------------------------------------

def test_markers_roundtrip_sorted_by_frames(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        r = client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": "cannonless",
            "markers": [{"frames": 600, "label": "pyramid warp"},
                        {"frames": 90, "label": "bobomb grab"}]})
        assert r.status_code == 200 and r.json()["ok"] is True
        sec = client.get("/api/session").json()["stars"][0]
        assert sec["markers_by_strat"]["cannonless"] == [
            {"frames": 90, "label": "bobomb grab"},
            {"frames": 600, "label": "pyramid warp"}]


def test_markers_null_strat_lands_in_empty_key(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None,
            "markers": [{"frames": 90, "label": "bobomb grab"}]})
        sec = client.get("/api/session").json()["stars"][0]
        assert sec["markers_by_strat"][""] == [{"frames": 90, "label": "bobomb grab"}]


def test_markers_empty_list_clears(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None,
            "markers": [{"frames": 90, "label": "x"}]})
        client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None, "markers": []})
        sec = client.get("/api/session").json()["stars"][0]
        assert sec["markers_by_strat"][""] == []


def test_markers_validation_422s(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        for bad in ({"frames": -1, "label": "x"},
                    {"frames": 0, "label": ""},
                    {"frames": 0, "label": "   "},
                    {"frames": 0, "label": "y" * 61}):
            r = client.put("/api/markers", json={
                "course_id": 2, "star_id": 2, "strat_tag": None,
                "markers": [bad]})
            assert r.status_code == 422, bad
        too_many = [{"frames": i, "label": f"m{i}"} for i in range(31)]
        assert client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None,
            "markers": too_many}).status_code == 422


def test_markers_label_is_trimmed(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None,
            "markers": [{"frames": 90, "label": "  bobomb grab  "}]})
        sec = client.get("/api/session").json()["stars"][0]
        assert sec["markers_by_strat"][""][0]["label"] == "bobomb grab"
```

Also extend the existing `test_degraded_service_returns_503` — add before its
last assertion:

```python
        assert client.put("/api/markers", json={
            "course_id": 2, "star_id": 2, "strat_tag": None,
            "markers": []}).status_code == 503
```

- [ ] **Step 2: Add a failing view test** (append to `tests/test_views.py`):

```python
# -- timeline markers in the view (spec §3) -------------------------------------

def test_section_carries_markers_by_strat(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    db.set_state("timeline_markers", {
        "2:2:": [{"frames": 90, "label": "wall jump"}],
        "2:2:cannonless": [{"frames": 200, "label": "owl"}],
        "8:1:": [{"frames": 50, "label": "other star — excluded"}],
    })
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert sec["markers_by_strat"] == {
        "": [{"frames": 90, "label": "wall jump"}],
        "cannonless": [{"frames": 200, "label": "owl"}],
    }


def test_markers_default_empty(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    view = build_session_view(db, svc, clock="igt")
    assert view["stars"][0]["markers_by_strat"] == {}
```

- [ ] **Step 3: Run — verify failures**

Run: `uv run pytest tests/test_api.py tests/test_views.py -q`
Expected: failures — 404 on `/api/markers` (route missing), KeyError `markers_by_strat`.

- [ ] **Step 4: Implement the view side.** In `src/sm64_events/tracking/views.py`, add after `_strategies_for`:

```python
def _markers_for(markers_state: dict, course_id: int, star_id: int) -> dict:
    """strat -> sorted marker list for ONE star, from the ui_state KV
    (key shape '<course>:<star>:<strat>', '' = no strategy)."""
    prefix = f"{course_id}:{star_id}:"
    return {k[len(prefix):]: v for k, v in markers_state.items()
            if k.startswith(prefix)}
```

In `build_session_view`, after `registered = db.get_state("strategies", {})`:

```python
    markers_state = db.get_state("timeline_markers", {})
```

and in the `sections.append({...})` dict, after the `"timeline":` entry:

```python
            "markers_by_strat": _markers_for(markers_state, course_id, star_id),
```

- [ ] **Step 5: Implement the API side.** In `src/sm64_events/server/api.py`, change the pydantic import to:

```python
from pydantic import BaseModel, Field, field_validator
```

Add models after `StatMenuBody`:

```python
class Marker(BaseModel):
    frames: int = Field(ge=0)
    label: str

    @field_validator("label")
    @classmethod
    def _trim_label(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 60:
            raise ValueError("label must be 1-60 chars after trimming")
        return v


class MarkersBody(BaseModel):
    course_id: int
    star_id: int
    strat_tag: str | None = None
    markers: list[Marker] = Field(max_length=30)
```

Add the endpoint after `put_statmenu` (same direct-`db` pattern):

```python
    @router.put("/markers")
    def put_markers(body: MarkersBody):
        """Replace the marker list for one star+strategy (spec §3)."""
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        key = f"{body.course_id}:{body.star_id}:{body.strat_tag or ''}"
        state = service.db.get_state("timeline_markers", {})
        state[key] = sorted(
            ({"frames": m.frames, "label": m.label} for m in body.markers),
            key=lambda m: m["frames"])
        service.db.set_state("timeline_markers", state)
        return {"ok": True}
```

- [ ] **Step 6: Run both files, then the full suite**

Run: `uv run pytest tests/test_api.py tests/test_views.py -q` → pass.
Run: `uv run pytest -q` → pass.

- [ ] **Step 7: Commit**

```bash
git add src/sm64_events/server/api.py src/sm64_events/tracking/views.py tests/test_api.py tests/test_views.py
git commit -m "feat: per-star per-strategy timeline markers (ui_state + PUT /api/markers)

Markers are user annotations, not gameplay events — they live in the same
ui_state KV as strategies/stat_menu (no migration). Replace-the-list PUT
keeps the API id-free; the view ships all strats per star so the UI can
switch without refetching."
```

---

### Task 5: Progress payload in the session view

**Files:**
- Modify: `src/sm64_events/tracking/views.py`
- Test: `tests/test_views.py`

- [ ] **Step 1: Add failing tests** (append to `tests/test_views.py`):

```python
# -- progress graph payload (spec §4) -------------------------------------------

def test_progress_groups_successes_by_session_with_pb_flags(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)                                   # session 1: igt 343 + igt 350
    aid = next(a.id for a in db.attempts() if a.igt_frames == 343)
    asyncio.run(svc.save_pb(aid, "igt"))
    asyncio.run(svc.new_session())
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(5400, igt=330)))   # session 2
    view = build_session_view(db, svc, clock="igt", scope="lifetime")
    [sec] = view["stars"]
    prog = sec["progress"]
    assert [s["session_id"] for s in prog["sessions"]] == [1, 2]
    s1 = prog["sessions"][0]
    assert [p["igt_frames"] for p in s1["points"]] == [343, 350]
    assert [p["is_pb_igt"] for p in s1["points"]] == [True, False]
    assert all(p["is_pb_rta"] is False for p in s1["points"])
    p = s1["points"][0]
    assert p["igt"] == "0'11\"43" and p["attempt_id"] == aid
    assert p["t_utc"]            # close timestamp present
    assert s1["started_utc"]     # session metadata present


def test_progress_session_scope_limits_to_current_session(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    asyncio.run(svc.new_session())
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(5400, igt=330)))
    view = build_session_view(db, svc, clock="igt", scope="session")
    [sec] = view["stars"]
    assert [s["session_id"] for s in sec["progress"]["sessions"]] == [2]


def test_progress_excludes_cleared_and_is_none_without_successes(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))
    aid = db.attempts()[0].id
    asyncio.run(svc.clear_attempt(aid, reason="accidental"))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert sec["progress"] is None


def test_progress_superseded_pbs_stay_gold(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    a343 = next(a.id for a in db.attempts() if a.igt_frames == 343)
    a350 = next(a.id for a in db.attempts() if a.igt_frames == 350)
    asyncio.run(svc.save_pb(a350, "igt"))
    asyncio.run(svc.save_pb(a343, "igt"))     # supersedes a350 as current PB
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    flags = {p["attempt_id"]: p["is_pb_igt"]
             for p in sec["progress"]["sessions"][0]["points"]}
    assert flags[a343] is True and flags[a350] is True   # every saved PB is gold
```

- [ ] **Step 2: Run — verify failures** (`KeyError: 'progress'`)

Run: `uv run pytest tests/test_views.py -q`

- [ ] **Step 3: Implement.** In `src/sm64_events/tracking/views.py`:

Change `_current_pbs` to take rows instead of the db (callers updated below):

```python
def _current_pbs(pb_rows: list[dict]) -> dict:
    """(course, star, mode) -> latest pb row."""
    out = {}
    for row in pb_rows:  # ordered by id: later rows win
        out[(row["course_id"], row["star_id"], row["timer_mode"])] = row
    return out
```

Add after `_markers_for`:

```python
def _progress(attempts, pb_rows, session_meta) -> dict | None:
    """Completion-time-over-time points (spec §4): non-cleared successes of
    the SCOPED attempt list, grouped by session, chronological. Gold =
    explicitly saved PB rows (every save stays gold even when superseded).
    rta race rows (rta_frames == 0) ship as-is; the UI filters them."""
    pb_ids = {(r["attempt_id"], r["timer_mode"]) for r in pb_rows}
    by_session: dict[int, list] = {}
    for a in attempts:
        if a.outcome != "success" or a.cleared:
            continue
        by_session.setdefault(a.session_id, []).append({
            "t_utc": a.ended_utc,
            "igt_frames": a.igt_frames,
            "rta_frames": a.rta_frames,
            "igt": format_igt(a.igt_frames) if a.igt_frames is not None else None,
            "rta": format_igt(a.rta_frames) if a.rta_frames is not None else None,
            "attempt_id": a.id,
            "is_pb_igt": (a.id, "igt") in pb_ids,
            "is_pb_rta": (a.id, "rta") in pb_ids,
        })
    if not by_session:
        return None
    return {"sessions": [
        {"session_id": sid,
         "label": session_meta.get(sid, {}).get("label"),
         "started_utc": session_meta.get(sid, {}).get("started_utc"),
         "points": pts}
        for sid, pts in sorted(by_session.items())]}
```

In `build_session_view`, replace `pbs = _current_pbs(db)` with:

```python
    pb_rows = db.pbs()
    pbs = _current_pbs(pb_rows)
    sessions_list = db.sessions()
    session_meta = {s["id"]: s for s in sessions_list}
```

in the section dict, after `"markers_by_strat":`:

```python
            "progress": _progress(in_section, pb_rows, session_meta),
```

(`in_section` is already exactly the scoped list for this star — current
session under `scope="session"`, full history under `scope="lifetime"`.)

and in the return dict replace `"sessions": db.sessions(),` with:

```python
        "sessions": sessions_list,
```

- [ ] **Step 4: Run the file, then the full suite**

Run: `uv run pytest tests/test_views.py -q` → pass. `uv run pytest -q` → pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/views.py tests/test_views.py
git commit -m "feat: per-star progress payload (completion time over time)

Successes grouped by session with is_pb flags per clock so the UI can draw
gold PB points; every explicit Save-as-PB stays gold even after being
superseded — the graph answers 'when did I get each PB'."
```

---

### Task 6: Section ordering + target-section guarantee

**Files:**
- Modify: `src/sm64_events/tracking/views.py`
- Test: `tests/test_views.py` (two new tests + ONE existing test updated)

- [ ] **Step 1: Add failing tests** (append to `tests/test_views.py`):

```python
# -- section ordering + pinned target (spec §5) ---------------------------------

def test_sections_ordered_newest_activity_first(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, course=2, star_id=2)))
    asyncio.run(svc.publish(ev("practice_reset", 2000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(2400, course=8, star_id=1, igt=500)))
    view = build_session_view(db, svc, clock="igt")
    assert [(s["course_id"], s["star_id"]) for s in view["stars"]] \
        == [(8, 1), (2, 2)]
    # fresh activity on (2,2) moves it back to the top
    asyncio.run(svc.publish(ev("practice_reset", 3000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(3400, course=2, star_id=2)))
    view2 = build_session_view(db, svc, clock="igt")
    assert [(s["course_id"], s["star_id"]) for s in view2["stars"]] \
        == [(2, 2), (8, 1)]


def test_target_star_section_always_present(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(8, 2))           # no attempts anywhere
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert (sec["course_id"], sec["star_id"]) == (8, 2)
    assert sec["attempts"] == [] and sec["timeline"] is None
    assert sec["progress"] is None
```

- [ ] **Step 2: Update ONE existing test whose premise changes.** In
`tests/test_views.py`, `test_timeline_none_when_only_abandoned` ends with
`assert view["stars"] == []`. The target star's section is now always present
(the 1350 grab set the target to (2,2)). Replace that final assertion with:

```python
    # target (2,2) section is now always present (pinned active star);
    # nothing in the current session, so its attempt list is empty.
    [sec] = view["stars"]
    assert sec["attempts"] == []
```

(`test_failures_before_any_grab_land_in_unassigned` still expects `stars ==
[]` — correct, no target is ever set there.)

- [ ] **Step 3: Run — verify failures**

Run: `uv run pytest tests/test_views.py -q`
Expected: ordering test fails (current order is oldest-first), target test fails (`view["stars"]` empty), updated test fails until implementation.

- [ ] **Step 4: Implement.** In `build_session_view`, after the `for a in scoped:` loop that fills `seen`/`unassigned`, add:

```python
    # the target star always gets a section (spec §5): setting a target
    # immediately surfaces its lifetime history, PB, and markers.
    if service.target and service.target not in seen:
        seen[service.target] = None
```

After the `for course_id, star_id in seen:` loop finishes building
`sections`, add (before the `tgt_c, tgt_s = ...` line):

```python
    # newest activity first; scoped is journal-id-ordered so the last
    # assignment per star is its max attempt id. Fresh targets (-1) sort last.
    last_id: dict[tuple[int, int], int] = {}
    for a in scoped:
        if a.course_id is not None:
            last_id[(a.course_id, a.star_id)] = a.id
    sections.sort(key=lambda s: last_id.get((s["course_id"], s["star_id"]), -1),
                  reverse=True)
```

- [ ] **Step 5: Run the file, then the full suite**

Run: `uv run pytest tests/test_views.py -q` → pass. `uv run pytest -q` → pass.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/tracking/views.py tests/test_views.py
git commit -m "feat: order star sections by recency and always include the target

Newest activity floats to the top (spec: newest first, oldest bottom) and
setting a target immediately materializes its section so the pinned
active-star block has data before the first attempt."
```

---

### Task 7: UI — timeline annotation markers

**Files:**
- Modify: `src/sm64_events/ui/components/timeline.js` (full rewrite below)
- Modify: `src/sm64_events/ui/components/practice.js` (call site only)

No JS test harness exists in this repo; UI tasks are verified by the live
smoke test in Task 10. Keep the server running (`uv run uvicorn
sm64_events.main:app --host 127.0.0.1 --port 8064`) and refresh
`http://127.0.0.1:8064` after each edit — the UI is served per request.

- [ ] **Step 1: Replace `src/sm64_events/ui/components/timeline.js` with:**

```js
// src/sm64_events/ui/components/timeline.js
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";

const html = htm.bind(h);

// Marker styles per outcome. Extending the graph = one row here plus one
// row in TIMELINE_OUTCOMES (tracking/views.py).
const MARKERS = {
  success: { color: "#a3e0a3" },
  reset: { color: "#e0a3a3" },
  death: { color: "#d96a6a" },
};
const ANNOT = "#8ab4f8"; // strategy annotation flags (spec §3)

const W = 600, H = 28, PAD = 8, MID = H / 2, BAND = 16; // BAND: label strip above

function fmtIgt(frames) {
  const m = Math.floor(frames / 1800), s = Math.floor((frames % 1800) / 30),
        c = Math.floor(((frames % 30) * 100) / 30);
  return `${m}'${String(s).padStart(2, "0")}"${String(c).padStart(2, "0")}`;
}

// "3" / "3.5" (seconds) or 0'03"50 (IGT) -> frames at 30 fps; null = unparseable
export function parseTimeInput(text) {
  const igt = String(text).trim().match(/^(\d+)'(\d{1,2})"(\d{1,2})$/);
  if (igt) return (+igt[1] * 60 + +igt[2]) * 30 + Math.round((+igt[3] * 30) / 100);
  const secs = Number(String(text).trim());
  return Number.isFinite(secs) && secs >= 0 ? Math.round(secs * 30) : null;
}

function Marker({ p, x }) {
  const m = MARKERS[p.outcome] || { color: "#888" };
  const label = html`<title>${p.outcome} · ${p.igt}</title>`;
  if (p.outcome === "success") {
    return html`<circle cx=${x} cy=${MID} r="4.5" fill=${m.color}>${label}</circle>`;
  }
  if (p.outcome === "death") {
    return html`<g stroke=${m.color} stroke-width="1.6">
      <line x1=${x - 3.5} y1=${MID - 3.5} x2=${x + 3.5} y2=${MID + 3.5} />
      <line x1=${x - 3.5} y1=${MID + 3.5} x2=${x + 3.5} y2=${MID - 3.5} />${label}</g>`;
  }
  return html`<line x1=${x} y1=${MID - 5} x2=${x} y2=${MID + 5}
                    stroke=${m.color} stroke-width="1.6">${label}</line>`;
}

// tl: attempt-point payload (may be null before any attempts);
// sec: the star section (course/star ids, last_strat, markers_by_strat);
// t: the tracker store (refresh after PUT).
export function Timeline({ tl, sec, t }) {
  const strat = sec.last_strat || "";
  const markers = (sec.markers_by_strat || {})[strat] || [];
  const [form, setForm] = useState(null); // {time, label} while the editor is open
  const points = tl ? tl.points : [];
  const showStrip = points.length > 0 || markers.length > 0;

  const axisMax = Math.max(tl ? tl.max_frames : 0,
    ...points.map((p) => p.frames), ...markers.map((m) => m.frames)) || 1;
  const x = (f) => PAD + (f / axisMax) * (W - 2 * PAD);

  async function save(list) {
    await send("PUT", "/api/markers", {
      course_id: sec.course_id, star_id: sec.star_id,
      strat_tag: sec.last_strat || null,
      markers: list.map(({ frames, label }) => ({ frames, label })),
    });
    setForm(null);
    t.refresh();
  }
  function addFromForm() {
    const frames = parseTimeInput(form.time);
    const label = (form.label || "").trim();
    if (frames === null || !label) return;
    save([...markers, { frames, label }]);
  }
  function clickToPlace(e) {
    // click anywhere on the strip -> open the editor prefilled at that IGT
    const rect = e.currentTarget.getBoundingClientRect();
    const frac = (e.clientX - rect.left) / rect.width;
    const f = Math.round(Math.max(0, Math.min(1, (frac * W - PAD) / (W - 2 * PAD))) * axisMax);
    setForm({ time: (f / 30).toFixed(2), label: form ? form.label : "" });
  }

  const TOT = H + BAND;
  return html`<div style="margin:.3rem 0">
    ${showStrip && html`<div>
      <svg viewBox="0 0 ${W} ${TOT}" style="width:100%;height:${TOT}px;display:block;cursor:crosshair"
           onclick=${clickToPlace}>
        ${markers.map((m) => html`<g>
          <text x=${x(m.frames)} y="10" fill=${ANNOT} font-size="9"
                text-anchor="middle">${m.label}</text>
          <line x1=${x(m.frames)} y1="13" x2=${x(m.frames)} y2=${BAND + H - 4}
                stroke=${ANNOT} stroke-width="1.2" stroke-dasharray="3,2">
            <title>${m.label} · ${fmtIgt(m.frames)}</title></line></g>`)}
        <g transform="translate(0 ${BAND})">
          <line x1=${PAD} y1=${MID} x2=${W - PAD} y2=${MID} stroke="#3a4150" />
          ${tl && tl.max_is_success && html`<line x1=${x(tl.max_frames)} y1=${MID - 7}
              x2=${x(tl.max_frames)} y2=${MID + 7} stroke="#3a4150"
              stroke-dasharray="2,2"><title>longest success · ${tl.max_display}</title></line>`}
          ${points.map((p) => html`<${Marker} p=${p} x=${x(p.frames)} />`)}
        </g>
      </svg>
      <div class="meta" style="display:flex;justify-content:space-between">
        <span>0'00"00</span>
        <span>${tl ? `${tl.max_is_success ? "" : "~"}${tl.max_display}` : fmtIgt(axisMax)}</span>
      </div>
    </div>`}
    <div class="chips">
      ${markers.map((m, i) => html`<span class="chip" style="color:${ANNOT}">
        ${fmtIgt(m.frames)} ${m.label}
        <span style="cursor:pointer;opacity:.6" title="delete marker"
              onclick=${() => save(markers.filter((_, j) => j !== i))}> ×</span></span>`)}
      ${form
        ? html`<span class="chip">
            <input size="8" placeholder='3 or 0&apos;03"00' value=${form.time}
                   oninput=${(e) => setForm({ ...form, time: e.target.value })} />
            <input size="14" placeholder="label" value=${form.label}
                   oninput=${(e) => setForm({ ...form, label: e.target.value })} />
            <button onclick=${addFromForm}>add</button>
            <button onclick=${() => setForm(null)}>cancel</button></span>`
        : html`<span class="chip" style="cursor:pointer;border-style:dashed"
              onclick=${() => setForm({ time: "", label: "" })}>+ marker</span>`}
    </div>
  </div>`;
}
```

- [ ] **Step 2: Update the call site.** In `src/sm64_events/ui/components/practice.js`, replace:

```js
    <${Timeline} tl=${sec.timeline} />
```

with:

```js
    <${Timeline} tl=${sec.timeline} sec=${sec} t=${t} />
```

- [ ] **Step 3: Manual check** — with the server running and at least one star
section visible: markers chip row renders, "+ marker" opens the form, adding
`3` + `bobomb grab` creates a blue dashed flag at 0'03"00, clicking the strip
prefills the time, × deletes. Check the browser console for errors.

- [ ] **Step 4: Run the backend suite (guards against accidental backend edits)**

Run: `uv run pytest -q` → pass.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/components/timeline.js src/sm64_events/ui/components/practice.js
git commit -m "feat: timeline annotation markers UI (click-to-place + exact-entry chips)

Markers render as dashed flags above the attempt strip for the section's
active strategy; both add flows the user chose are supported and every
change PUTs the full list (replace semantics, no per-marker ids)."
```

---

### Task 8: UI — progress graph component

**Files:**
- Create: `src/sm64_events/ui/components/progress.js`
- Modify: `src/sm64_events/ui/components/practice.js` (import + render)

- [ ] **Step 1: Create `src/sm64_events/ui/components/progress.js`:**

```js
// src/sm64_events/ui/components/progress.js — completion time over time
// (spec §4). One segment per session, ⫽ breaks between segments (lifetime);
// gold = explicitly saved PBs for the current clock. Y: faster = lower.
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

const W = 600, H = 170, PADL = 56, PADR = 10, PADT = 12, PADB = 26, GAP = 18;
const GOLD = "#e0c36a", GOLD_RIM = "#f5e2a8", GREEN = "#a3e0a3",
      GRID = "#262c38", AXIS = "#3a4150", TXT = "#6c7686";

function fmtIgt(frames) {
  const m = Math.floor(frames / 1800), s = Math.floor((frames % 1800) / 30),
        c = Math.floor(((frames % 30) * 100) / 30);
  return `${m}'${String(s).padStart(2, "0")}"${String(c).padStart(2, "0")}`;
}

// Local-timezone tick label; MM/DD/YY prefix when the graph spans >1 day.
function fmtTick(iso, withDate) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return withDate
    ? `${d.toLocaleDateString([], { month: "2-digit", day: "2-digit", year: "2-digit" })} ${time}`
    : time;
}

export function Progress({ prog, clock }) {
  if (!prog) return "";
  const fKey = clock === "igt" ? "igt_frames" : "rta_frames";
  const pbKey = clock === "igt" ? "is_pb_igt" : "is_pb_rta";
  // frames > 0 drops same-tick race rows (rta=0 junk; see projection.py)
  const segs = prog.sessions
    .map((s) => ({ ...s, points: s.points.filter((p) => p[fKey] != null && p[fKey] > 0) }))
    .filter((s) => s.points.length > 0);
  if (!segs.length) return "";

  const all = segs.flatMap((s) => s.points.map((p) => p[fKey]));
  let lo = Math.min(...all), hi = Math.max(...all);
  const span = Math.max(hi - lo, 30);
  lo = Math.max(0, lo - span * 0.15);
  hi = hi + span * 0.15;
  const y = (f) => PADT + ((hi - f) / (hi - lo)) * (H - PADT - PADB);

  const stamps = segs.flatMap((s) => s.points.map((p) => Date.parse(p.t_utc)));
  const withDate = new Date(Math.min(...stamps)).toDateString()
    !== new Date(Math.max(...stamps)).toDateString();

  // segment widths proportional to point count; within a segment, x is
  // linear wall-clock time for that session
  const innerW = W - PADL - PADR - GAP * (segs.length - 1);
  const total = all.length;
  let cursor = PADL;
  const placed = segs.map((s) => {
    const w = Math.max(innerW * (s.points.length / total), 24);
    const t0 = Date.parse(s.points[0].t_utc);
    const t1 = Date.parse(s.points[s.points.length - 1].t_utc);
    const left = cursor;
    const xs = s.points.map((p) => t1 > t0
      ? left + 8 + ((Date.parse(p.t_utc) - t0) / (t1 - t0)) * (w - 16)
      : left + w / 2);
    cursor += w + GAP;
    return { ...s, left, w, xs };
  });

  const mid = (lo + hi) / 2;
  const last = placed[placed.length - 1];
  return html`<div style="margin:.3rem 0">
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;display:block">
      ${[hi, mid, lo].map((v, i) => html`<g>
        <line x1=${PADL} y1=${y(v)} x2=${W - PADR} y2=${y(v)}
              stroke=${i === 2 ? AXIS : GRID} />
        <text x=${PADL - 6} y=${y(v) + 3} fill=${TXT} font-size="9"
              text-anchor="end">${fmtIgt(Math.round(v))}</text></g>`)}
      ${placed.map((s, i) => html`<g>
        ${i > 0 && html`<g stroke=${AXIS} stroke-width="1.4">
          <line x1=${s.left - GAP + 4} y1=${y(lo) - 6} x2=${s.left - GAP + 10} y2=${y(lo) + 6} />
          <line x1=${s.left - GAP + 9} y1=${y(lo) - 6} x2=${s.left - GAP + 15} y2=${y(lo) + 6} /></g>`}
        <polyline fill="none" stroke=${AXIS} stroke-width="1.2"
          points=${s.points.map((p, j) => `${s.xs[j]},${y(p[fKey])}`).join(" ")} />
        ${s.points.map((p, j) => html`<circle cx=${s.xs[j]} cy=${y(p[fKey])}
            r=${p[pbKey] ? 5 : 4.5} fill=${p[pbKey] ? GOLD : GREEN}
            stroke=${p[pbKey] ? GOLD_RIM : "none"} stroke-width="1">
          <title>${p[pbKey] ? "PB " : ""}${clock === "igt" ? p.igt : p.rta} · ${fmtTick(p.t_utc, true)}</title>
        </circle>`)}
        <text x=${s.left + s.w / 2} y=${H - 8} fill=${TXT} font-size="9"
              text-anchor="middle">${fmtTick(s.points[0].t_utc, withDate)}</text>
      </g>`)}
      ${placed.length === 1 && last.points.length > 1 && html`<text
          x=${W - PADR} y=${H - 8} fill=${TXT} font-size="9" text-anchor="end"
        >${fmtTick(last.points[last.points.length - 1].t_utc, withDate)}</text>`}
    </svg>
  </div>`;
}
```

- [ ] **Step 2: Wire it into `practice.js`.** Add the import after the
`Timeline` import:

```js
import { Progress } from "./progress.js";
```

and render it in `StarSection`, directly under the `Timeline` line:

```js
    <${Progress} prog=${sec.progress} clock=${t.clock} />
```

- [ ] **Step 3: Manual check** — grab a star (or use an existing db with
successes): a scatter appears under the timeline; saved-PB points are gold
with a bright rim; tooltips show time + local timestamp; toggling clock
igt/rta re-renders. Console clean.

- [ ] **Step 4: Run the backend suite, commit**

Run: `uv run pytest -q` → pass.

```bash
git add src/sm64_events/ui/components/progress.js src/sm64_events/ui/components/practice.js
git commit -m "feat: progress graph — completion time over the session, gold PB points

Session-segmented x axis (user choice B): every session stays readable
regardless of calendar gaps; UTC stays in storage and the browser renders
local time with MM/DD/YY prefixes once a graph spans days."
```

---

### Task 9: UI — pinned active star + global sort/hide-resets bar

**Files:**
- Modify: `src/sm64_events/ui/components/practice.js` (full rewrite below)
- Modify: `src/sm64_events/ui/index.html` (CSS)

- [ ] **Step 1: Add CSS.** In `src/sm64_events/ui/index.html`, after the
`.starsec { ... }` rule, add:

```css
  .active-star { border-color: #e0c36a; }
  .active-tag { color: #e0c36a; font-size: .7em; letter-spacing: 1px; margin-bottom: .2rem; }
  .listhead { letter-spacing: 1px; text-transform: uppercase; font-size: .7em; margin: .2rem 0 .4rem; }
```

- [ ] **Step 2: Replace `src/sm64_events/ui/components/practice.js` with**
(this is the final integrated version — it includes the Task 7/8 call sites):

```js
// src/sm64_events/ui/components/practice.js
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { StatMenu } from "./statmenu.js";
import { Timeline } from "./timeline.js";
import { Progress } from "./progress.js";

const html = htm.bind(h);

const OUTCOME_LABEL = { success: "✔", reset: "✘ reset",
  hard_reset: "✘ hard reset", abandoned: "– abandoned", death: "✘ death" };

const SORT_OPTIONS = [
  ["newest", "newest first"], ["oldest", "oldest first"],
  ["fastest", "fastest first"], ["slowest", "slowest first"]];

// Row time on the current clock: completion time for successes, how-far-in
// for failures. Nulls sort last in both directions.
function rowTime(a, clock) {
  return clock === "igt" ? a.igt_frames : a.rta_frames;
}
function comparator(sort, clock) {
  if (sort === "oldest") return (a, b) => a.id - b.id;
  if (sort === "fastest")
    return (a, b) => (rowTime(a, clock) ?? Infinity) - (rowTime(b, clock) ?? Infinity);
  if (sort === "slowest")
    return (a, b) => (rowTime(b, clock) ?? -Infinity) - (rowTime(a, clock) ?? -Infinity);
  return (a, b) => b.id - a.id; // newest (default)
}

function delta(frames) {
  if (frames === null || frames === undefined) return "";
  const cls = frames > 0 ? "delta-up" : "delta-down";
  const sign = frames > 0 ? "+" : "";
  return html` <span class=${cls}>${sign}${(frames / 30).toFixed(2)}s vs PB</span>`;
}

function AttemptRow({ a, t, idx }) {
  async function clear() {
    await send("POST", `/api/attempts/${a.id}/clear`, { reason: "accidental" });
    t.refresh();
  }
  async function restore() {
    await send("POST", `/api/attempts/${a.id}/restore`);
    t.refresh();
  }
  async function savePb() {
    await send("POST", "/api/pb", { attempt_id: a.id, timer_mode: t.clock });
    t.refresh();
  }
  const time = t.clock === "igt" ? a.igt : a.rta;
  const frames = t.clock === "igt" ? a.igt_frames : a.rta_frames;
  // Glow when saving would set a new PB: beats the recorded PB, or no PB
  // exists yet. frames > 0 excludes same-tick race rows (rta=0 junk) whose
  // "PB" would be meaningless.
  const pbBeat = a.outcome === "success" && !a.cleared
    && frames != null && frames > 0
    && (a.pb_delta_frames === null || a.pb_delta_frames < 0);
  return html`<tr class=${a.cleared ? "cleared" : ""}>
    <td class="meta">#${idx + 1}</td>
    <td class=${a.outcome === "success" ? "good" : "badx"}>
      ${OUTCOME_LABEL[a.outcome] || a.outcome}
      ${a.outcome === "death" && a.outcome_detail
        ? html` <span class="meta">(${a.outcome_detail})</span>` : ""}
      ${a.outcome === "success" && time ? html` <b>${time}</b>` : ""}
      ${a.outcome !== "success" && a.igt ? html` <span class="meta">${a.igt} in</span>` : ""}
    </td>
    <td>${a.outcome === "success" ? delta(a.pb_delta_frames) : ""}</td>
    <td class="meta">${a.strat_tag || ""}</td>
    <td style="text-align:right">
      ${a.outcome === "success" && !a.cleared
        ? html`<button class=${pbBeat ? "pb-glow" : ""} onclick=${savePb}>Save as PB</button> ` : ""}
      ${a.cleared
        ? html`<button onclick=${restore}>undo</button>`
        : html`<button onclick=${clear} title="clear (mistake)">×</button>`}
    </td>
  </tr>`;
}

// Shared table component used by both StarSection and the unassigned block.
// attempts: the full ordered list for stable numbering;
// rows: the filtered/sorted subset to actually render.
function AttemptTable({ attempts, rows, t }) {
  return html`<table>
    ${rows.map((a) => {
      const idx = attempts.indexOf(a);
      return html`<${AttemptRow} a=${a} t=${t} idx=${idx} />`;
    })}
  </table>`;
}

function HideToggle({ hidden, showHidden, setShowHidden }) {
  if (hidden.length === 0) return null;
  return html`<button class="meta"
      style="background:none;border:none;cursor:pointer"
      onclick=${() => setShowHidden(!showHidden)}>
    ${showHidden ? "hide" : "show"} ${hidden.length} hidden
  </button>`;
}

function StarSection({ sec, t, ui, pinned }) {
  const [showHidden, setShowHidden] = useState(false);
  const pb = sec.pb[t.clock];
  const base = showHidden ? sec.attempts
    : sec.attempts.filter((a) => !a.cleared && a.outcome !== "abandoned");
  const hidden = sec.attempts.filter((a) => a.cleared || a.outcome === "abandoned");
  const rows = base
    .filter((a) => !(ui.hideResets
      && (a.outcome === "reset" || a.outcome === "hard_reset")))
    .slice()
    .sort(comparator(ui.sort, t.clock));
  return html`<div class="starsec ${pinned ? "active-star" : ""}">
    ${pinned && html`<div class="active-tag">★ ACTIVE STAR</div>`}
    <div class="shead">
      <b>${sec.course_name} · ${sec.star_name}</b>
      <a href=${sec.links.ukikipedia} target="_blank">RTA Guide</a>
      ${sec.links.example && html`<a href=${sec.links.example} target="_blank">Example</a>`}
      <span class="pbtag">${pb ? `PB ${pb.display} (${t.clock})` : "no PB yet"}</span>
    </div>
    <${Timeline} tl=${sec.timeline} sec=${sec} t=${t} />
    <${Progress} prog=${sec.progress} clock=${t.clock} />
    <${AttemptTable} attempts=${sec.attempts} rows=${rows} t=${t} />
    <${HideToggle} hidden=${hidden} showHidden=${showHidden} setShowHidden=${setShowHidden} />
    <div class="chips">
      ${sec.stats.map((s) => html`
        <span class="chip" title=${s.key}>${s.label} ${s.display ?? "–"}</span>`)}
    </div>
  </div>`;
}

function ControlBar({ ui }) {
  return html`<div class="bar">
    <label class="meta">sort${" "}
      <select value=${ui.sort} onchange=${(e) => ui.setSort(e.target.value)}>
        ${SORT_OPTIONS.map(([k, label]) => html`<option value=${k}>${label}</option>`)}
      </select></label>
    <label class="meta" style="cursor:pointer">
      <input type="checkbox" checked=${ui.hideResets}
             onchange=${(e) => ui.setHideResets(e.target.checked)} />
      ${" "}hide resets <span class="meta">(stats unaffected)</span></label>
  </div>`;
}

export function Practice({ t }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [showUnassignedHidden, setShowUnassignedHidden] = useState(false);
  const [sort, setSortState] = useState(localStorage.getItem("sm64.sort") || "newest");
  const [hideResets, setHideResetsState] = useState(
    localStorage.getItem("sm64.hideResets") === "1");
  const ui = {
    sort, hideResets,
    setSort: (v) => { localStorage.setItem("sm64.sort", v); setSortState(v); },
    setHideResets: (v) => {
      localStorage.setItem("sm64.hideResets", v ? "1" : "0");
      setHideResetsState(v);
    },
  };
  const v = t.view;
  if (!v) return html`<p class="meta">loading… (server unreachable? check /health)</p>`;

  const tgt = v.target || {};
  const isActive = (sec) =>
    sec.course_id === tgt.course_id && sec.star_id === tgt.star_id;
  const active = tgt.course_id != null ? v.stars.find(isActive) : undefined;
  const rest = v.stars.filter((sec) => sec !== active);

  const unassignedVisible = v.unassigned.filter(
    (a) => !a.cleared && a.outcome !== "abandoned");
  const unassignedHidden = v.unassigned.filter(
    (a) => a.cleared || a.outcome === "abandoned");
  const unassignedRows = showUnassignedHidden ? v.unassigned : unassignedVisible;

  return html`
    <div style="display:flex;justify-content:flex-end">
      <button onclick=${() => setMenuOpen(!menuOpen)}>⚙ stats</button>
    </div>
    ${menuOpen && html`<${StatMenu} t=${t} close=${() => setMenuOpen(false)} />`}
    <${ControlBar} ui=${ui} />
    ${active && html`<${StarSection} sec=${active} t=${t} ui=${ui} pinned=${true} />`}
    ${v.stars.length === 0 && v.unassigned.length === 0
      ? html`<p class="meta">No attempts this session yet — grab a star.</p>` : ""}
    ${rest.length > 0 && html`<div class="meta listhead">this session — newest first</div>`}
    ${rest.map((sec) => html`<${StarSection} sec=${sec} t=${t} ui=${ui} pinned=${false} />`)}
    ${v.unassigned.length > 0 && html`<div class="starsec">
      <div class="shead"><b>No target</b>
        <span class="meta">failures before any star was grabbed or set</span></div>
      <${AttemptTable} attempts=${v.unassigned} rows=${unassignedRows} t=${t} />
      <${HideToggle} hidden=${unassignedHidden}
                     showHidden=${showUnassignedHidden}
                     setShowHidden=${setShowUnassignedHidden} />
    </div>`}`;
}
```

(The unassigned block intentionally keeps its original chronological order —
those rows have no completion times and no star identity, so the sort options
don't meaningfully apply.)

- [ ] **Step 3: Manual check** — set a target: its section appears pinned with
the gold border and ★ ACTIVE STAR tag, extracted from (not duplicated in) the
list below; remaining stars ordered newest-first; sort dropdown reorders rows
(numbering stays stable); "hide resets" removes ✘ reset rows while the reset
stat chips keep their values; both settings survive a page reload.

- [ ] **Step 4: Run the backend suite, commit**

Run: `uv run pytest -q` → pass.

```bash
git add src/sm64_events/ui/components/practice.js src/sm64_events/ui/index.html
git commit -m "feat: pinned active-star section + global sort/hide-resets bar

The target star extracts into a gold-bordered block at the top so the data
for the star being practiced is always in view; one global control bar
(user choice A) persists sort + hide-resets in localStorage. Hiding resets
is display-only — stats stay server-computed from full history."
```

---

### Task 10: Docs, live VERIFY, smoke test

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `docs/architecture.md`

- [ ] **Step 1: README — event table.** Update the `practice_reset` and
`state_loaded` payload columns (around line 68) to
`igt_frames_before, mario_acted, paused_frames_before, acted_tracking` and
`igt_frames_restored, mario_acted, paused_frames_before, acted_tracking`
respectively, and add a row after them:

```markdown
| `mario_acted` | _(none)_ | Mario's first non-passive action since the last anchor — the tracking layer uses it to judge whether an attempt had any behavior |
```

- [ ] **Step 2: README — outcomes paragraph** (around line 84). Append:

```markdown
Two more automatic discards (never recorded as attempts): reset/load closures arriving after ≥5 s of pause (`paused_frames_before` ≥ 150 — AFK, not practice), and — for attempts opened by an `acted_tracking` anchor — ANY non-success closure where no `mario_acted` event arrived (no behavior = garbage). Successes always count.
```

- [ ] **Step 3: README — API table** (after the `PUT /api/statmenu` row):

```markdown
| `PUT /api/markers` `{course_id, star_id, strat_tag?, markers: [{frames, label}]}` | Replace the timeline annotation markers for one star+strategy (max 30; labels 1–60 chars) |
```

Also note in the `/api/session` row description that sections now include
`markers_by_strat` and `progress`, are ordered newest-activity-first, and the
target star's section is always present.

- [ ] **Step 4: CLAUDE.md module map** — extend the UI components row to also
name `ui/components/progress.js` (per-star completion-time-over-time graph,
gold = saved PBs).

- [ ] **Step 5: Full suite + frontend smoke test.**

Run: `uv run pytest -q` → all pass.
Then run the mandatory frontend smoke-test gate (frontend-smoke-test skill):
server up, open `http://127.0.0.1:8064`, exercise: marker add/delete (both
flows), progress graph rendering on both clocks, pinned active star, all four
sorts, hide-resets toggle, page reload persistence. Console must be clean.

- [ ] **Step 6: LIVE VERIFY with the human (gate for §1's assumption).**
Ask the human to run PJ64 + Usamune and the server, then:

1. Enter any course, let IGT run, open the Usamune pause menu and wait ~10 s,
   then use the menu's level reset. Expected: NO new reset row appears for
   that run (check the UI / `GET /api/session`), and the next attempt records
   normally.
2. Same, but pause only ~2 s before resetting. Expected: a normal reset row.
3. While paused, confirm via logs or `tools/watch_timer.py` (addresses in
   `memory/addresses.py`: `USAMUNE_OVERALL`, `GLOBAL_TIMER`) that IGT freezes
   while global_timer keeps advancing — this is the load-bearing assumption.

If (3) fails (global_timer also freezes in the menu), STOP: the AFK rule
cannot fire; record the finding and revisit the spec's fork 1 option B
(pause-address hunt).

- [ ] **Step 7: Record findings in `docs/architecture.md`** — add a short
subsection under the existing memory/domain notes: "Usamune pause menu:
IGT (USAMUNE_OVERALL) freezes while gGlobalTimer keeps running — verified
live on <date> with <how>. The AFK discard (PAUSE_DISCARD_FRAMES) and the
pause-streak inference in detectors/anchors.py depend on this. Emulator
pause freezes both clocks and is NOT caught (accepted)."

- [ ] **Step 8: Commit**

```bash
git add README.md CLAUDE.md docs/architecture.md
git commit -m "docs: mario_acted event, AFK/no-behavior discards, /api/markers, progress graph

Record the live-verified pause-menu clock behavior in architecture.md —
the AFK rule's load-bearing assumption — so future sessions inherit the
evidence, not just the constant."
```

---

## Spec coverage map (self-review)

| Spec section | Tasks |
|---|---|
| §1 AFK pause discard | 1 (detector), 2 (policy), 10 (VERIFY) |
| §2 no-activity all closures | 1 (event + marker), 3 (projection rule) |
| §3 timeline markers | 4 (storage/API/view), 7 (UI) |
| §4 progress graph | 5 (payload), 8 (UI) |
| §5 layout/sort/hide-resets | 6 (ordering/target), 9 (UI) |
| §6 testing & docs | every task's test steps; 10 (docs, smoke, VERIFY) |

Known consistency points double-checked: `paused_frames_before` /
`acted_tracking` key names identical in Task 1 detector, Task 2/3 projection,
and Task 10 README; `markers_by_strat` and the `progress` point fields
(`t_utc`, `igt_frames`, `rta_frames`, `igt`, `rta`, `attempt_id`,
`is_pb_igt`, `is_pb_rta`) identical between Task 4/5 payloads and Task 7/8
components; `Timeline` props `(tl, sec, t)` match between Task 7's definition
and Task 9's final practice.js; `mario_acted` is deliberately absent from
`BOUNDARY_EVENT_TYPES` (Task 3) so rollout counts survive it.
