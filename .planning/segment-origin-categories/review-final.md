# Final whole-branch review — `feature/segment-origin-categories`

Reviewer: Opus 5 · 2026-07-24 · 18 commits, `main..HEAD`
Suite on the branch: **`uv run pytest -q` → 1508 passed, 3 warnings, 51.28s** (exit 0)

Verdict: **no Critical findings; 5 Important, 14 Minor.** Nothing here is a
merge blocker in the "will corrupt data" sense, but findings 1–3 are
user-visible or user-affecting and I'd fix them before the merge; 4–5 are false
claims written into the project's memory files, which the definition of done
treats as a broken build.

---

## Important

### I1 — A seeded segment renders under a group header literally labelled `6`
**Severity: Important · Confidence: High (reproduced with the real code)**

`src/sm64_events/tracking/segments.py:590` (`origin_taxonomy`) builds its place
list only from `world_regions()` keys. The node `"6"` (subarea-less castle
interior) is *not* a `world_regions()` key — `region_for_node`
(`memory/addresses.py:540`) special-cases it to the Lobby *region*, but nothing
gives it a *place* entry. `start_origin` can still return it.

One seeded segment does exactly that:

```
'Castle Entrance → BoB'  start_triggers[0] = {"type":"level_enter","to":6}
  → start_origin = "6"
  → origin_view  = {key:"6", label:"Castle Inside", region:"6:1", region_label:"Lobby"}
```

In the UI, `originLevels`' place level (`ui/components/segments.js:378-380`)
does `placeLabels.get(key) || key`, and `placeLabels` is built only from
taxonomy children — so the label falls back to the raw node key. Driving the
real `ui/group.js` `buildTree` with the real `origin_taxonomy()` output:

```
[Lobby] key=6:1 count=2
  [Lobby (in-area starts)] key=6:1/6:1  items=LBLJ
  [6]                      key=6:1/6    items=Castle Entrance -> BoB      <-- header reads "6"
```

It also sorts last inside the Lobby (order 999), below the main courses.

**Why it matters:** a raw internal key is showing in the library, on a seeded
row, in the one feature this branch exists to ship. Spec §2 says node `"6"`
"resolves to the Lobby" — the region does, the place doesn't, so the spec is
only half-implemented.

**Fix (pick one):**
- *Preferred* — normalize in one place: have `start_origin` (or `origin_view`)
  map a subarea-less `LEVEL_CASTLE_INSIDE` node to `node_key(6, AREA_LOBBY)`,
  which is what the spec's prose already promises. Then it files under
  "Lobby (in-area starts)" with LBLJ.
- Or add `"6"` to the Lobby's children in `origin_taxonomy()` with the
  `LEVEL_NAMES` label. Uglier — two Lobby-ish places.

### I2 — Pinning an origin does not move the segment in the library
**Severity: Important · Confidence: High**

`ui/components/segments.js:253-260` (`saveOrigin`) POSTs and then calls
`t.refresh()` only. The library's grouping reads `defs`, which is local state
set exclusively by `load()` (`getJSON("/api/segments")`, line 391) inside a
mount-only `useEffect` (line 392-393). `t.refresh()` refreshes the *session
view*, a different payload that carries no `origin`. `store.js`'s `REFRESH_ON`
set (lines 5-8) does not contain `segments_changed` either.

So: the user picks "Upstairs" in the editor, the select updates (local state),
and the row stays in its old group until they leave and re-enter the tab. The
one affordance the spec gives them for a misfiled segment appears to do
nothing. Every sibling handler in the same file gets this right — `toggle`
(line 418) and `remove` (line 423) both call `load(); t.refresh();`.

**Fix:** `saveOrigin` → `load(); t.refresh();` after the POST.

### I3 — The origin write triggers a full journal re-projection, contrary to its own docstring and to docs/api.md
**Severity: Important · Confidence: High**

