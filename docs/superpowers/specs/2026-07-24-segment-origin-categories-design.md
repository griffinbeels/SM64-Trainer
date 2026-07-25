# Segment library: categorize by trigger origin

**Date:** 2026-07-24
**Status:** approved design, not yet implemented
**Follows:** `2026-07-24-default-routes-corpus-design.md` (the 65-segment corpus
this taxonomy has to organize), the route-library grouping work
(commits `e01598b`, `ae2e9ee`)

## Problem

The segment library is a flat, scrolling list of 65 seeded definitions plus
whatever the user has built. Search (added 2026-07-24) helps only when you
already know the name. There is no way to answer the question a runner actually
asks — *"what can I practice from here?"* — even though the answer is already
encoded in every definition: a segment can only arm where its **start rules**
can fire. BLJs arm upstairs. Lakitu Skip arms on the castle grounds. SSL → LLL
arms on the way out of SSL. Nothing surfaces that.

The routes library solved its own version of this with two-level collapsible
categories over a free-text field. Segments need the same *shape* of navigation
but must not need the same *authoring*: the origin is derivable, so asking a
user to file 65 rows by hand would be busywork with a fragmentation risk.

## Prior art

- **In-repo precedent (the one that matters).** `ui/components/routes.js`
  already ships the exact interaction: nested collapsible groups, open-set
  localStorage, numeric-aware sorting, margin-indented rows. Its hard-won
  details are recorded in `.claude/rules/ui.md` and are reused verbatim here
  rather than rediscovered. This spec's UI is a second consumer of that
  pattern, not a new one.
- **Derived facets vs. user-authored tags.** The general split in library
  navigation is between tags a user writes (flexible, fragments, needs a
  vocabulary UI — which is why the routes work added `CategoryModal`) and
  facets derived from the data (always consistent, free at authoring time,
  wrong when the derivation is wrong). The standard resolution is *derive by
  default, allow an explicit override* — which is what the user asked for
  directly, and what §4 specifies.
- **Deliberately not novel.** Region membership is not invented here: it falls
  out of the `WORLD_EDGES_*` topology already in `addresses.py` for the segment
  builder's dropdown filtering. One registry, second consumer.

## Decisions (user, 2026-07-24)

1. **Two-level grouping: castle region → place.** Not a flat list of ~25
   origins, and not place → free-text category.
2. **Teach the detector MIPS → Basement**, rather than letting the three MIPS
   movements fall into "Anywhere".
3. **Add a per-segment origin override in the editor**, for anything the rules
   place wrongly.
4. **The shared categorized picker modal is a separate spec**, designed right
   after this one, consuming this taxonomy (§8).

## 1. Origin derivation

New pure function in `tracking/segments.py`, beside `arm_level` /
`start_level_set`, reading trigger **param names** — never the match lambdas
(the same decoupling `views._segment_start_areas` relies on, pinned by
`test_views.test_segment_banner_param_names_match_the_registry`):

```python
def start_origin(start_triggers: list) -> str | None
```

It returns a **world-node key** in the existing `world_connections()` format —
`"6:3"` for a castle subarea, `"22"` for a whole level — or `None` when the
rules carry no location.

| Start clause | Origin node | Why |
|---|---|---|
| `level_exit` | `from` + `from_subarea` | The place you are leaving. All 51 seeded exits omit `to` entirely, so the source is the only signal there is — and it is the one runners name ("coming out of SSL"). |
| `level_enter` | `to` + `to_subarea` | Where the arm lands. |
| `area_enter`, `attempt_anchor` | `level` + `area` | Already a position. |
| `spawned`, `warp_entered`, `key_grabbed` | `level` | Level-only; `key_grabbed`'s level is optional → may be `None`. |
| `star_grabbed` | any course in `COURSE_BY_LEVEL` (1–24, i.e. the 15 main + 3 Bowser + the slide/caps/WMOTR/aquarium levels) → its level, by reversing that table; course 0 → `CASTLE_SECRET_STAR_AREAS[star]` | "DDD → BitFS (sub)" starts on a course-9 star and resolves to DDD. |
| `reset_game`, anything unlocated | `None` | Honestly unplaceable. |

**Multiple clauses — most specific wins.** A node carrying a subarea beats the
same level without one: LBLJ's `level_enter to=6` + `attempt_anchor 6/1`
collapses to `"6:1"` (Lobby). If two clauses name genuinely different locatable
places, the **first clause wins** and the docstring says so; no seeded
definition hits this today.

