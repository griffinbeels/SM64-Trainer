# Practice-banner option flicker — root-cause diagnosis (2026-07-25)

> User report: "the segment displayed in the practice section has a visible
> glitch when purging options based on the game state. It looks like it loads
> everything first, and then unloads invalid options."

READ-ONLY investigation. No source file was edited.

---

## 1. Root cause

**The server legitimately publishes a PROVISIONAL game state at every level
entry and contradicts it ~27 ms later; the UI commits every payload the
instant it arrives, so the provisional state gets painted and then purged.**

This is hypothesis 1 (two-payload race), but not in the shape the dispatch
guessed — the two payloads are not `stage` vs `view`, they are **two
consecutive `stage_changed` payloads**, and the *server* is the one that
emits an intermediate.

### The producer: a level entry always emits a transient area first

`detectors/area.py` emits an **establishing** `area_changed` on the level-entry
frame (stamped `from_transient: true`) and then the **real** area one game
frame later. This is unambiguous in the live journal
(`%LOCALAPPDATA%\SM64Trainer\data\tracker.db`, session 2026-07-23) — every
single level entry has this shape:

```
seq 208 level_changed f=284281 21:04:29.314 {'from': 24, 'to': 8, 'from_area': 1}
seq 211 area_changed  f=284281 21:04:29.314 {'level': 8, 'from': 1, 'to': 1, 'from_transient': True}
seq 213 area_changed  f=284282 21:04:29.341 {'level': 8, 'from': 1, 'to': 2, 'from_transient': False}
                                        ^^^ 27 ms after the provisional one
seq 359 level_changed f=584291 23:51:32.946 {'from': 6, 'to': 24, 'from_area': 1}
seq 361 area_changed  f=584291 23:51:32.946 {'level': 24, 'from': 1, 'to': 1, 'from_transient': True}
seq 363 area_changed  f=584292 23:51:32.973 {'level': 24, 'from': 1, 'to': 2, 'from_transient': False}
```

Measured gap across ~20 level entries in that journal: **23–46 ms**, i.e. one
game frame. The provisional area is essentially always **1**.

`detectors/stage.py:59-60` keys the emitted context on
`("castle", curr_area)` — deliberately, so a lobby↔upstairs walk re-offers the
right segments (`stage.py:26-29`). The consequence is that inside **Castle
Inside (level 6)** the entry burst emits **two `stage_changed` events**:

1. `{mode: "castle", level: 6, area: 1}`  ← the transient lobby
2. `{mode: "castle", level: 6, area: 3}`  ← the real destination, 27 ms later

Main courses are immune (keyed on `("course", id)`, so the area burst is
silent) — which is why this is a **segment**-side symptom, exactly as reported.

### The consumer: every payload is committed and painted

