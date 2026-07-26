# Target Picker Strategy Step — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the header's practice-target control into one three-step modal — course → star/segment → strategy — that writes nothing until a strategy card is clicked.

**Architecture:** The existing `EntityPicker` grid gains ONE optional prop (`nextStep`) so a caller can hang a third step off it without the generic picker learning a domain rule. A new `strategystep.js` owns that step and the single commit write. Two new lazy GET endpoints supply per-strategy ranks and best-strategy ranks, computed on demand rather than added to the session view, which rebuilds on every WebSocket event.

**Tech Stack:** Python 3.12 via `uv`, FastAPI + Pydantic v2, pytest. Frontend is vendored Preact + `htm` tagged templates, no build step (`ui/index.html` is served per request — edit and refresh).

**Spec:** `docs/superpowers/specs/2026-07-25-target-picker-strategy-step-design.md`. Read it first; this plan implements it and does not restate its reasoning.

## Global Constraints

- **This branch is based on `mario-cap-rank-icons`, not on `main`** (decision, 2026-07-26). That branch **deleted `Medal`** from `ranks.js`. The rank icon is now `Hat` from `ui/components/hat.js`, and the tier registry lives in `ui/components/caps.js`. Anywhere this plan or the spec says "medal", read `Hat`.
  - `Hat({ tier, division = null, size = 18, title = null })` — `tier` is the raw tier key (`"Platinum"`), NOT a cap name.
  - **The division numeral only draws at `size >= 30`** (`DETAIL_MIN_SIZE` in `hat.js`) and only when `division` is non-null. A 16px Hat is a silhouette. Size the call site for what you need it to say.
- **Never print a raw tier key.** Tier keys are scraped from xcams and were deliberately not renamed, so `Gold` now renders *purple* (Waluigi) and `Platinum` renders *yellow* (Wario) — a surface printing the raw key is wrong on screen, not merely off-style. Route every visible tier string through `capName(tier)` and every division numeral through `divisionDigit(numeral)`, both from `./caps.js`. Enforced by `tests/test_ui_cap_names.py`; when a new call site holds a tier expression, **extend that file's `RAW_TIER_EXPRESSIONS` tuple** — it is a consciously-maintained list, not a JS parse.
  - This is a **UI-side** rule. The server keeps emitting raw tier keys (`"Platinum"`, `"II"`) — `ranks/` is the source of truth and must not learn cap vocabulary.
- **Never write reference code from memory — read the real API.** Every signature in this plan was read out of the repo, but constants, field names and arithmetic in it may still be wrong. If a value here contradicts the code, **the code wins — flag it, do not bend working code to match this document.**
- `uv run pytest -q` must pass before any commit. Run it from the repo root.
- **Never put a backtick inside an `html\`\`` template**, including inside an HTML comment — the first one ends the template literal and the page dies with an unrelated-looking error while `node --check` still passes.
- Tests that assert on source text must assert on `strip_comments(source)` (`tests/source_scan.py`) — a comment naming a rule must not satisfy a guard for it.
- `tests/test_docs_cover_api.py` fails until every new `/api` route appears in `README.md` **or** `docs/api.md`. Documenting a route is part of the task that adds it, not a follow-up.
- New tests are plain pytest functions (`def test_x(tmp_path):`) — there is no `unittest.main()` runner anywhere in `tests/`.
- Rule 11 (star ↔ segment parity, `CLAUDE.md`): anything built for one kind ships for both in the same change.
- Commit messages explain WHY, in the style of `git log`.

---

## File Structure

| File | Responsibility after this plan |
|---|---|
| `src/sm64_events/tracking/views.py` | + `build_entity_ranks(db, service)` and `build_entity_strategies(db, service, ek)` — pure view builders beside the existing ones |
| `src/sm64_events/server/api.py` | + `GET /api/target/ranks`, `GET /api/target/strategies`; `TargetBody` learns explicit-null |
| `src/sm64_events/tracking/service.py` | `set_target` clears the strategy when the caller explicitly asks |
| `src/sm64_events/ui/components/practicecell.js` | + `rankBadge` look flag (out-of-flow corner `Hat`) |
| `src/sm64_events/ui/entities.js` | `courseUnionGroups` stamps `rank` on options from a rank map |
| `src/sm64_events/ui/components/entitymodal.js` | + optional `nextStep` prop; unchanged for callers that omit it |
| `src/sm64_events/ui/components/strategystep.js` | **new** — the strategy cards, the fetch, the commit write, the `StratModal` |
| `src/sm64_events/ui/components/header.js` | target card opens the picker directly; `TargetEditor` deleted |
| `src/sm64_events/ui/index.html` | `.starrank-badge`, `.strat-grid`, `.strat-card` |

**Dependency order:** 1 → 2 → 3 (all touch `views.py`/`api.py`, so serialize them). 4 and 5 are independent of each other and of 1-3. 6 consumes tasks 1-3's endpoints. 7 consumes 4, 5 and 6. 8 is verification.

---

### Task 1: `build_entity_ranks` + `GET /api/target/ranks`