**`CASTLE_SECRET_STAR_AREAS`** (new, `addresses.py`): `{3: AREA_BASEMENT,
4: AREA_BASEMENT}` — MIPS 1st and 2nd. The Toad stars are deliberately absent:
their per-star locations are not established in this codebase, and a
half-guessed row would mis-file segments silently, where a missing row files
them under "Anywhere" visibly. The comment must say that, so nobody "completes"
the table from memory.

## 2. Region taxonomy

New in `addresses.py`, beside the topology it reads:

```python
def world_regions() -> dict[str, str]   # node key -> castle-region node key
```

BFS from the five castle nodes (`6:1` Lobby, `6:2` Upstairs, `6:3` Basement,
`16` Castle Grounds, `26` Courtyard) over `WORLD_EDGES_TWO_WAY` +
`WORLD_EDGES_ONE_WAY` treated as undirected, not traversing *through* another
castle node. Every non-hub level is claimed by exactly the region you reach it
from.

Verified against the current tables while designing this — **every level
resolves, none unassigned**, and the assignment matches the game: BBH →
Courtyard, VCUtM → Castle Grounds, CotMC → Basement (through HMC), BitDW /
BitFS / BitS and the Bowser 1/2/3 arenas → Lobby / Basement / Upstairs. A wrong
or missing edge is still fixed in exactly one row of `WORLD_EDGES_*`, and the
taxonomy re-derives — the same promise the builder's dropdown filtering makes.

**Level 6 with no subarea → Lobby.** `level_enter to=6` without a `to_subarea`
("Castle Entrance → BoB") yields the node `"6"`, which no region claims —
regions are keyed on the three subareas. It resolves to the Lobby, because
every castle entry lands there before settling elsewhere (the transient-lobby
behaviour `detectors/level.py` and `area_changed`'s `from_transient` already
document). Written down because it is the one origin the BFS cannot answer.

**Display order.** Regions in gameflow order: Castle Grounds, Lobby, Basement,
Courtyard, Upstairs, then "Anywhere" last — the order the castle opens up
(8 stars → basement, 12 → courtyard, 30 → upstairs).

Inside a region, places are ordered by **class first, then id** (user decision:
Bowser and secret stages pinned to the top of their region rather than split
into top-level groups of their own — regions stay the only top level, so
"everything I can start from the basement half of the run" keeps working):

| Order | Class | Members | Sort within |
|---|---|---|---|
| 1 | the region itself | `"6:1"`/`"6:2"`/`"6:3"`/`"16"`/`"26"`, labelled `"<Region> (in-area starts)"` | — |
| 2 | Bowser stages | courses 16–18 (BitDW/BitFS/BitS) + `BOWSER_{1,2,3}_ARENA` (30/33/34, which have no course id) | level id — puts each course above its arena (17 < 30, 19 < 33, 21 < 34), and a region holds at most one pair |
| 3 | Secret stages | courses 19–24 (PSS, CotMC, TotWC, VCUtM, WMOTR, Secret Aquarium) | course id |
| 4 | Main courses | courses 1–15 | course id — which is gameflow order (BoB 1 … RR 15) |

**Labels.** Castle subareas use `CASTLE_AREA_NAMES` ("Lobby"/"Upstairs"/
"Basement"), everything else `LEVEL_NAMES`. Display only — the node key is the
identity.

## 3. Serving the taxonomy

- `tracking/segments.py::vocab()` gains `origins`: the ordered
  `[{key, label, region, region_label}]` list, plus the trailing "Anywhere"
  entry (`key: null`). The builder UI stays 100% vocab-driven, and the spec-B
  picker gets the same list for free.
- `GET /api/segments` rows gain
  `origin: {key, label, region, region_label, source: "derived" | "override"}`.
  Stamped by a helper in `tracking/views.py` (api.py currently returns
  `db.segment_defs()` raw and should stay that thin).
- Session-view segment sections do **not** carry origin yet — YAGNI; spec B
  adds it if the picker needs it.

## 4. The override

Stored as a `ui_state` KV `origin_overrides` (`{segment_id: node_key}`),
mirroring `icon_overrides` exactly — **not** a column on `segment_defs`.