`tracking/service.py:874` ends `set_segment_origin` with
`await self._segments_changed()`. That is not a broadcast —
`_segments_changed` (line 596) reloads the defs and calls `_reproject()`
(line 1026), which **replays the entire event journal**, rebuilds every attempt
and run, and does `db.replace_attempts(...)` / `db.replace_runs(...)`.

The docstring three lines above says *"broadcast-only like set_icon, a display
preference that is never journaled"*. `set_icon` (line 832) genuinely is
broadcast-only: one `publish(Event(type="icons_changed"))`, no re-projection.
`docs/api.md:114` repeats the claim: *"Broadcast-only (`segments_changed`),
never journaled."*

The origin is read by nothing in the projector — it is a pure display facet —
so the re-projection is wasted work on the user's whole history for every
dropdown change, and the two docs describing it are wrong. Spec §4 said "ends
in the existing `_segments_changed()` broadcast", so the implementer followed
the spec faithfully; the spec was wrong about what `_segments_changed` is.

**Fix:** mirror `set_icon` — publish a notice event (e.g.
`origins_changed`/`segments_changed` payload `{segment_id, origin}`) instead of
`_segments_changed()`, and if the UI should react, add that type to `store.js`
`REFRESH_ON`. If you keep the re-projection, both docstrings must stop calling
it broadcast-only.

### I4 — `--depth` is dead, its test is satisfied by a comment, and two docs assert the false mechanism
**Severity: Important · Confidence: High** — this is the direct sibling of `1ab2b66`.

`ui/components/grouplist.js:49` writes `style="--depth:N"` on every `.lib-cat`.
**No CSS anywhere reads it** — `grep -rn -- "--depth" src/` returns exactly two
hits: that inline style, and `index.html:1009`, which is a *comment*. Indent is
actually produced by the nested `.lib-cat .lib-group { margin-left:.5rem;
padding-left:.45rem; border-left: ... }` rule plus
`.lib-group > .route-list-item { margin-left: .5rem }`.

Three places now assert a mechanism that does not exist:
- `tests/test_ui_grouplist.py:22-26`, named
  `test_css_indents_by_depth_variable_and_never_scrolls_sideways`, whose only
  depth assertion is `assert "--depth" in INDEX` — satisfied purely by the
  comment on line 1009. Delete the comment and the test fails while the UI is
  unchanged; that is the exact failure mode `1ab2b66` was written to stop.
  (Its `assert "width: auto" in INDEX` is also substring-anywhere.)
- `index.html:1009` — "indent driven by --depth".
- `.claude/rules/ui.md:23` — "indent driven by a `--depth` custom property on
  `.lib-cat`". Rule files are this project's cross-session memory; a wrong row
  is worse than a missing one.

**Fix (pick one):** either make it true — `.lib-cat .lib-group { margin-left:
calc(.5rem + var(--depth) * 0rem) }` is silly, so more honestly use
`padding-left: calc(...var(--depth)...)` and drop the descendant nesting — or
delete the `style=${...--depth...}` from grouplist.js and correct the comment,
the rule row, and the test to describe the nesting that actually indents. The
test should assert the selector that does the work
(`.lib-cat .lib-group` + `border-left`), not a string that prose can satisfy.

### I5 — "all 51 seeded exits omit `to` entirely" is false, in three places
**Severity: Important · Confidence: High (counted against the corpus)**

Counted over `src/sm64_events/data/defaults.seed.json`: 51 `level_exit` start
clauses, of which **1 carries a `to`** — `MIPS Clip`,
`{"type":"level_exit","from":7,"to":6}`.

The claim appears in:
- `tracking/segments.py:513` (the `_ORIGIN_PARAMS` comment)
- `.claude/rules/tracking-storage.md` (the segments.py row)
- `docs/superpowers/specs/…-design.md:71`

The *derivation* is unaffected — `_ORIGIN_PARAMS["level_exit"]` reads `from`
regardless, and MIPS Clip correctly files under Hazy Maze Cave → Basement. But
the comment is load-bearing: it is the stated justification for reading `from`
rather than `to`, and the next reader will trust the number.