**Files:**
- Modify: `src/sm64_events/tracking/views.py` (add beside `entity_rank`, ~line 340)
- Modify: `src/sm64_events/server/api.py` (`create_api_router`, near the existing `@router.post("/target")` at ~line 554)
- Modify: `docs/api.md` (the `## HTTP API` section)
- Test: `tests/test_views.py`, `tests/test_api.py`

**Interfaces:**
- Produces: `build_entity_ranks(db, service) -> dict[str, dict]`, mapping entity key → `{"rank": str, "division": str, "strat": str}`.

**Contract:**

- Key is the canonical entity key from `ranks.standards.entity_key` — `"star:8:2"` / `"segment:12"`. Never the view's `"8:2"` composite; that shape belongs to `last_strat_by_star` and is not what the picker's option ids resolve to.
- Candidate entities are those with attempts: the keys of the `attempts_by_star` / `attempts_by_seg` grouping that `build_session_view` already builds in one pass (~line 643). **Do not** scan `all_attempts` per entity — that O(entities × attempts) pattern was removed here deliberately in a 2026-07-23 review.
- Per entity, per strategy from `ranks.strategies(ek)`:
  - clock = `service.ranks.clock_for(ek)` — the authoritative per-entity answer (`igt` for stars, `rta` for segments, overridable). `rank_by_star` and `segment_targets` each hardcode a literal instead; they agree today, but **do not copy the literals**.
  - basis = `grading_basis(rank_mode, pbs_by_strat.get(<key>), history, strat, clock)` — public, already used by `rank_by_star`. The `pbs_by_strat` key shape is `(course_id, star_id, timer_mode, strat)` for stars and `("segment", segment_id, timer_mode, strat)` for segments (`_current_pbs_by_strat`, ~line 113).
  - ladder = `service.ranks.ladder_cs(ek, strat)`; skip the strategy when it is empty.
  - graded = `_graded_progress(ladder, classify.display_cs(basis["frames"]))` — THE shared path. It returns `score`/`rank`/`division` among other keys.
- Winner = highest `score`. **Ties break on `min(strat)`** — the same deterministic convention `_fastest_strategy` uses for the same reason (dict-order luck is not a tie-break).
- An entity where no strategy grades is **absent from the map**, not present with nulls. The UI's "no rank if never attempted yet" is the absence.
- A tombstoned strategy must not win. Reuse the `masked` idea: `db.get_state("deleted_strats", {})` gives `{entity_key: [names]}`; filter those names out of the candidate list. (`masked` itself is a closure local to `build_session_view` — write the filter inline rather than trying to import it.)
- `rank_mode` reads `db.get_state("rank_mode", classify.DEFAULT_RANK_MODE)` and falls back to `classify.DEFAULT_RANK_MODE` when the stored value is not in `classify.RANK_MODES` — same forward-safe guard `build_session_view` uses at ~line 634.

**Endpoint:** `GET /api/target/ranks`, returning the map directly. `503 "database unavailable"` when `service.db is None`, matching `GET /session`. Declare it **before** any `/target/{...}` path route if one is ever added — the `/segments/vocab` comment at api.py:341 records that FastAPI matches in declaration order.

- [ ] **Step 1: Write the failing tests in `tests/test_views.py`**

Use the existing `make(tmp_path)` / `ev()` / `star()` helpers at the top of the file. Four tests, named for the behaviour:

- `test_entity_ranks_pick_the_best_strategy_not_the_active_one` — seed a star with a saved PB under two strategies where the *slower* one is the active strat; assert the returned `strat` is the faster one and the `rank` matches what `_graded_progress` gives for that strategy's ladder.
- `test_entity_ranks_omit_an_entity_with_no_gradeable_time` — an entity with attempts but no strat-tagged PB is absent from the map (`not in`, not `is None`).
- `test_entity_ranks_break_ties_on_the_strategy_name` — two strategies with identical ladders and identical times return the alphabetically-first name. Without this the result is dict-order luck.
- `test_entity_ranks_skip_a_tombstoned_strategy` — a strategy listed in the `deleted_strats` ui_state KV never wins, even when it has the best time.

Seed rank standards through the same path the existing rank tests use — check `tests/test_views_marelo.py` for how it attaches a `StandardsStore` to `service.ranks`, and copy that fixture rather than inventing one.

- [ ] **Step 2: Run them and confirm they fail for the right reason**

Run: `uv run pytest tests/test_views.py -k entity_ranks -q`
Expected: `ImportError`/`AttributeError` naming `build_entity_ranks`. If any test fails with an assertion instead, the helper import is wrong — fix that before implementing.

- [ ] **Step 3: Implement `build_entity_ranks`**

Place it directly after `entity_rank` so the two "which rank" functions read together. Give it a docstring in this file's house style: state that it answers *"how good am I at this star"* at pick time — a different question from `rank_by_star` (active strategy) and from `entity_rank` (best-possible ladder) — and say why it is a separate on-demand builder rather than a session-view field.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_views.py -q`
Expected: PASS, including the pre-existing tests.

- [ ] **Step 5: Add the endpoint and its test**

