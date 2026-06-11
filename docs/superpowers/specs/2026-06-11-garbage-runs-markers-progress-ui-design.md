# Garbage-run discard, timeline markers, progress graph, practice-view layout

**Date:** 2026-06-11 · **Status:** implemented + live-audited (merged 2026-06-11; findings in docs/architecture.md → "Practice-quality round"; fork-1 IGT-freeze assumption still VERIFY — see anchors.py)

Five features for the practice tracker, all inside existing seams: no new
memory addresses, no schema migration, one new journal event type.

## Decision log (user-confirmed)

| Question | Decision |
|---|---|
| AFK pause (≥5s) then reset, with real play before the pause | **Always discard** the closed run |
| Camera-only input counts as activity? | **No** — Mario actions only; no camera address hunt |
| Gold points on progress graph | **Saved PBs** (explicit Save-as-PB rows), not running best |
| Marker add interaction | **Both** click-to-place and exact-entry chip row |
| Progress graph X axis | **Wall clock**, UTC stored, browser-local display; MM/DD/YY prefix when span crosses a day |
| Lifetime progress axis | **One segment per session** with ⫽ break marks (not linear calendar time) |
| Sort + hide-resets controls | **One global control bar**, persisted across reloads |
| Active star | **Extracted** (not duplicated) into a static pinned section at top |

## §1 — AFK pause-then-reset discard

**Detector** (`detectors/anchors.py`): `AnchorDetector` keeps a pause-streak
counter in game frames. Per tick:

- anchor classified → stamp `paused_frames_before = streak` into the anchor
  payload (both `practice_reset` AND `state_loaded` — pausing then loading a
  savestate is equally AFK), then reset streak to 0
- `curr.global_timer < prev.global_timer` (backward jump, incl. console-reset
  path that returns no anchor) → streak = 0 (self-heal, domain rule 4)
- `global_timer` advanced and `igt_overall` unchanged → streak += global_timer
  delta (frame-based, poll-rate independent)
- `igt_overall` advanced → streak = 0
- `global_timer` unchanged (poll faster than frame) → streak unchanged

**Policy** (`tracking/projection.py`): new constant
`PAUSE_DISCARD_FRAMES = 150` (5 s × 30 fps) with rationale comment. In
`_close_by_reset`: if `ev.payload.get("paused_frames_before", 0) >=
PAUSE_DISCARD_FRAMES`, drop the closed attempt entirely (same path as the
existing no-op-reset discard; the anchor still opens the next attempt).
Checked BEFORE the activity check — discard applies even when the player
acted before pausing (user decision). Old journal events lack the key →
default 0 → kept; historical rebuilds unchanged.

**Why this is safe for castle/level-entry resets:** IGT may sit frozen in the
castle, growing the streak — but `level_changed` already closed any open
attempt when the player left the level, so the level-entry anchor's discard
check is a no-op (nothing open). The streak only has consequences when an
attempt is open, i.e. in-level, where frozen IGT means game logic is stopped.

**Known edges (documented, accepted):**
- Dialog/cutscene time-stop also freezes IGT: reading a sign 5+ s then
  resetting discards the run — that is AFK-adjacent and acceptable.
- Pausing the emulator itself freezes BOTH clocks → streak doesn't grow →
  that flavor of AFK is not caught. Acceptable; the primary clock is game
  frames (domain rule 7).

**VERIFY (live gate, with the human):** with the Usamune menu open in-level,
`igt_overall` freezes while `global_timer` keeps ticking. Optionally
characterize dialog time-stop the same way. Record findings in
architecture.md with evidence.

## §2 — No-activity discard for ALL closure types

Today only reset-closures consult `mario_acted` (stamped on the CLOSING
anchor). Deaths, abandons, and hard resets of zero-input attempts still count.
User rule: any non-success run with no Mario activity is garbage.