**Fix:** "50 of the 51 seeded exits omit `to`; the one that carries it (MIPS
Clip) is still filed by its source, which is the point."

---

## Minor

### M1 — The routes migration changed the indent it was supposed to preserve
**Minor · Confidence: High on the CSS, Medium on whether you care**

Old DOM: `.route-cat > .route-list-item { margin-left: .6rem }` — top-level
rows sat directly in the category div, **no guide line**. Sub-category rows sat
in `.route-subcat` (margin .5 + padding .45 + `border-left`) plus .5rem =
~1.45rem, one guide line.

New DOM (`grouplist.js:56-59`): every group's rows live inside a `.lib-group`
wrapper. So level-1 rows are now ~1.45rem in **with a guide line they never
had**, and level-2 rows are ~2.4rem in with **two nested guide lines**. Also
`.lib-cat + .lib-cat { margin-top: .5rem }` now applies to nested siblings too,
where `.route-cat + .route-cat` did not.

`ae2e9ee` ("indent route rows under their group") was the commit that tuned
this two days ago, so it is worth a look rather than an assumption. Not a bug —
flagging because the task and the ledger both call the migration
behaviour-preserving, and visually it isn't.

### M2 — Dead CSS rule left by the migration
`index.html:1052` — `.lib-cat > .route-list-item, .lib-cat > .segrow
{ margin-left: .6rem; }` can never match: `GroupedList` puts rows either at the
root (outside any `.lib-cat`) or inside `.lib-group`. Delete it, or it will
mislead the next person tuning indent. **Minor · High.**

### M3 — Dead helper left in routes.js
`ui/components/routes.js:55-56` — `byName` has no remaining call site (its two
users were `groupByCategory`'s sorts, now replaced by
`compareKeys`). Delete it. Its comment ("numeric-aware so 0/1/16/70/120") is
worth moving onto `CATEGORY_LEVELS`, which is where that behaviour now lives.
**Minor · High.**

### M4 — A row without an `origin` stamp produces a group labelled `undefined`
`segments.js:373` does `String(originOf(segment).region)`; `originOf` defends
against a missing `origin` by returning `{}`, and then `String(undefined)` =
`"undefined"`, which matches no taxonomy entry. Proven with the real
`buildTree`:

```
[undefined] key=undefined count=1 items=no origin at all
```

Only reachable from a stale/foreign `/api/segments` payload, so low
probability — but the defensive `|| {}` currently buys a worse outcome than no
defence. **Fix:** `String(originOf(segment).region ?? null)`, which routes it
into "Anywhere". **Minor · High.**

### M5 — Clicking a group header during a search silently rewrites the stored open-set
`grouplist.js:48`: `shut = !open.has(key) && !(forceOpen && forceOpen(node))`.
While a search is active `forceOpen` returns true unconditionally
(`segments.js:480`), so the group is open no matter what — but the click still
runs `toggle(key)`, which **adds** the key to the persisted open-set. A user
who tries to collapse a group mid-search sees nothing happen, and finds that
group open after clearing the search.

To answer the probe directly: **clearing the search does not corrupt anything**
— `forceOpen` never writes, so the stored set is exactly what the user left.
The corruption only comes from clicks *during* the search. **Fix:** ignore the
toggle (or visually honour it) while `forceOpen(node)` is true. **Minor · High.**

Also note `forceOpen` takes a `node` argument that `segments.js` ignores; spec
§5 says "every group *with a match*", which is equivalent only because `shown`
is pre-filtered. Fine, but the parameter is decorative.

### M6 — `test_defaults_corpus_origins.py` documents exceptions that don't exist
The module docstring says "The four exceptions are the star-grab starts
documented in the design spec" and the comment above `EXPECTED_UNPLACED` says
course-0 stars can't be placed — but `EXPECTED_UNPLACED: set[str] = set()` and
all 65 seeded segments resolve. Stale prose in the file whose job is to be the
authority on corpus coverage. **Fix:** say "no seeded segment is unplaced
today; a future course-0 (Toad) star start would land here." **Minor · High.**