Add `GET /api/target/ranks` to `create_api_router`. Add to `tests/test_api.py`, following that file's existing client fixture: one test that the route returns 200 with a dict, one that a fresh db returns `{}` rather than erroring.

- [ ] **Step 6: Document the route in `docs/api.md`**

Add a row/section under `## HTTP API`. Say what the map is keyed by and that an absent entity means "never graded" — a consumer that reads a missing key as an error will get this wrong.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. `tests/test_docs_cover_api.py::test_every_api_route_is_documented` is the one that proves Step 6 landed.

- [ ] **Step 8: Commit**

```bash
git add src/sm64_events/tracking/views.py src/sm64_events/server/api.py docs/api.md tests/test_views.py tests/test_api.py
git commit -F <message file>
```

Message: why this is a lazy endpoint and not a view field (the view rebuilds on every event; avg-mode grading is O(history) per strategy per entity), and why the winner is the best strategy rather than the active one.

---

### Task 2: `build_entity_strategies` + `GET /api/target/strategies`

**Files:**
- Modify: `src/sm64_events/tracking/views.py`
- Modify: `src/sm64_events/server/api.py`
- Modify: `docs/api.md`
- Test: `tests/test_views.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: nothing from Task 1 (both are independent builders; they share only `grading_basis`/`_graded_progress`).
- Produces: `build_entity_strategies(db, service, entity_key) -> dict`.

**Contract — the payload:**

```json
{ "entity": "star:8:2",
  "kind": "star",
  "current": "Sign Clip",
  "allow_blank": true,
  "strategies": [
    { "name": "Sign Clip", "rank": "Platinum", "division": "II",
      "score": 74.2, "pb_display": "0'21\"53" },
    { "name": "Backwards LJ", "rank": null, "division": null,
      "score": null, "pb_display": null }
  ] }
```

- **`rank` and `division` are RAW tier keys** (`"Platinum"`, `"II"`), exactly as `_graded_progress` emits them. The server does not learn cap vocabulary — `capName`/`divisionDigit` are a UI-side display rule (see Global Constraints). Task 6 wraps them; this task must not.
- **`pb_display` is the server's `format_igt` output** (`M'SS"CC`, e.g. `0'21"53`) — the same string every other PB in this app shows. The spec's mockup wrote `0:21.53`; that was illustrative. Do not invent a second time format, and do not format in JS: `ui/format.js::fmtIgt` exists but the server already has the value.
- Strategy list comes from `_strategies_for` (stars) or `_seg_strategies` (segments) — the merged registered ∪ observed-on-attempts ∪ rank-standards list, tombstones filtered. Their exact signatures are at views.py:179 and :199; `_seg_strategies` additionally takes `default_strat` and puts it first. **Read both before calling them.**
- `current` is the entity's active strategy — `service.strat_by_star[(course, star)]` / `service.strat_by_segment[segment_id]` — masked to `None` when tombstoned.
- `allow_blank` is `False` exactly when the entity is a segment whose `SegmentDef.default_strat` is truthy, and `True` otherwise. This mirrors the rule `stratpicker.js` already applies from `sec.default_strat`, and the server enforces the same thing (`projection.py` caveat 17).
- Ranks use the same clock/basis/ladder/`_graded_progress` chain as Task 1, so a strategy's medal here is identical to the one the practice card's rank banner shows for that strategy. A strategy with no ladder or no gradeable basis gets `rank: null` — the card renders "unranked", which is a real state, not an error.
- Unknown or malformed entity key → raise `LookupError`, which `_http` (api.py:265) already maps to 404.

**Endpoint:** `GET /api/target/strategies?entity=star:8:2`.

- [ ] **Step 1: Write the failing tests in `tests/test_views.py`**