**New journal event `mario_acted`** (emitted by `AnchorDetector`, which
already tracks the flag): fired once, the moment Mario first enters a
non-passive action after an anchor. `frame = curr.global_timer`, payload `{}`.
The existing swallow rule stands: the action transition ON the anchor tick
belongs to the warp/spawn, not to the new attempt.

**Anchor payload marker:** new anchors carry `"acted_tracking": true`.
This versions the rule PER ATTEMPT inside the journal — no global flags.

**Projection rule:** projector tracks `_open_acted` (set by a `mario_acted`
event, reset when an attempt opens/closes). Every NON-SUCCESS closer (reset,
death, abandoned, hard_reset) drops the attempt when its OPENING anchor has
`acted_tracking` and `_open_acted` is false. Successes always count.

**Back-compat:** attempts opened by legacy anchors (no marker) keep today's
exact semantics — reset-closures use the closer payload's `mario_acted`
(default True), other closures always kept. A naive "no event → discard"
rule would erase all pre-feature history on replay; the marker prevents that.
Death-synthesized attempts with no anchor at all (first event = the death)
keep the existing "a death is always meaningful" stance — there was no
tracking window to consult.

The detector keeps stamping `mario_acted` into anchor payloads (legacy
semantics) so mixed journals replay correctly.

## §3 — Timeline markers (per star, per strategy)

**Storage:** existing `ui_state` KV (`db.get_state`/`set_state`), key
`timeline_markers`, value `{"<course_id>:<star_id>:<strat>": [{"frames": int,
"label": str}, ...]}` — strat key `""` when no strategy. Lists kept sorted by
frames. Same category as `strategies` / `stat_menu`; no migration.

**API** (`server/api.py`): `PUT /api/markers` with body
`{course_id: int, star_id: int, strat_tag: str | None, markers:
[{frames: int, label: str}]}` — replace-the-list semantics (no per-marker
ids). Validation: `frames >= 0`, label trimmed and 1–60 chars, max 30 markers
per key → 422 on violation; 503 when db unavailable (statmenu precedent;
writes via `service.db.set_state` directly, no service-layer change).

**View** (`tracking/views.py`): each star section gains
`"markers_by_strat": {strat_key: [...]}` for that star, so the UI switches
strats without refetching.

**UI** (`ui/components/timeline.js`): renders the active strat's markers
(`sec.last_strat` or `""`) as blue dashed vertical flags with labels above
the strip (mockup-approved); axis max extends to include markers beyond the
longest success. Adding:
- click on the strip → marker at that IGT position, prompt for label
- chip row under the timeline: one chip per marker (`0'03"00 bobomb grab ×`),
  delete via ×, plus a `+ marker` chip opening time + label inputs. Time
  input accepts seconds (`3`, `3.5`) or IGT format (`0'03"50`).
Every change PUTs the full list, then refreshes.

## §4 — Progress graph (completion time over time)