### M7 — The `seed_dirty` test doesn't prove the row is seeded
`tests/test_api.py::test_origin_override_does_not_dirty_a_seeded_row` asserts
`not after["seed_dirty"]` but never asserts the row *has* a `seed_key` — if
LBLJ ever stopped being seeded the test would pass vacuously. It is true today
(`seg:lblj`, verified in the corpus), and it *would* fail if
`set_segment_origin` were rerouted through `db.update_segment_def`, so it does
prove the invariant it names. Add `assert after["seed_key"]` to keep it honest.
**Minor · High.**

### M8 — No test would have caught I1
Nothing asserts that a derived origin key exists in `origin_taxonomy()`.
`test_every_seeded_segment_resolves_to_a_region` only checks `region is not
None`, which node `"6"` satisfies. **Fix:** add to
`tests/test_defaults_corpus_origins.py`:

```python
def test_every_seeded_origin_has_a_place_in_the_taxonomy():
    known = {p["key"] for g in origin_taxonomy() if g["key"] is not None
             for p in g["children"]}
    for segment in SEED["segments"]:
        node = start_origin(segment["start_triggers"])
        assert node is None or node in known, segment["name"]
```

**Minor · High** (it's the regression test for the headline bug).

### M9 — Substring-only and implementation-pinned UI assertions
Siblings of the `1ab2b66` shape, in `tests/test_segments_editor_ui.py`:
- `test_search_opens_matching_groups` asserts only `"forceOpen" in SOURCE` — a
  comment mentioning forceOpen satisfies it. Assert
  `"forceOpen=${() => needle" ` or the `needle.length > 0` predicate.
- `test_editor_offers_an_origin_override_with_the_detected_value_visible`:
  `"/origin" in SOURCE` and `"Auto (" in SOURCE` — both prose-satisfiable.
- `test_origin_override_is_offered_only_for_saved_segments` pins the exact
  string `"initial && initial.id != null"`; an innocent `initial?.id != null`
  breaks it with identical behaviour.
- `test_library_groups_come_from_the_server_stamp_not_a_js_copy`: the
  `"origin.region" in SOURCE` half is dead (that literal does not appear;
  `"origin || {}"` is what passes). Its *negative* assertions
  (`world_regions`/`CASTLE_REGION`/`region_for_node` absent) are the good part
  and do pin the real guarantee — keep those.

`test_vocab_ships_the_origin_taxonomy` (`vocab()["origins"] ==
origin_taxonomy()`) is near-tautological, but it pins wiring and the *content*
is pinned by `test_origin_taxonomy_is_ordered_by_gameflow_then_class`. Fine as
is. **Minor · High.**

### M10 — `world_regions()` re-runs the BFS once per row
`region_for_node` (`addresses.py:551`) calls `world_regions()` on every
invocation, and `stamp_origins` calls it once per segment — 65 full
adjacency-builds + BFS per `GET /api/segments`, plus one more for
`origin_taxonomy()`. The graph is ~40 nodes so this is microseconds, and the
tables are module constants, so an `@lru_cache` on `world_regions()` is free
and obvious. Not urgent. **Minor · Medium.**

### M11 — Deleting a segment orphans its `origin_overrides` entry
`delete_segment` doesn't prune the KV. Harmless: `segment_defs.id` is
`INTEGER PRIMARY KEY AUTOINCREMENT`, so ids are never reused and a new segment
cannot inherit a dead override (I checked specifically for that bug — it does
not exist). Same pre-existing behaviour as `icon_overrides`. Worth one line of
cleanup if you ever touch delete. **Minor · High.**

### M12 — The rule-11 asymmetry is recorded only in the spec
Spec §7 argues the star↔segment asymmetry, and I think **the argument holds**:
stars are already organised by course in every surface, there is no star
"library" to group, and the star-side equivalent is explicitly the picker modal
in spec B. But CLAUDE.md rule 11 asks for the asymmetry to be *written down*,
and the precedent (`default_strat`) wrote it into
`.claude/rules/tracking-storage.md` **and** pinned it with
`test_star_sections_carry_no_default_strategy`. Specs are not loaded into
future sessions; rule files are. **Fix:** one clause in the new `ui.md`
"Segment library grouping" row — "segments only; the star-side equivalent is
the picker modal (spec B), not a second library grouping". **Minor · Medium.**

### M13 — "Anywhere" cannot be pinned, and that isn't written anywhere
`_origin_nodes()` (`api.py:97`) excludes the `key: null` group, and the editor
select filters it out (`segments.js:313`). So a user can never force a
mislocated segment *into* Anywhere — only clear back to Auto. Almost certainly
intended; it just isn't stated in `docs/api.md`'s row for the endpoint.
**Minor · Medium.**

### M14 — `_place_sort_key`'s class-4 branch is unreachable
`segments.py:565` — `(4, level)` for "a castle node that is not this region"
cannot be hit: every node without a `COURSE_BY_LEVEL` entry is either a Bowser
arena (caught by class 1) or a region node (caught by `node == region`).
Harmless defensive default; the comment slightly overstates it as a real case.
**Minor · Low.**

---

## Checked and CLEAN

**The `seed_dirty` invariant (probe 1).** `set_segment_origin` writes only
`db.set_state("origin_overrides", ...)`. Nothing on the branch calls
`db.update_segment_def` for origin, and `stamp_origins` is read-only over
`db.segment_defs()` rows. The test does prove what it claims (see M7 for the
one hole). **Clean.**

**Order is a user contract (probe 2).** Pinned twice and properly:
`test_castle_region_nodes_are_in_gameflow_order` pins the constant tuple
element-for-element, and `test_origin_taxonomy_is_ordered_by_gameflow_then_class`
pins the rendered region order `["16","6:1","6:3","26","6:2",None]` **and** the
Lobby's first 8 place labels in class order (in-area → BitDW → Bowser 1 Arena →
PSS → TotWC → Secret Aquarium → BoB → WF). Reordering `CASTLE_REGION_NODES` or
flipping any class in `_place_sort_key` fails these. **Clean.**

