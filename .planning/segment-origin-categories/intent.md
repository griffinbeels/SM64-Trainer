# Intent — segment library categories from trigger origin

**Status:** MERGED to main 2026-07-25 (`35304ba`, 29 commits, 1515 tests green)
**Branch:** `feature/segment-origin-categories` (from `main` @ 9d278e3)
**Spec:** `docs/superpowers/specs/2026-07-24-segment-origin-categories-design.md`

## Goal

Group the segment library by where each segment can START, derived from its
start rules: two collapsible levels, castle region → place, in gameflow order,
with a per-segment override for anything mis-placed. Extract the group chrome
into shared, depth-agnostic building blocks so the routes library, this one,
and the upcoming picker modal all render from one implementation.

## Touch set

| File | Why |
|---|---|
| `src/sm64_events/memory/addresses.py` | `world_regions()` BFS, `CASTLE_SECRET_STAR_AREAS` |
| `src/sm64_events/tracking/segments.py` | `start_origin()` + its per-trigger table, `vocab().origins` |
| `src/sm64_events/tracking/views.py` | origin stamp helper for `/api/segments` rows |
| `src/sm64_events/tracking/service.py` | `set_segment_origin` (+ existing `_segments_changed`) |
| `src/sm64_events/server/api.py` | `GET /api/segments` stamp, `POST /api/segment/origin` |
| `src/sm64_events/ui/group.js` (new) | `buildTree(items, levels)` |
| `src/sm64_events/ui/components/grouplist.js` (new) | `GroupedList` — any depth |
| `src/sm64_events/ui/components/routes.js` | migrate onto the shared pieces (behaviour-preserving) |
| `src/sm64_events/ui/components/segments.js` | grouped library + editor origin control |
| `src/sm64_events/ui/index.html` | `.route-cat*` → `.lib-cat*`, `--depth` indent |
| tests | `test_addresses.py`, `test_segments.py`, API tests, headless render |

## Collision risk with other in-flight work

- **`.claude/worktrees/desktop-gui-packaging`** — desktop shell / packaging
  zone. No overlap with this touch set. **Independent.**
- **Shared contracts** (`core/events.py`, `core/snapshot.py`,
  `tracking/projection.py`, `main.py`) are NOT touched here.
- **Watch item:** a parallel session landed `segment_defs.default_strat`
  (migration v13) on main hours ago and works in this checkout. This branch
  adds **no migration** — the override is a `ui_state` KV — so the two cannot
  collide on schema version. `ui/components/segments.js` and
  `ui/components/routes.js` are the realistic conflict surface; both are owned
  entirely by this branch.