- `test_entity_strategies_include_a_strategy_seen_only_on_attempts` — the bug this fixes: a strategy with attempts but no ui_state registration must appear. Assert its name is in the returned list.
- `test_entity_strategies_rank_matches_the_section_banner_for_the_same_strategy` — build a session view and this payload from the same seeded db; assert the strategy's `rank` equals `view["stars"][0]["rank"]["rank"]` when that strategy is the active one. This is the "no medal may disagree" invariant, and it is the single most valuable test in this task.
- `test_entity_strategies_report_an_ungraded_strategy_as_unranked_not_missing` — a strategy with no PB is present with `rank: None`.
- `test_a_defaulted_segment_disallows_the_blank_strategy` — a segment def carrying `default_strat` returns `allow_blank: False`; a star returns `True`.
- `test_entity_strategies_reject_an_unknown_entity` — `pytest.raises(LookupError)`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_views.py -k entity_strategies -q`

- [ ] **Step 3: Implement `build_entity_strategies`**

Parse the entity key with `ranks.standards.entity_key`'s inverse — there is no public parser, so split on `":"` and validate: `["segment", id]` or `["star", course, star]`. Anything else raises `LookupError`. Docstring should name the merged-list sources and record that this endpoint is what stopped the header showing a narrower strategy list than the practice card.

- [ ] **Step 4: Run the tests** — `uv run pytest tests/test_views.py -q`, expect PASS.

- [ ] **Step 5: Add the endpoint + `tests/test_api.py` coverage** (200 for a real entity, 404 for junk).

- [ ] **Step 6: Document in `docs/api.md`** — including that `pb_display` is already-formatted and `allow_blank` is a server rule the client must honour, not a suggestion.

- [ ] **Step 7: Run the whole suite** — `uv run pytest -q`.

- [ ] **Step 8: Commit.** Message: the header was reading the raw registered strategy map and hiding strategies the practice card offered; this is the shared merged list, with each strategy's own rank attached.

---

### Task 3: Explicit-null strategy clear on `POST /api/target`

**Files:**
- Modify: `src/sm64_events/server/api.py` (`TargetBody` ~line 28, `@router.post("/target")` ~line 554)
- Modify: `src/sm64_events/tracking/service.py` (`set_target` ~line 324)
- Test: `tests/test_api.py`, `tests/test_lifecycle.py` (whichever already covers `set_target` — grep first)

**Interfaces:**
- Produces: `POST /api/target` with `strat_tag` **present and null** clears the entity's strategy. With `strat_tag` **absent**, behaviour is unchanged (existing strat left alone).

**Contract:**

`service.py:328` documents the gap verbatim:

> KNOWN GAP (found 2026-07-23, not yet fixed): a None strat_tag is omitted from the payload rather than journaled as an explicit clear, so picking "(no strategy)" in the header target editor leaves an already-set strat in place.

The fix:

- `TargetBody` — distinguish absent from explicitly-null using Pydantic v2's `model_fields_set` on the parsed body. `routes.js` category clearing already relies on this family of behaviour via `model_dump(exclude_unset=True)`; the same information is available as `"strat_tag" in body.model_fields_set`.
- `set_target(course_id, star_id, strat_tag=None, clear_strat=False)` — on `clear_strat` and a falsy `strat_tag`, publish `target_set` exactly as today, then `await self.set_strat(course_id, star_id, None)`. `set_strat` **does** journal an explicit null (service.py:356-364, verified).
- **The `target_set` payload shape does not change.** That is what makes the docstring's "auditing every target_set consumer" warning inapplicable — no consumer sees a new field. Say so in the code comment, and delete the KNOWN GAP note now that it is closed.
- Segments already behave correctly: `set_target_segment` delegates to `set_strat_segment` (service.py:342-354). Do not duplicate the flag there; make the star path match the segment path, and say that in the comment.

- [ ] **Step 1: Write the failing tests**

Both directions, or the fix is unpinned:

- `test_target_with_explicit_null_strat_clears_it` — set a strat, then `POST /api/target` with `{"course_id":…, "star_id":…, "strat_tag": None}`; assert the session view's `last_strat_by_star` entry is gone/None.
- `test_target_without_a_strat_key_leaves_the_existing_one` — same setup, body omits `strat_tag` entirely; assert the strat survives. **This is the regression guard** — a naive fix that always clears passes the first test and breaks every existing caller.

- [ ] **Step 2: Run them** — the first fails, the second passes. If the second fails, something else is already wrong; stop and report.

- [ ] **Step 3: Implement in `service.py` then `api.py`.**

- [ ] **Step 4: Run both tests** — expect PASS.

- [ ] **Step 5: Run the whole suite.** Pay attention to `tests/test_projection.py` and `tests/test_lifecycle.py` — they replay journals and are where an unintended extra `strat_set` event would surface.

- [ ] **Step 6: Commit.** Message: name the closed gap and why the payload shape is untouched.

---

### Task 4: `PracticeCell` rank badge + `courseUnionGroups` rank map

**Files:**
- Modify: `src/sm64_events/ui/components/practicecell.js`
- Modify: `src/sm64_events/ui/entities.js` (`courseUnionGroups` ~line 303)
- Modify: `src/sm64_events/ui/index.html` (beside `.entity-grid` rules, ~line 155-190)
- Test: `tests/test_ui_entities.py`, `tests/test_star_icons.py` (source guard — check which file owns the picker's cell contract first)

**Interfaces:**
- Produces: `PracticeCell` accepts `rankBadge` (boolean, default `false`). `courseUnionGroups(catalog, segments, courseByLevel, ranksByKey = {})` — the fourth parameter is optional and defaults to an empty object, so the existing call in `header.js` keeps working during this task.

**Contract — read spec §1a before writing a line of CSS.**

`index.html` currently carries `.entity-grid .starrank { display: none; }` with a recorded reason: an in-flow rank row cost a line per grid row and was most of the 94px that made the picker scroll on a 900px-tall window (live audit 2026-07-25). Grading the cells does not remove that cost — a course where two of seven stars are practiced still renders five "–".

So:

- `rankBadge=true` renders the rank icon as an **absolutely-positioned corner badge** over `.starholder`, in a new `.starrank-badge` element, and renders **nothing at all** when `rank` is falsy. It does **not** render the in-flow `.starrank` span.
- The badge draws `<${Hat} tier=${rank} size=${16} />` — **the same call the in-flow slot already makes** on this branch (`practicecell.js` line ~48). Do not pass `division`: at 16px `hat.js` draws no numeral anyway (`DETAIL_MIN_SIZE = 30`), and passing one would imply detail that is not rendered. `Hat` supplies its own tooltip from the tier.
- `rankBadge=false` (the banner, the default) is **byte-for-byte unchanged** — in-flow `.starrank` with its `–` fallback and its `rankbob` animation.
- `.entity-grid .starrank { display: none; }` **stays.** It is now a guard against a future call site restoring the scrolling row, and its comment should be updated to say that rather than "nothing grades a cell here", which stops being true.
- `.starcell` is already `position: relative` (index.html:54, its hover ✎ depends on it) — no new positioning context needed.
- The badge must not overlap the ✎ edit affordance at `top: 3px; right: 3px`. The picker passes no `onEdit` so no ✎ renders there, but placing the badge top-**left** costs nothing and removes the question.
- `courseUnionGroups` stamps `rank` on each option from `ranksByKey[<entity key>]?.rank`. **Note the key translation:** option ids are `"8:2"` / `"segment:12"`, the rank map is keyed `"star:8:2"` / `"segment:12"`. Get this wrong and every star silently shows no medal while every segment works — write the test for the star side first.

- [ ] **Step 1: Write the failing test in `tests/test_ui_entities.py`**

That file drives `entities.js` through real `node` (see its `run_node` helper) — use it, not a source scan. `test_course_union_groups_attach_a_rank_from_the_star_entity_key`: pass a rank map containing `"star:1:0"` and assert the option with id `"1:0"` carries that rank, and that an option with no map entry has `rank` undefined/null.

- [ ] **Step 2: Run it** — `uv run pytest tests/test_ui_entities.py -q`, expect FAIL.

- [ ] **Step 3: Implement the `courseUnionGroups` parameter.** Keep it optional and defaulted.

- [ ] **Step 4: Run it** — expect PASS.

- [ ] **Step 5: Implement `rankBadge` in `practicecell.js` + the `.starrank-badge` CSS.**

- [ ] **Step 6: Write the source guard for §1a.**

In whichever `tests/test_ui_*.py` owns the picker cell contract, assert on `strip_comments(...)`: `.entity-grid .starrank { display: none` is still present in `index.html`, and `practicecell.js` still branches on `rankBadge`. Probe the guard in both directions — feed it a comment-only sample and a real-code sample — following `test_the_guards_can_still_fail` in `tests/test_ui_picker_parity.py`. A raw substring check here would pass on the comment explaining the rule.

- [ ] **Step 7: Run the whole suite** — `uv run pytest -q`.

- [ ] **Step 8: Commit.** Message: why the medal is a badge and not a row — name the 900px audit it would otherwise undo.

---

### Task 5: `EntityPicker` gains an optional `nextStep`

**Files:**
- Modify: `src/sm64_events/ui/components/entitymodal.js`
- Test: `tests/test_ui_entitymodal.py`

**Interfaces:**
- Produces: `EntityPicker({..., nextStep})` where `nextStep` is a **component** rendered as `<${nextStep} value=... option=... onBack=... onClose=... />`.
  - `value` — the picked option id (string).
  - `option` — the picked option object, so the step can show its name without re-looking it up.
  - `onBack()` — clears the pending pick and returns to the layer-2 grid.
  - `onClose()` — closes the whole dialog. The step calls this after it commits.

**Contract:**

- When `nextStep` is **absent**, behaviour is unchanged: picking a cell calls `onChange(id)` and closes. **Three existing call sites depend on this** (the segment builder, the route step editor, the header). Any change visible to them is a bug.
- When `nextStep` is present, picking a cell sets pending state and renders the step **inside the same `Modal`** (`size="grid"`), with `title` = the picked option's `name`. `onChange` is **not** called — the step owns the write.
- The **clear cell** (`placeholder`, emitting `null`) always calls `onChange(null)` and closes, `nextStep` or not. There is no strategy to choose for "nothing".
- Escape stacks: at the step it goes back to the layer-2 grid; at layer 2 it goes back to layer 1; at layer 1 it closes. The existing capture-phase Escape handler (~line 84) already implements the layer-2 half — extend the same handler, do not add a second listener, or the two will race.
- Focus on entering the step must land somewhere real. The existing drill-in uses a `ref` callback (`focusOnDrillIn`, ~line 79) because the clicked button unmounts and focus falls to `<body>`, from which Tab escapes the dialog. **The same problem applies here** — the step needs the same treatment on its own Back button.

- [ ] **Step 1: Write the failing tests in `tests/test_ui_entitymodal.py`**

Assert on `MODAL_CODE` (already `strip_comments`ed at the top of that file):
- `test_a_caller_without_a_next_step_still_closes_on_pick` — the no-`nextStep` path still reaches `onChange` and `setOpen(false)`.
- `test_the_clear_cell_never_enters_the_next_step` — the `onPick(null)` path is not routed through pending state.
- `test_escape_backs_out_of_the_next_step_before_the_group` — one handler, and it clears the pending pick first.

Source assertions are weak evidence on their own — Task 8's live render is the real proof. Say so in the module docstring, as that file already does.

- [ ] **Step 2: Run them** — `uv run pytest tests/test_ui_entitymodal.py -q`, expect FAIL.

- [ ] **Step 3: Implement `nextStep`.** Keep the pending pick derived during render like `openGroupKey` is (its comment at ~line 67 explains why: an effect would paint one layer then correct it).

- [ ] **Step 4: `node --check` the file, then run the tests.**

Run: `node --check src/sm64_events/ui/components/entitymodal.js && uv run pytest tests/test_ui_entitymodal.py -q`

- [ ] **Step 5: Run the whole suite** — the picker-parity guards in `tests/test_ui_picker_parity.py` must still pass. That file fails if this component gained a domain word; `nextStep` is deliberately domain-free, so if it trips, the prop leaked something it should not know.

- [ ] **Step 6: Commit.** Message: one prop, three untouched call sites, and why the step lives outside the generic picker.

---

### Task 6: `strategystep.js`

**Files:**
- Create: `src/sm64_events/ui/components/strategystep.js`
- Modify: `src/sm64_events/ui/index.html` (`.strat-grid`, `.strat-card`)
- Test: `tests/test_ui_section_parity.py` or a new `tests/test_ui_strategy_step.py` — check whether the parity file already owns "both kinds render the same control" before adding a file

**Interfaces:**
- Consumes: `GET /api/target/strategies` (Task 2), `POST /api/target` incl. explicit null (Task 3), `EntityPicker`'s `nextStep` contract (Task 5).
- Produces: `StrategyStep({ value, option, onBack, onClose })` — the component `header.js` passes as `nextStep`.

**Contract:**

- Parses `value` with `parseSegmentId` / `parseStarId` from `../entities.js`. `parseSegmentId(id)` returns a number or `null`; a `null` means it is a star id like `"8:2"`. Ids are **strings**; `POST /api/target` needs **integers** — this boundary is the one `tests/test_header_ui.py::test_target_modal_still_posts_course_and_star_as_numbers` exists to guard, and Task 7 relocates that test here.
- Fetches with `getJSON` from `../api.js` on mount, keyed on `value`. Render a quiet loading state; on failure show the thrown message (`api.js` already unwraps the server's `detail`).
- Renders a `.strat-grid` of `.strat-card` buttons: rank icon + rank name + strategy name + `PB ${pb_display}`. The card for `current` carries a `● current` marker.
  - The icon is `<${Hat} tier=${rank} division=${division} size=${32} />`. **32, not 16** — `hat.js` draws the division numeral and wings only at `size >= 30`, and the division is exactly what distinguishes the strategies you are choosing between. This is the one call site in this plan that wants the detailed cap.
  - The rank text is `` `${capName(rank)} ${divisionDigit(division)}` `` — never the raw keys. See Global Constraints; `tests/test_ui_cap_names.py` enforces it, and **this file's tier expressions must be added to its `RAW_TIER_EXPRESSIONS` tuple** as part of this task.
  - Unranked (`rank: null`) → let `Hat` render its own no-rank state, and show a muted "no attempts" line instead of the PB.
- **No strategy** card: present only when `allow_blank` is true. Commits with an explicit null strategy. Wears the existing `.needs-strat` class when `current` is null — that is the blinking display this feature was asked for, and it already exists in `index.html:280`.
- **+ New strategy…** card: opens the existing `StratModal` (`./stratmodal.js`) with `entity` = the entity key and `existing` = the fetched names. On save, commit with that name. Match how `header.js` currently wires `onSaved`/`onClose`.
- Zero strategies available → render only the two cards above, over a one-line `.stable-empty compact` note (index.html:1369). **Not** `emptystate.js` — its cast art + quip is sized for a 458px card, not a modal step.
- **Exactly one write per commit**, then `onClose()`, then the caller refreshes. `POST /api/target` carries both the target identity and `strat_tag` (the endpoint already accepts it for both kinds — api.py:554).
- On write failure: alert with the thrown message and **stay open**, matching `stratpicker.js`'s dropped-write handling. Do not close on failure — closing would look like it worked.
- A `.entity-back` Back button at the top calling `onBack`, matching the layer-2 Back the grid already renders.

- [ ] **Step 1: Write the failing tests.** At minimum: the string→number boundary (`Number(` applied to the parsed course/star before the POST), that the blank card is gated on `allow_blank`, and that `.needs-strat` is applied when there is no current strategy. Assert on `strip_comments(source)`.

- [ ] **Step 2: Run them** — expect FAIL (file does not exist).

- [ ] **Step 3: Write `strategystep.js`.** Header comment in this codebase's style: what it is, why the commit lives here and not in the generic picker, and the "nothing is written until a card is clicked" rule with its reason.

- [ ] **Step 4: Add `.strat-grid` / `.strat-card` CSS** to the design-system block in `index.html`, beside the `.entity-grid` rules. Reuse the existing grid geometry idiom (`repeat(auto-fill, minmax(…, 1fr))`) rather than inventing a second one.

- [ ] **Step 5: `node --check` and run the tests.**

- [ ] **Step 6: Run the whole suite.**

- [ ] **Step 7: Commit.**

---

### Task 7: Rewire `header.js`, delete `TargetEditor`

**Files:**
- Modify: `src/sm64_events/ui/components/header.js` (`Header` ~line 140, `TargetEditor` ~line 267-361 deleted)
- Modify: `tests/test_header_ui.py`
- Modify: `.claude/rules/ui.md` (the header row and the entity-picker row of the change-map table)

**Interfaces:**
- Consumes: everything from Tasks 1, 4, 5, 6.

**Contract:**

- The *Practice target* card opens the picker dialog **directly**. Keep the card a `<button>` — `tests/test_header_ui.py::test_every_context_card_is_one_hit_target` and the whole-card hit-target rule depend on it.
- `TargetEditor` is deleted, along with the `.context-editor` wrapper if nothing else uses it (grep before removing the CSS).
- The picker gets `nextStep={StrategyStep}` and keeps everything it already has: `courseGroups` from `courseUnionGroups`, `depth={2}`, and the `iconContext` with **both** `segmentLevels: segmentLevelsOf(t.segments)` and `iconOverrides` — dropping either makes every segment cell fall back to a plain gold star while the banner shows its real art (whole-branch review I1, 2026-07-25; pinned by `test_target_picker_resolves_segment_art_like_the_banner_does`).
- Fetch `GET /api/target/ranks` when the dialog opens and pass the map to `courseUnionGroups`. On modal open, not on every render — the header re-renders on every WebSocket event.
- `EntityPicker` currently renders its own trigger `<button class="entity-trigger">`. The header does **not** want that button — its own context card is the trigger. Either add an `open`/`onOpenChange` control to `EntityPicker` or render `PickerDialog` directly; **decide from the code**, and prefer whichever leaves the other three call sites untouched.

**Test relocation — do not delete:**

`test_target_modal_still_posts_course_and_star_as_numbers` (test_header_ui.py:45) asserts `"course_id: Number(course)"` and `"star_id: Number(star)"` in `header.js`. That write moves to `strategystep.js` in Task 6. **Move the test, retarget it at the new file, keep its comment** — the string→number boundary at the API edge is still real. Deleting it because it broke is the failure mode this note exists to prevent.

- [ ] **Step 1: Update `tests/test_header_ui.py`** — retarget the moved test, keep the four others passing.

- [ ] **Step 2: Run it** — expect the retargeted test to fail (the write is not in `strategystep.js` yet if Task 6's version differs, or `header.js` still holds it).

- [ ] **Step 3: Rewire `header.js` and delete `TargetEditor`.**

- [ ] **Step 4: `node --check` header.js, run `tests/test_header_ui.py`.**

- [ ] **Step 5: Update `.claude/rules/ui.md`** — the header row (the target card now opens the picker directly) and the entity-picker row (`nextStep`, and that the target picker's third layer is the strategy step). Keep the existing prose density; that table is the next session's memory.

- [ ] **Step 6: Run the whole suite.**

- [ ] **Step 7: Commit.**

---

### Task 8: Live verification and docs

**Files:**
- Modify: `docs/architecture.md` only if a cross-cutting fact was learned (the three-way "which rank" distinction — active / best-strategy / best-possible-ladder — is a candidate; link, do not duplicate)
- Modify: `README.md` if the consumer-facing API surface description needs it

**Contract:**

**Unit tests plus `node --check` are not sufficient** — that exact combination shipped an invisible feature in this repo before. Verify by rendering.

Preferred recipe (`.claude/rules/ui.md`, "UI verification norms"): capture `GET /api/session?clock=&scope=` plus `/api/marelo`, `/api/segments`, `/api/segments/vocab`, `/api/routes`, `/api/target/ranks`, `/api/target/strategies` off a **running** instance — these are GETs and safe while the user is playing — then serve those fixtures plus `/ui/*` from a small static server in the scratchpad. That gives the real shell, real CSS, real container queries and the real component tree. **Never** start `python -m sm64_events.main` for this: the recorder lock is the only thing protecting a live recording.

Mutate the captured JSON to reach the states live data will not have:

- [ ] A course where some stars are ranked and some are not — **the §1a check**: confirm the grid does not scroll at a 900px-tall window. This is the regression the badge design exists to avoid; measure it, do not assume it.
- [ ] An entity with several strategies at different ranks — the step-3 grid.
- [ ] An entity with **no** strategies — the two-card empty state.
- [ ] An entity with no current strategy — the `.needs-strat` blink on the No-strategy card.
- [ ] A segment with `default_strat` — the No-strategy card must be **absent**.
- [ ] Escape from step 3 → step 2 → step 1 → closed, and confirm via the session view that **nothing was written** at any of those exits. This is the user's central requirement and the one a screenshot cannot prove.
- [ ] Sweep the width continuum, not three sample points — the sidebar steps at 1180px, so the pane a card lives in is not monotonic in window width.

Then:

- [ ] **Kill the fixture server in this same session** and confirm with `netstat -ano | grep :<port>`. A stopped background task is not proof the port is free — check the port and kill the surviving PID.
- [ ] Run `uv run pytest -q` one final time on the merged result.
- [ ] Commit any doc updates.

---

## Self-review notes

- **Spec coverage:** §1 → Tasks 5, 7. §1a → Task 4 (+ verified in Task 8). §2 → Task 6. §3 → Tasks 1, 2. §4 → Task 3. §5 → the File Structure table. §6 → each task's test steps + Task 8. §7 risks → Task 8's measured 900px check and the tooltip requirement in Task 1's payload (`strat` rides the map so the tile can name it).
- **The rank-map key translation in Task 4** (`"8:2"` option id vs `"star:8:2"` map key) is the single likeliest silent bug in this plan. Its test is called out explicitly.
- **The `pb_display` format** in Task 2 corrects the spec's illustrative `0:21.53` to this app's real `M'SS"CC`. If `format_igt` disagrees with that description, trust `core/timefmt.py`.
- **The spec's step-3 mockup is pre-cap-icons.** It draws `( ★ )` and prints "Platinum II"; on this base those are a Mario cap and "Wario 2". The mockup's *layout* is the requirement — icon, rank line, strategy name, PB line, current marker — not its vocabulary. Global Constraints govern the words.
- **`tests/test_ui_cap_names.py` is a gate this plan can trip.** Task 6 introduces a file that holds tier expressions; extending `RAW_TIER_EXPRESSIONS` is part of that task, not a follow-up. Task 4's badge passes `rank` as a prop (`tier=`), which `_PROP_PREFIXES` already exempts — no extension needed there.

---

### Task 3b: Grade a section on its ladder's OWN clock

Added 2026-07-26, after a probe during Task 2's review found a plan
contradiction. User ruling: fix the section banner.

**Files:**
- Modify: `src/sm64_events/tracking/views.py` (`build_session_view`, the star
  section's `star_basis`; check the segment section's for symmetry)
- Test: `tests/test_views.py`

**The defect (measured, not theorised).** `_section_banner` grades on the
**view** clock — whatever the header's Clock control is set to. But a rank
ladder is defined in ONE clock, recorded per entity as
`RankStandards.clock_for(ek)` (`igt` for stars, `rta` for segments). So with
the header set to "Anchor → grab", a star's RTA time is graded against its
IGT-defined ladder — the wrong ruler. RTA includes approach time, so it
systematically under-ranks.

Probe output, one star, one strategy, PBs saved on both clocks:

```
view clock=igt:  banner Diamond V   | picker endpoint Diamond V    AGREE
view clock=rta:  banner Platinum II | picker endpoint Diamond V    DISAGREE
```

This is **pre-existing and already visible in the shipped app**: `rank_by_star`
(the star quick-select row's medals) hardcodes `igt`, so at the rta setting
that row already disagrees with the section banner directly beneath it. Tasks
1 and 2 use `clock_for` and are on the correct side; the section banner is the
outlier. Fixing it aligns three surfaces.

**Contract:**

- The **rank basis** clock becomes `service.ranks.clock_for(ek)`, falling back
  to the view clock when `service.ranks is None`. This is the clock used to
  pick the row out of `pbs_by_strat` AND the one passed to `grading_basis`.
- **The displayed PB still follows the view clock.** `sec["pb"]` is a display
  choice and is correct as it stands — do not touch it. Only the grading basis
  moves. After this change `sec["pb"]` and `sec["rank"]` can legitimately be
  measured on different clocks; that is the same split the docstring already
  describes for average rank modes.
- `entity_rank` reads the same basis, so it moves with it. That is intended:
  it grades against the entity's best-possible ladder, which is defined in the
  same clock.
- Segment sections already force `rta`, which **equals** `clock_for` for every
  segment. Route them through `clock_for` anyway so there is one rule rather
  than one rule and one coincidence — and say that in the comment.

**Steps:**

- [ ] **Step 1: Write the failing test.** `test_a_star_section_grades_on_its_ladders_clock_not_the_view_clock`: seed one star, one strategy, PBs saved on BOTH clocks with times that land in different tiers, then assert `build_session_view(..., clock="rta")["stars"][0]["rank"]["rank"]` equals the value built at `clock="igt"`. The working probe is at `scratchpad/probe_clock.py` — read it, it already produces exactly this scenario.
- [ ] **Step 2: Run it.** Expect FAIL, showing the two different tiers.
- [ ] **Step 3: Add the regression guard.** A second test asserting `sec["pb"]` STILL follows the view clock — igt and rta views give different `pb` displays for the same star. Without this, a later "simplification" collapses both onto one clock and silently changes what the card shows.
- [ ] **Step 4: Implement.** Comment must record WHY: the ladder is defined in one clock, so grading against another compares to the wrong ruler.
- [ ] **Step 5: Run `tests/test_views.py`, `tests/test_views_marelo.py`, `tests/test_ranks_api_marelo.py`.** These three own rank grading. **Existing tests may legitimately break here** — this changes shipped behaviour on purpose. A break means reading that test and deciding whether it encoded the bug; if it did, update it AND say so in the report. Do not silently rewrite assertions to match new output.
- [ ] **Step 6: Full suite, then commit.**