**`world_regions()` determinism (probe 3).** Deterministic across runs and
Python versions: the seed dict is built from the `CASTLE_REGION_NODES` tuple
(insertion-ordered, 3.7+), `frontier` is a FIFO queue, and the only
set-iteration (`adjacency.get(...)`) is wrapped in `sorted()`. A node
equidistant from two regions goes to the earlier one in `CASTLE_REGION_NODES`,
which is exactly what the docstring claims — no flipping. `PYTHONHASHSEED` is
irrelevant here. **Clean.**

**The null-origin path end to end (probe 4).** Verified by executing the real
`ui/group.js` against the real `origin_taxonomy()`:
`origin.region: null` → `String(null)` = `"null"` → matches the trailing
`{key: null}` entry via `regionOrder.set(String(region.key))` → group renders
as **"Anywhere"**, count 1, with the segment as a direct item (place level
returns `null`, so `buildTree` places it at the parent). `test_api.py::
test_a_location_free_segment_stamps_as_anywhere` covers the server half.

Is it too clever to survive the next edit? **It's acceptable.** Both
stringifications live in the same 20-line `originLevels` function, so an editor
touching one sees the other, and the ledger records it as a paired watch item.
The genuine cost of the trick is M4 (`undefined`), which the `?? null` fix
removes. **Clean, with M4 noted.**

**Search state on clear (probe 5).** `forceOpen` is read-only; clearing the
search restores the user's stored set byte-for-byte. Only the mid-search click
path (M5) writes. **Clean.**

**Routes migration equivalence (probe 6), everything except the CSS.**
- Open-state key unchanged (`sm64.routeCatsOpen`), pinned by a test.
- Node paths identical: old top key = category, old sub key =
  `` `${category}/${sub}` ``; `buildTree` produces `String(key)` at the root and
  `` `${parentPath}/${key}` `` below, with `PATH_SEP = "/" = CATEGORY_SEP`. No
  user loses their open groups.