**View** (`tracking/views.py`): each star section gains `"progress"` built
from non-cleared successes of the scoped attempt list (session scope → the
current session's attempts; lifetime → full history), grouped by session:

```json
{"sessions": [{"session_id": 3, "label": null, "started_utc": "...",
  "points": [{"t_utc": "...", "igt_frames": 1459, "rta_frames": 1502,
              "igt": "0'48\"63", "rta": "0'50\"06", "attempt_id": 41,
              "is_pb_igt": true, "is_pb_rta": false}]}]}
```

`is_pb_<mode>` = a `pbs` row exists with this `attempt_id` and `timer_mode`.

**UI** (`ui/components/progress.js`, NEW): SVG scatter under the timeline in
each star section. Y = completion time on the current clock (faster = lower);
gold points with bright rim for `is_pb_<clock>`, green otherwise; faint
polyline per segment in chronological order (line does not cross ⫽ breaks).
Session scope → one continuous wall-clock segment. Lifetime → one segment per
session separated by ⫽ marks; within a segment time is linear. X labels in
the browser's local timezone; MM/DD/YY prefix once the graph spans more than
one calendar day; tooltips always full date + time. No points → render
nothing (timeline precedent).

## §5 — Practice view layout

**View** (`tracking/views.py`):
- The target star's section is ALWAYS included, even with zero attempts in
  scope (set a target → its lifetime history, PB, markers show immediately).
- Sections ordered newest-activity-first: sort key = max attempt journal id
  among the star's scoped attempts (−1 when none, e.g. fresh target).

**UI** (`ui/components/practice.js`):
- Pinned `★ ACTIVE STAR` block: extract (not duplicate) the section matching
  `view.target`; gold accent border; full section content (header, timeline
  + markers, progress graph, attempts, stat chips). Remaining sections render
  below under "this session — newest first".
- Global control bar above the pinned block:
  - sort: `newest first` (default) | `oldest first` | `fastest first` |
    `slowest first`. Client-side. Comparators: newest/oldest by attempt id;
    fastest/slowest by the row's time on the current clock (success
    completion time, or how-far-in for failures), nulls last.
  - `hide resets` checkbox: filters `reset` and `hard_reset` rows from
    display only. Stats, reset-rate chips, and timeline are server-computed
    from full history — unaffected.
  - Both persisted in `localStorage` (`sm64.sort`, `sm64.hideResets`).
- Attempt numbering stays chronological under any sort (`#14` is always
  `#14`; numbering already derives from the unsorted list via `indexOf`).

## File-by-file change map

| File | Change |
|---|---|
| `detectors/anchors.py` | pause streak; `mario_acted` event; `paused_frames_before` + `acted_tracking` payload keys |
| `tracking/projection.py` | `PAUSE_DISCARD_FRAMES`; `_open_acted` tracking; AFK + acted discard rules in closers |
| `tracking/views.py` | `markers_by_strat`; `progress`; section ordering; target-section guarantee |
| `server/api.py` | `PUT /api/markers` + `MarkersBody` validation |
| `ui/components/timeline.js` | marker flags; click-to-add; chip row |
| `ui/components/progress.js` | NEW — progress scatter |
| `ui/components/practice.js` | pinned active star; control bar; sort/filter |
| `tests/test_anchors.py` | streak accumulation/reset/self-heal; event emission; anchor-tick swallow |
| `tests/test_projection.py` | AFK discard incl. 149/150 boundary; acted rule per closure type; legacy-journal stability |
| `tests/test_views.py` | progress payload; ordering; target guarantee; markers_by_strat |
| `tests/test_api.py` | markers endpoint validation + round-trip |
| `README.md` | `mario_acted` event; payload keys; `/api/markers`; session-payload additions |
| `CLAUDE.md` | module map row for `progress.js` |
| `docs/architecture.md` | IGT-freeze/pause findings AFTER the live VERIFY, with evidence |

No changes to: `core/events.py` (envelope unchanged), `core/snapshot.py`,
`memory/addresses.py` (no new reads), `storage/db.py` (ui_state suffices),
`stats/registry.py`, `main.py` (AnchorDetector already wired).

## Testing & verification

- Tests first (`snap(**overrides)` fixture pattern); full `uv run pytest -q`
  before merge.
- Live VERIFY session (human + PJ64): §1's IGT-freeze-during-menu assumption.
  The discard threshold ships behind that gate.
- Frontend changes go through the frontend-smoke-test gate (Chrome DevTools
  MCP, console clean) and a human-audit pass.

## Out of scope (explicitly deferred)

- Camera-input activity tracking (user: camera doesn't count).
- Pause/menu-open address hunt and the Phase-3 "menu" outcome — approach A
  covers the practice rule; revisit only if IGT inference misfires live.
- Timezone picker (browser-local display covers it).
- Per-star sort/filter overrides (global bar chosen).
- Trend lines / moving averages on the progress graph.