- `ui/store.js:170-171` — `stage_changed` sets `stage` unconditionally, with no
  coalescing:
  ```js
  } else if (ev.type === "stage_changed") {
    setStage(ev.payload);
  }
  ```
  Each WS message is its own macrotask, so 27 ms apart guarantees a paint
  between them (Chrome's frame budget is 16.7 ms).
- `ui/components/stagebanner.js:46-51` dispatches on `stage.mode` during
  render, and `SegmentRow` filters purely on `stage.level`/`stage.area`:
  ```js
  // stagebanner.js:432-434
  const here = (v.segment_targets || []).filter((s) =>
    s.enabled &&
    s.start_areas.some((a) => a[0] === stage.level && a[1] === stage.area));
  ```

### The exact sequence the user saw

Entering the castle **basement** or **upstairs** (a course exit, a warp, a
door — anything that is not the lobby):

| t | event | banner renders |
|---|---|---|
| 0 ms | `level_changed → 6` | (unchanged) |
| 0 ms | `area_changed → 1` (transient) → `stage_changed{castle, area 1}` | **the LOBBY's segment options** — the largest set in the corpus (LBLJ, lobby→basement, lobby→upstairs, …) |
| ~27 ms | `area_changed → 3` → `stage_changed{castle, area 3}` | the basement's options only — the lobby cells are **purged** |

Superset → subset, one paint apart. That is precisely "it loads everything
first, and then unloads invalid options".

### The identical twin (same root cause, different payload)

`segment_armed` / `segment_disarmed` ride the same transient. `store.js:147-155`
mutates `armedOrder` on each notice, and `stagebanner.js:221-225`
(`armedExtraCells`) renders a cell per armed segment. On the transient area the
server arms a lobby-destination segment and retracts it on the correction — the
retract path is explicitly documented at `tracking/segments.py:993-1002`
("the instant a LATER co-frame moves away (the transient lobby before a
basement/upstairs settle) we retract"), with the same-frame re-pin at
`segments.py:969-975` covering only co-frame events, so a correction one frame
later falls through to the relocation disarm at `segments.py:1027-1044`.
Result: a cell (and the header "⏱ running" chip) appears for ~27 ms and
vanishes. Same flash, second code path.

---

## 2. Hypotheses confirmed / killed

| # | Hypothesis | Verdict |
|---|---|---|
| 1 | Two-payload race | **CONFIRMED**, but between two consecutive `stage_changed` payloads, not `stage` vs `view`. See above. |
| 2 | Effect-after-render filtering | **KILLED as the purge.** No effect prunes options. `BowserCourseRow`'s restore effect (`stagebanner.js:346-353`) and `ArenaRow`'s auto-select (`stagebanner.js:398-407`) only *select* and *enable* — `segsForLevel` (line 88) deliberately keeps disabled segments, so nothing is removed. They DO cause a real post-paint flash of the **unselected** state (paint → POST → refresh → highlighted), which is a genuine second-order defect, but it is not a purge and is not what was reported. |
| 3 | Route-scoped filtering arriving late | **KILLED for game state.** `active_route` only changes on a journaled `route_selected`, i.e. a user action (`practice.js:811-825`). It is *not* refetched on game events. It does produce the same flash on the `pickRoute` path — pick a plan and the banner shows all 7 stars until the `/api/session` refresh lands — but that is user-driven, not game-state-driven. |
| 4 | (found) `refresh()` has no in-flight or ordering guard | **REAL, secondary.** `store.js:89-118` fires a fresh `getJSON('/api/session')` per REFRESH_ON event with no sequencing, and each response does `setStage(v.stage)` (line 93). An older response landing last reverts `stage` to a previous course/area. Bursty entries (`level_changed` + `attempt_completed` + `target_changed` in the same tick) make concurrent refreshes routine. Produces a wrong-row flash rather than a purge; worth fixing alongside. |

---

## 3. The industry name

The user's own word is the technically correct one. In reactive/dataflow
programming a **glitch** is exactly this: *the momentary observation of an
inconsistent intermediate value before the dependency graph converges*
(Cooper & Krishnamurthi, 2006; "glitch-freedom" is the guarantee that
consumers never see one). The UX-facing name is a **flash of incorrect
content** — rendering provisional data before it has been reconciled.

**Distinguish it from FOUC.** *Flash of unstyled content* (and its cousins
FOIT, flash of invisible text; FART, flash of inaccurate re-theming) is a
**presentation** failure: the DOM is already correct, its stylesheet/font/theme
just hasn't applied. This bug is a **data** failure: the DOM itself is built
from an input the producer is about to contradict. CLS (cumulative layout
shift) is the *metric* that would score the visible jump, not the cause.

Conventional remedies, in the order they apply here:

1. **Don't publish a state you will contradict** — coalesce the producer's
   transient behind a *settle window* (debounce/`distinctUntilChanged` on the
   settled value). The correct fix when the producer knowingly emits a
   provisional value, which is this case.
2. **Derive during render, never in an effect** — anything computed in
   `useEffect` is by definition one paint late (React's "You Might Not Need an
   Effect"); `useLayoutEffect` if a pre-paint mutation is truly unavoidable.
   Applies to the ArenaRow/BowserCourseRow flash.
3. **One authoritative "settled" flag, gating the first paint** — render a
   skeleton until *every* input the render depends on has arrived, rather than
   letting several independently-arriving payloads each trigger a paint
   (Suspense; a single `isSettled` boolean; an atomic/transactional store
   commit).
4. **Stale-while-revalidate for UI** — keep the last known-good content on
   screen while the new one is pending (`useDeferredValue` / `startTransition`)
   instead of showing a provisional set. The right choice when the settle
   window is long enough to feel like a stall.

---

## 4. Minimal fix

**Purely client-side. No server change is required** for the reported symptom.

`src/sm64_events/ui/store.js` — commit `stage` through a settle window so a
level-entry burst collapses into one commit. ~8 lines.

```js
// A level entry emits a PROVISIONAL area first: the area detector's
// establishing (from_transient) area_changed, corrected to the real area one
// game frame later — measured 23-46 ms apart across every entry in the live
// journal (2026-07-23, e.g. 23:51:32.946 -> .973). stage.py keys the castle on
// its AREA, so both edges emit stage_changed, and committing both paints the
// lobby's segment options and then purges them. Hold the payload for a settle
// window and commit only the last: the banner shows the settled answer only.
const STAGE_SETTLE_MS = 120;
const stageCommit = useRef(null);
const commitStage = useCallback((payload) => {   // used by BOTH paths
  clearTimeout(stageCommit.current);
  stageCommit.current = setTimeout(() => setStage(payload), STAGE_SETTLE_MS);
}, []);
useEffect(() => () => clearTimeout(stageCommit.current), []);
```

- WS handler (`store.js:170-171`): `setStage(ev.payload)` → `commitStage(ev.payload)`.
- `refresh()` (`store.js:93`): `setStage(v ? v.stage : null)` must also cancel
  any pending commit, or a stale debounced payload lands after the refetch —
  route it through `commitStage` too (or `clearTimeout` there).

Cost: the banner updates 120 ms after a stage change instead of instantly —
imperceptible next to the level-load fade, and strictly better than a wrong
first answer.

**Better-placed alternative (server, if you want the GUI, the browser, and the
redundant broadcast all fixed at the source):** give `StageChangeDetector` a
2-poll settle — hold a newly resolved context until it repeats on the next
`process()` before emitting. It is bounded per-detector state, which
`.claude/rules/memory-detectors.md` domain rule 4 already sanctions, costs one
poll tick (~30 ms) of latency, and is directly testable in
`tests/test_stage.py`. It does **not** fix the armed-extras twin.

**The twin needs its own change** (server): `tracking/segments.py:996` should
not publish a `segment_armed` notice for a `_pending` entry until the entry
frame is spent, since the co-frame retract at `segments.py:1000-1002` may
withdraw it. Separate fix, same root cause — worth its own ticket.

---

## 5. Does it predate this branch? YES — not a merge blocker

| Responsible line | Introduced |
|---|---|
| `store.js` `setStage(ev.payload)` on `stage_changed` | `2ac59f4` 2026-06-12 *feat(ui): track current stage in the store* |
| `stagebanner.js` `SegmentRow` `start_areas.some(...)` area filter | `1dbc349` 2026-06-13 *feat: castle subarea segment quick-select* |
| `stage.py` keying the castle on `curr_area` | `1dbc349` 2026-06-13 |

`git diff --stat main...HEAD` on this branch touches `views.py`, `grouplist.js`,
`routes.js`, `segments.js`, `group.js`, `index.html` — **not** `stagebanner.js`,
`store.js`, `stage.py`, or `segments.py`. The bug is ~6 weeks old and
independent of `feature/segment-origin-categories`. **It should ship as its own
fix, and must not gate this branch's merge.**

---

## 6. How to verify the fix

**The observable:** enter Castle Inside at a **non-lobby** area — exit HMC, DDD
or LLL into the basement, or take the star door to the upstairs. The segment
row must never show the lobby's segments. Entering the lobby itself is NOT a
reproduction (area 1 is both the transient and the real answer, so the two
payloads are identical and no flash occurs) — that is very likely why this went
unnoticed for six weeks.

**A screenshot cannot catch it.** The window is one game frame (~27 ms), often
a single browser paint. Screenshot timing is not deterministic at that
resolution. Instrument instead:

1. **Regression test (the durable one).** A JSDOM/preact test that mounts
   `StageBanner`, delivers `stage_changed{castle, area 1}` then
   `stage_changed{castle, area 3}` 27 ms apart, and asserts the DOM **never
   contained** a lobby-only segment cell. Fails today, passes after the fix.
   This is the `bug-to-test` artifact.
2. **Headless instrumentation (to see it live).** A `MutationObserver` on
   `.starrow` (`{childList: true, subtree: true}`) pushing
   `{t: performance.now(), cells: [...names]}` to `window.__paints`. The
   contract is: **one stage transition ⇒ exactly one committed option set.**
   Today `__paints` gets two entries 27 ms apart with different cell sets;
   after the fix, one.
3. **Live eyeball, as the backstop.** Human audit per the project's
   `human-audit` rule — the human runs the emulator and walks into the
   basement. Worth doing because the fix trades instant update for a 120 ms
   settle, and only a human can confirm that trade feels right.

Do **not** verify by unit test + `node --check` alone (project rule: that
combination once shipped an invisible feature).

---
---

# PART II — the class (scope widened 2026-07-25)

The user confirmed the flicker reproduces on `main` (consistent with §5) and
asked for it fixed **everywhere**, not just the practice section. Below is a
sweep of every render site in `src/sm64_events/ui/` with the same shape.

## 7. The class, in three sub-shapes

> **The defect:** derived UI state computed from N independently-arriving
> payloads, committed as each one arrives.

Every instance found falls into one of three sub-shapes. They need different
mechanisms, which is why "one patch" is not honest here — but they share one
principle: *never commit a render input the producer is about to contradict,
and never commit one input of a multi-input render alone.*

| | Sub-shape | What arrives wrong | Where the flash comes from |
|---|---|---|---|
| **S1** | **Producer transient** | One source emits a provisional value it contradicts within a settle window | The consumer paints both |
| **S2** | **Multi-source skew** | Render depends on A and B; A commits alone (B is `null`, or non-null but **stale**) | The paint between A and B |
| **S3** | **Post-paint derivation** | A value derived in `useEffect` instead of during render | Guaranteed one paint late |

## 8. The sweep

Every file in `ui/` was checked. Verdicts:

### Real instances — game-state driven (a player sees these without touching anything)

**A. `stagebanner.js` — castle option purge. S1. THE reported bug.**
`store.js:170-171` + `stagebanner.js:432-434`. Full analysis in Part I.
*User sees:* the lobby's segment options for ~27 ms, then purged to the real
subarea's.

**B. `stagebanner.js` — armed-extra cells. S1 (twin of A).**
`store.js:147-155` (`armedOrder` mutated per notice) + `stagebanner.js:221-225`
(`armedExtraCells`). The server arms a segment on the transient area and
retracts it on the settle — `tracking/segments.py:993-1002` documents the
retract explicitly; the same-frame re-pin at `segments.py:969-975` only covers
co-frame events, so a one-frame-later correction falls through to the
relocation disarm at `segments.py:1027-1044`.
*User sees:* a segment cell (and the header "⏱ running" chip) appear and vanish.

**C. `runview.js` — the run clock resets to 0:00 before showing the final time. S2-stale.**
`runview.js:111-131`. `t.run` arrives via `refreshRun()` (WS `RUN_REFRESH_ON`),
but `routeView` and `hist` are fetched in an effect keyed on `[effRouteId, run]`
— i.e. **after** the paint that already committed the new `run`. The
frozen-clock decision reads the stale history:
```js
const mostRecent = hist && hist.runs.length ? hist.runs[hist.runs.length - 1] : null;
const lastFinished = (mostRecent && mostRecent.status === "finished" && ...) ? mostRecent : null;
```
*User sees:* a run finishes → paint 1 has the new `run` with the OLD `hist`, so
`lastFinished` is null and it falls through to the **idle `0:00` + route
preview**; paint 2 (one round-trip later) shows the frozen finish time.
**Showing a runner 0:00 at the instant they finish is arguably worse than the
reported bug** — it lands on the number they care most about.

**D. `practice.js` RouteFocus — the ▶ CURRENT pointer jumps to step 1. S2-stale.**
`practice.js:798-810` (routeView refetched in an effect keyed on `[activeRouteId, t.view]`)
+ `practice.js:659-664`:
```js
let currentIdx = rv.steps.findIndex((s) => s.candidates.some((c) => candIsTarget(c, tgt)));
if (currentIdx === -1) currentIdx = 0;
```
`tgt` comes from the freshly committed `t.view`; `rv.steps` is the PREVIOUS
fetch. When the new target isn't in the old steps the `-1` fallback fires.
*User sees:* collect a star → the ▶ CURRENT badge snaps to **step 1**, then to
the correct step a round-trip later; the per-step and cumulative %s change
under it too.

### Real instances — user-action driven (lower severity, same class)

**E. `routes.js` — pickers render empty, then populate. S2-null.**
`routes.js:275-288` initialises `segs = []` and `vocab = null`, but the
readiness gate at `routes.js:303` only tests `routes === null`. So the tab
paints with no segment options (`ItemPicker`'s `segs.length === 0` branch at
line 154, "Add option" **disabled** at line 160) and **no "Run starts when:"
picker at all** (line 446 `${vocab ? … }`), then both pop in.
*Also a non-flash bug on the same lines:* `routes.js:135`
`useState(segs[0] ? segs[0].id : null)` captures the empty list at mount and is
never re-seeded when `segs` arrives.

**F. `compare.js` — comparisons paint closed, then open. S3.**
`compare.js:370-376` restores the remembered open-set in a `useEffect` instead
of a lazy `useState` initializer.
*User sees:* the saved comparisons render collapsed for one paint, then expand.
`grouplist.js:23-26` (`useOpenGroups`) does the identical job **correctly** with
a lazy initializer — this is the same idea done the slow way.

**G. `compare.js` — previous entity's comparisons linger. S2-stale.**
`compare.js:365` `useEffect(reloadCmp, [entity, strat])` — picking a different
run/strat keeps the old `cmp` on screen until the fetch lands.
*Borderline:* keeping the last good content while revalidating is a legitimate
choice (see standards.js below); it only reads as a bug because there is no
pending indication.

**H. `stagebanner.js` — arena/Bowser rows paint unselected. S3.**
`stagebanner.js:398-407` (`ArenaRow` auto-select) and `346-353`
(`BowserCourseRow` restore) run after first paint, then POST + `t.refresh()`.
*User sees:* the fight/reds cell paints **unhighlighted**, then highlights ~1
round-trip later. Confirmed in Part I that neither prunes anything — this is a
flash of the *unselected* state, not a purge.

**I. `routes.js:299-301` — `setStartCond` mirrored from `view` in an effect. S3.**
One paint with a stale/absent start-condition editor after selecting a route.
Low visibility (it's an edit buffer), but same shape.

### Checked and NOT instances

| File | Why not |
|---|---|
| `stratpicker.js` | Already carries the fix for its own version of this (`stratpicker.js:53-55` keeps the current value listed so a filtered dropdown never renders blank). No effect-derived state; the `nonce` remount is a deliberate snap-back to server truth. |
| `segments.js` | **The correct pattern already.** `segments.js:415`: `if (!defs \|\| !vocabData) return PageState(loading)` — a single readiness gate over BOTH payloads. Use this as the model. |
| `standards.js` | `standards.js:30-33` deliberately keeps old data visible until replaced — textbook stale-while-revalidate, with the intent written down. |
| `grouplist.js` | Lazy `useState` initializer for the open-set (`:23-26`). Correct. |
| `progress.js`, `timeline.js`, `ranks.js`, `icons.js`, `states.js`, `modal.js`, `feed.js`, `statmenu.js` | Pure renders from props. No local async state. |
| `header.js` | The target picker seeds from `tgt` via `useState` initializers (`:231-235`) and only mounts on click. A stale-initial-value question, not a flash. |
| `replay.js`, `failcomp.js`, `iconpicker.js`, `stratmodal.js`, `update.js` | Effect-fetched panels that render a loading state first — content appears, nothing incorrect is shown first. |
| `videosync.js`, `frame.js`, `group.js` | No payload-derived option lists. |

## 9. One systematic remedy

**Principle (worth writing into `.claude/rules/ui.md`):**

> A rendered list, selection, or timer is derived **during render** from inputs
> that are all from the same generation. If an input is missing or stale, show
> the last complete answer or a skeleton — never a partial one. Never commit a
> value the producer is about to contradict.

Three mechanisms implement it. They are small, and each covers a named set:

**Mechanism A — settle window on the store's live payloads.** `store.js`, ~8 lines.
```js
// A level entry emits a PROVISIONAL area first (the area detector's
// establishing, from_transient area_changed), corrected one game frame later —
// measured 23-46 ms apart across every entry in the live journal. stage.py
// keys the castle on its AREA, so both edges emit stage_changed. Committing
// both paints the lobby's options and then purges them.
const STAGE_SETTLE_MS = 120;
const stageCommit = useRef(null);
const commitStage = useCallback((payload) => {
  clearTimeout(stageCommit.current);
  stageCommit.current = setTimeout(() => setStage(payload), STAGE_SETTLE_MS);
}, []);
useEffect(() => () => clearTimeout(stageCommit.current), []);
```
Route **both** `store.js:170-171` and `refresh()`'s `setStage` (`store.js:93`)
through `commitStage`, or a stale debounced payload lands after a refetch.
→ **Covers A.** Cost: one file, ~8 lines.

**Mechanism B — `ui/settled.js`, a readiness gate derived during render.** New file, ~10 lines.
```js
// One rule for every list derived from more than one payload: render the LAST
// COMPLETE set of inputs, never a partial one. Derived during render (no
// effect), so it cannot itself be a paint late.
//   const [{ segs, vocab }, ready] = useSettled({ segs, vocab });
export function useSettled(inputs) {
  const last = useRef(null);
  if (Object.values(inputs).every((x) => x != null)) last.current = inputs;
  return [last.current || inputs, last.current != null];
}
```
→ **Covers E, and the first paint of C.** Cost: one new file + ~1 line per site.
This is `segments.js:415` generalised so the correct pattern is cheaper to
write than the wrong one.

**Mechanism C — a generation tag on the store.** `store.js` ~3 lines + ~3 per consumer.
```js
const [gen, setGen] = useState(0);   // bumped in refresh() alongside setView
```
Component-local fetches stamp the `gen` they answered; the render gates on
`fetchedGen === t.gen` and falls back to the last matching answer.
→ **Covers C and D** — the *stale-not-null* cases, which `useSettled` cannot
detect on its own. Cost: the store change is trivial; each consumer site needs
a `gen` state and one comparison. Two sites.

### What these do NOT cover — say so plainly

1. **B (the armed-extras twin) needs a server change.** A client settle window
   on the armed set would delay the "⏱ running" chip, which the armed-visibility
   rule wants instant. The right fix is `tracking/segments.py:996`: don't publish
   `segment_armed` for a `_pending` entry until the entry frame is spent, since
   the co-frame retract at `:1000-1002` may withdraw it.
2. **S3 sites (F, H, I) need local fixes, not a shared primitive.** The remedy
   is "derive during render / use a lazy `useState` initializer" — copy
   `grouplist.js:23-26`. A shared hook cannot impose this.
3. **`refresh()` has no in-flight or ordering guard** (`store.js:89-118`). Each
   REFRESH_ON event fires an unsequenced `getJSON('/api/session')`; an older
   response landing last reverts `view` and `stage`. Needs its own request-id or
   `AbortController` guard — orthogonal to A/B/C.
4. **`routes.js:135`'s stale initial `segId`** is a stuck value, not a flash.
   Separate one-line fix.

## 10. Ranked fix order (worst user-visible flash first)

| # | Site | Sub-shape | Mechanism | Why here |
|---|---|---|---|---|
| 1 | **C — run clock shows `0:00` on finish** (`runview.js:111-131`) | S2-stale | C | Wrong *time* at the moment a runner finishes. Worse than a wrong option list, and this is a speedrun timer. |
| 2 | **A — castle option purge** (`store.js:170`, `stagebanner.js:432`) | S1 | A | The reported bug; happens on every non-lobby castle entry. |
| 3 | **D — RouteFocus ▶ CURRENT jumps to step 1** (`practice.js:659-664`) | S2-stale | C | Fires on every star collected during a route. |
| 4 | **B — armed cell / running chip flash** (`segments.py:996`) | S1 | server | Same root cause as 2; contradicts the "a running segment is never invisible" rule from the other direction. |
| 5 | **`refresh()` out-of-order responses** (`store.js:89-118`) | — | own guard | Rare but reverts the whole view; hard to diagnose in the wild. |
| 6 | **E — routes.js empty pickers** (`routes.js:275-288, 303`) | S2-null | B | User-action driven, on a build tab. Cheap once B exists. |
| 7 | **F — compare.js open-set** (`compare.js:370-376`) | S3 | local | One-line change to a lazy initializer. |
| 8 | **H — arena/Bowser unselected first paint** (`stagebanner.js:346, 398`) | S3 | local | Cosmetic; the write is genuinely async, so a true fix means optimistic local selection. |
| 9 | **I — routes.js startCond buffer** (`routes.js:299`) | S3 | local | Lowest visibility. |
| — | `routes.js:135` stale `segId` | (not a flash) | local | Fold into 6. |

**Suggested split:** items 1-3 are one focused branch (the game-state flashes,
mechanisms A + C, ~2 files + store). Item 4 is a server-side branch with its own
`tests/test_segments.py` case. Items 5-9 are a cleanup pass. None of it blocks
`feature/segment-origin-categories` (§5).

## 11. Verification for the class

The §6 technique generalises: **one state transition ⇒ exactly one committed
render**. A `MutationObserver` harness asserting that invariant works for every
site above, since none of them legitimately need an intermediate paint.

Per-site observables:
- **C:** finish a run — the clock must go straight from ticking to frozen,
  never through `0:00`.
- **D:** collect a star mid-route — ▶ CURRENT must never touch step 1 on its way.
- **E:** open the Routes tab — "Add option" must never render disabled.
- **F:** open Compare on a run with saved comparisons — none may render
  collapsed first.

Screenshots cannot catch any of these; instrument, then hand to a human audit
(project `human-audit` rule) because every fix trades instant update for a
settle, and only the human can confirm that trade feels right.