- Ordering identical: old `ga-gb || la-lb || byName(a,b)` vs new
  `order = [...rankTop(name), name]` compared element-wise by `compareKeys`,
  whose number branch is numeric and whose string branch uses the *same*
  `localeCompare(…, {numeric:true, sensitivity:"base"})` as `byName`.
- Ungrouped-above-subgroups preserved: `subCategoryOf` returns `null` →
  `buildTree` parks the item in `items`, and `GroupedList` renders `items`
  before `children`.
- Counts identical: top-level `bucket.length` == old `routeCount(subs)`.
- Leftovers: `groupByCategory` and `loadOpenGroups` are genuinely gone (pinned
  by test); `byName` is not (M3).
**Clean apart from M1/M2/M3.**

**Leftovers sweep (probe 7).** `.route-cat` / `.route-subcat` are gone from
`index.html` and every JS file — the only hits repo-wide are in the plan/spec
documents (correct, they're historical) and in the test that asserts their
absence. No harness files, no untracked files (`git status --short` is empty),
no stale imports. `CATEGORY_SEP`, `UNCATEGORISED`, `rankTop`, `splitCategory`
all still used. **Clean apart from M2/M3.**

**Rule-file accuracy (probe 9), other than I4/I5.** I re-derived every claim in
the three new/changed rows against source:
- `memory-detectors.md` — BBH→courtyard, VCUtM→grounds, CotMC→basement, each
  arena→its exit's region: all four verified against `world_regions()` and
  pinned by `test_world_regions_match_the_castle_layout`. `region_for_node`'s
  lobby fallback: verified. "`CASTLE_SECRET_STAR_AREAS` is MIPS-only ON
  PURPOSE": verified, and the constant's comment says why. ✔
- `tracking-storage.md` — `_ORIGIN_PARAMS` is a table, one row per trigger
  type, defaults to Anywhere ✔; "not arm_level's mapping" ✔; most-specific
  wins / first-wins-on-conflict ✔ (both pinned by tests); `origin_view` shape
  ✔; `origin_taxonomy` in `vocab()["origins"]` ✔; the `seed_dirty` reason ✔.
  Only the "51" is wrong (I5).
- `ui.md` — server-stamped grouping, order from `vocab().origins`, `forceOpen`,
  the two localStorage keys, the `width:auto` history: all verified. Only the
  `--depth` claim is wrong (I4).

**Other project rules.**
- Rule 10 (browser↔GUI parity): pure `ui/` + server, `desktop/` untouched. ✔
- Rule 11: see M12 — argument holds, placement is thin.
- Rule 12 (route step order): untouched. ✔
- "One fact, one authoritative place": the taxonomy lives only in
  `tracking/segments.py`/`addresses.py`; the JS provably never re-derives it
  (pinned by the negative assertions in
  `test_library_groups_come_from_the_server_stamp_not_a_js_copy`). ✔
- Definition of done: suite green (1508); no new memory reads so no live gate
  needed; `docs/api.md` updated for the changed `GET /api/segments` shape and
  the new endpoint (README does not document `/api/segments`, so `api.md` is
  the right and only home); `docs/architecture.md` correctly left alone — this
  is module-local, not cross-cutting. ✔

**Security / input handling.** The override endpoint validates against the
taxonomy allowlist before writing (400 on anything else), so an arbitrary
string can never reach the KV and hide a segment in an unrendered group. Ids
are `AUTOINCREMENT`, so a recycled id cannot inherit a stale override.
`OriginBody` is a strict Pydantic model. **Clean.**

**Controller commits.** `1ab2b66` is correct and the restored comment
(`addresses.WORLD_EDGES_*`) does make `segments.js` findable from the registry
— I verified the citation is accurate and that the replacement assertion
(`world_regions`/`CASTLE_REGION`/`region_for_node` absent from segments.js)
holds. `ca61b79` and the ledger commits are docs-only; their content is covered
by I4/I5/M12 above. **Clean apart from those.**