The reason is `seed_dirty`: any write through `db.update_segment_def` flips a
seeded row's `seed_dirty=1`, which protects it from future reconcile refreshes.
Correcting a *display label* must not silently opt a castle movement out of
corpus updates. A KV sidesteps that entirely, needs no migration, and survives
a Reset to default (which is right — resetting the definition should not
un-fix the user's classification).

- `TrackerService.set_segment_origin(segment_id, node_key | None)` — `None`
  clears back to auto; validates the key against the taxonomy; ends in the
  existing `_segments_changed()` broadcast.
- `POST /api/segment/origin` body `{segment_id, origin}` — mirrors
  `POST /api/icon`'s shape and its stem-validation stance.
- Editor control in `ui/components/segments.js`'s Builder: a grouped select
  defaulting to **"Auto (Basement → SSL)"**, i.e. the detected value is shown,
  not hidden behind a blank. Choosing a place pins it; choosing "Auto" clears
  the KV entry.

## 5. Library UI

`ui/components/segments.js` renders the library as region → place →
rows, with the route library's rules carried over deliberately:

- **Open-set localStorage**, new key `sm64.segOriginsOpen` — nothing stored
  means everything collapsed, and a new key (never a re-used one) so no
  existing user's state is reinterpreted.
- **Rows indent by margin and stretch** (`width: auto`); the list is
  `overflow-x: hidden` with `minmax(0,1fr)` columns per level. The indent CSS
  must target the group wrapper's children, not `.route-list-item` — segment
  rows are `.segrow` and would otherwise sit flush.
- **A non-empty search auto-expands every group with a match.** Without this
  the existing search box would drop results into closed boxes — the one place
  the routes pattern cannot be copied unchanged, because the segment library
  has a search field and the route library does not.
- Group headers carry a count badge; the existing `.count-badge` header count
  keeps showing `shown / total` while filtering.

**Shared renderer.** The nested group chrome is extracted from `routes.js` into
`ui/components/grouplist.js`:

```js
GroupedList({ groups, openKey, renderRow })
// groups: [[topLabel, [[subLabel | null, items]]]]  — the shape routes.js's
// groupByCategory already returns
```

Both libraries feed it a pre-grouped tree; each keeps its own grouping
function (free-text path for routes, derived origin for segments) and its own
row renderer. CSS classes are renamed `.route-cat*` → `.lib-cat*` in the one
index.html block, with a grep sweep for stragglers. Rationale: copying ~80
lines of collapse/indent logic that took two commits to get right is how the
next bug lands in only one of the two copies — and spec B's picker is a third
consumer. Because this touches route code that landed the same day, it is its
own task with a headless render check on **both** tabs.

## 6. Testing

- `tests/test_segments.py` — `start_origin` table: one case per trigger type,
  the subarea-specificity collapse (LBLJ), differing-clause first-wins, MIPS
  course-0 → Basement, `reset_game` → `None`.
- `tests/test_addresses.py` — `world_regions()` assigns **every** level in
  `LEVEL_NAMES` (the design-time probe, promoted to a regression test) and
  pins the handful that would be wrong under a naive mapping: BBH → Courtyard,
  VCUtM → Castle Grounds, CotMC → Basement, the three arenas.
- `tests/test_defaults_corpus.py` (or a sibling) — every seeded segment
  resolves to a region except the documented "Anywhere" set, so a future
  corpus row that lands nowhere is caught at test time rather than in the UI.
- `tests/test_segments.py` — the taxonomy's ORDER, since it is a user
  decision and nothing else would catch a regression: regions in gameflow
  order, and inside the Lobby the sequence `Lobby (in-area starts)`, BitDW,
  Bowser 1 Arena, PSS, TotWC, Secret Aquarium, BoB, WF, … Also that node `"6"`
  (no subarea) resolves to the Lobby.
- API tests — the `origin` stamp; an override wins over the derived value and
  reports `source: "override"`; clearing restores `derived`.
- **Headless render** of the Segments and Routes tabs (per `.claude/rules/ui.md`
  — unit tests plus `node --check` once shipped an invisible feature):
  collapsed-by-default, one region open reveals only its places, a search
  reveals matching rows.

## 7. Rule 11 (star ↔ segment parity)

This ships origin grouping for **segments only**, and the asymmetry is
deliberate: stars are already organized by course everywhere they appear, and
the star-side equivalent of this navigation *is* the categorized picker modal
(spec B) rather than a second library grouping. Recorded here so the next
reader does not read it as an oversight.

## 8. Out of scope → next spec

The shared categorized picker modal that replaces the level / course / star
`<select>`s (segment builder, route candidate picker, target pickers) is
designed separately, immediately after this, and will consume `vocab().origins`
and `world_regions()`. That spec owns the UI/UX research: prior art on
categorized entity pickers, keyboard and search behaviour, and how it degrades
on a narrow OBS pane.

## 9. Risks

- **A wrong `WORLD_EDGES_*` row now mis-files segments**, not just a dropdown
  option. Mitigated by the coverage test and by the fact that both consumers
  read the same registry — a fix is one row, and both improve.
- **The override is invisible state.** A user who pins an origin and later
  wonders why a segment sits oddly has only the editor control to tell them;
  the control therefore always shows the detected value next to "Auto".
- **Touching `routes.js` the day its grouping landed.** Handled as its own
  task with a both-tabs render check, and the extraction is mechanical (no
  behaviour change to the route library).
