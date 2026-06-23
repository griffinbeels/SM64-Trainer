# RANKS — Design Spec

**Date:** 2026-06-22
**Status:** Approved design, pending implementation plan
**Source of standards:** [sm64-xcams "Daily Star / Rank Standards"](https://sm64-xcams.netlify.app/beta?star=ssl_3) (Twig64)

## 1. Goal

When the user completes a star or segment, classify their time against
community-agreed, per-strategy **rank standards** and surface the rank
everywhere it's useful: by the PB, on the progress graph, and across routes.
Standards are seeded by scraping the xcams site but are fully **user-editable**
local data.

## 2. The rank ladder (one registry)

Nine fixed tiers, hardest → easiest, each with a color (from the site):

| Rank | Color |
|---|---|
| Mario | red `#e23b3b` |
| Grandmaster | dark red `#8b1a1a` |
| Master | purple `#7b3f9e` |
| Diamond | blue `#3f86d6` |
| Platinum | green `#5cb85c` |
| Gold | gold `#e0b520` |
| Silver | silver `#c2c2c2` |
| Bronze | bronze `#c0894a` |
| Iron | gray `#8a8a8a` |

- This list (name → color → order) is **one registry** in `ranks/standards.py`,
  mirrored once in the UI (like `stats/registry.py`). It is the single source
  for names, order, and colors.
- **Times are upper bounds.** Your rank = the best (highest) tier whose
  threshold your time beats.
- **Iron is the implicit floor:** a completion slower than the lowest defined
  tier ranks Iron. (Iron itself carries no time in the data — it's the
  "completed but unranked" catch-all.)
- Colors are tweakable later; this is the starting palette.

## 3. Data model — standards are per (entity, strategy)

Each star/segment has multiple **strategies**, and each strategy has its own
rank ladder. The trainer already treats "strategy" as a first-class per-section
concept (`strat_tag` on attempts/PBs; the per-section strat `<select>`;
`last_strat`/`strat_by_star`/`strat_by_segment`). Standards key on the same
identity.

### Store: `data/rank_standards.json`

A human-editable JSON file (chosen over a DB table for hand-editability;
mirrors the `replay_settings.json` precedent). Resolved via `core/paths.py` so
it lives in the right place from source and when frozen. A **bundled seed**
(the scraper's output) ships with the app and is copied into the data dir on
first run if absent. A corrupt/invalid file loses to the bundled seed so the
server always starts.

```json
{
  "version": 1,
  "entities": {
    "star:9:2": {
      "clock": "igt",
      "strategies": {
        "Nuts Pless": {"Mario": 12.93, "Grandmaster": 13.03, "Master": 13.16,
                       "Diamond": 13.36, "Platinum": 14.16, "Gold": 15.66, "Silver": 16.76},
        "Tama Pless": {"Mario": 13.06, "Grandmaster": 13.13, "Master": 13.26,
                       "Diamond": 13.46, "Platinum": 15.23, "Gold": 16.36, "Silver": 17.13, "Bronze": 19.90}
      }
    },
    "segment:7": {"clock": "rta", "strategies": {"No Reds": {"Mario": 41.5, "Gold": 44.0, "Silver": 46.0}}}
  }
}
```

- **Entity key:** `star:<course_id>:<star_id>` or `segment:<segment_id>` — the
  trainer's real identity. Fragile site slugs (`ssl_3`, `bow_wc`) live **only**
  inside the scraper's mapping, never in the store.
- **Times:** human-friendly seconds (`12.93`). Missing tiers are omitted.
- **`clock`** per entity: `igt` for stars, `rta` for segments (the trainer's
  existing split). Stored in data so it's editable and verifiable.

## 4. Classification (pure)

`ranks/classify.py`, no I/O:

- `rank_for(ladder, time_cs) -> Rank | None` — best tier the time beats; `Iron`
  if completed but below the lowest tier; `None` if the ladder is empty/absent.
- `next_tier(ladder, rank)` and `band_progress(ladder, time_cs)` for the banner.

**Comparison unit — centiseconds, via the displayed time.** Per the project's
"times come from the Usamune IGT clock" rule, classification compares the **same
centisecond value the user sees** (the time produced by `igt_clock` /
`format_igt`), not a raw frame delta. Thresholds (seconds) convert to
centiseconds once at load. This guarantees the displayed time and the displayed
rank never disagree.

### Which ladder grades a completion

- **Per-attempt rank** uses that attempt's own `strat_tag` (the strat it was run
  with) → fully derivable from stored data, **no DB migration**.
- **Section "current rank"** = rank of the best time under the **active** strat
  (`last_strat`). If a star has standards but no strat is selected, show
  "— pick a strat" (no rank) rather than guessing.
- **No standards / no PB / no strat** → no rank (gray "–").

## 5. Strategy integration

- Scraped strategy names become canonical options in the existing per-section
  strat selector — the option list is the union of (standards strats) ∪
  (registered strats in `ui_state`) ∪ (strats observed on attempts).
- Selecting a strat sets the active target (existing `POST /api/strat`) and
  thereby the ladder used for the banner.
- **Creating a custom strategy + ladder** happens in the standards panel
  (§7c); it then appears in the selector automatically. This covers segments
  with no scraped source (MIPS/LBLJ/BLJs) and any user-invented strat.

## 6. Scraper — `tools/scrape_ranks.py` (reusable)

Re-runnable: `uv run python tools/scrape_ranks.py` regenerates the bundled seed.

- **Scope:** every star slug (15 main courses + Castle secret stars) and every
  `bow_*` Bowser course (each Bowser battle **and** each Bowser-course level,
  where the site's **"No Reds" = pipe entry**).
- **Extraction:** for each entity, read the "RANK STANDARDS" table →
  `{strategy → {rank → time}}`.
- **Mapping:** slug → entity key + canonical strat names. Star slugs derive
  from `COURSE_NAMES` order; Bowser/pipe-entry → segment IDs need a small
  explicit map (authored, then **live-verified**).
- **Transport:** first look for the site's backing data file (these SPAs
  usually fetch one JSON — one request, parse); else fall back to a
  headless-browser DOM scrape of each star page. Idempotent either way.
- **No-source segments** (MIPS clip, LBLJ, BLJs): the seed ships **hand-authored
  reasonable default ladders**, flagged as defaults the user can edit.

## 7. UI surfaces (all in `ui/`, so they appear in browser + desktop GUI)

Badge style (chosen): **medal coin** (tier-colored circle with a ★) + name. The
compact medal coin is the graph-node form.

### a. Practice section header — rank banner
A tier-colored banner is the focal element: medal + rank name + PB + a
**gap-scaled progress bar toward the next tier** with `next: <Tier> −X.XXs`.

- **Bar fill** = `(current_tier_threshold − time) / (current_tier_threshold − next_tier_threshold)`.
  The `−X.XXs` is the remaining gap (`time − next_tier_threshold`).
- **Top tier (Mario):** no bar, no "next" (you can still improve past the Mario
  threshold — a full bar would wrongly imply "done").
- **Floor/unranked:** empty bar + the absolute gap to the lowest tier (no band
  start exists to scale against).
- **No strat selected (star has standards):** "— pick a strat to see your rank."

### b. Attempt rows + progress graph
- Each recent-attempt row shows that attempt's rank medal (from its own strat).
- The completion event surfaces "you just got <Rank>".
- `ui/components/progress.js`: nodes become **tier-colored medal coins** instead
  of plain dots; the PB node keeps a distinguishing ring.

### c. Collapsible rank-standards table (per section)
- **Closed by default**, openable (a disclosure row).
- Mirrors the xcams layout: ranks down the left (colored labels), strategies
  across the top, **active strat column highlighted**, the cell the PB currently
  reaches marked.
- **Edit model: view by default + "Edit" toggle.** Edit flips cells to inputs
  and reveals `+ Strategy`, `+ Custom standard set`, and Save/Cancel.
- **"Reset to community defaults"** re-imports the bundled seed for this entity.
- New component (e.g. `ui/components/standards.js`), rendered inside the
  star/segment section.

### d. Route preview / builder (`ui/components/routes.js`, `practice.js` RouteFocus)
- A rank medal on each step (for a K-of-N step: the **best-ranked candidate**).
- A **route-average badge** in the header: numeric scale Iron=1 … Mario=9, mean
  of step ranks (no-rank steps excluded), shown as `Avg: <Tier> · N.N/9`
  alongside the existing cumulative %.
- The **weakest-ranked step is flagged** so improvement targets stand out.
- Gray "–" for any step with no standards/strat/PB.

## 8. Backend wiring

| Concern | File |
|---|---|
| Ladder registry + standards store (load/save/CRUD/seed/reset) | `ranks/standards.py` (new) |
| Pure classification (rank/next-tier/band) | `ranks/classify.py` (new) |
| Reusable scraper | `tools/scrape_ranks.py` (new) |
| REST CRUD for standards | `server/ranks_api.py` (new); same error taxonomy as `api.py` |
| Rank fields in the session view (per-attempt, section banner, route steps/avg) | `tracking/views.py` |
| Route average rank | `tracking/routes.py` |
| Load the store at startup | `main.py` / `server/app.py` |
| UI rank registry mirror (names/colors/order) | `ui/components/ranks.js` (new) or `ui/format.js` |

No new DB migration: ranks are derived at view-time from existing attempt
times + `strat_tag` + the JSON standards.

## 9. REST surface (sketch)

- `GET /api/ranks/standards?entity=star:9:2` → ladders for an entity.
- `PUT /api/ranks/standards/{entity}/{strategy}` → upsert a strategy's ladder.
- `POST /api/ranks/standards/{entity}` → create a strategy.
- `DELETE /api/ranks/standards/{entity}/{strategy}` → delete a strategy.
- `POST /api/ranks/standards/{entity}/reset` → reset entity to bundled seed.
- Broadcast-only `ranks_changed` so open clients refetch.

## 10. Timing-basis correctness gate

Stars graded IGT, segments RTA (per-entity `clock`). Before trusting
classification, **live-verify with the human** that the site's absolute times
match the trainer's clock for at least one known star and one segment
(compare a known PB to the tier it lands in). This is a VERIFY-gate item, per
the project's address-verification discipline.

## 11. Testing

- **Unit:** `classify` (tier boundaries, Iron floor, top tier, missing tiers,
  empty ladder); `standards` store (load/save, CRUD, first-run seed copy,
  corrupt-file fallback, reset); scraper parsing against a captured fixture;
  route-average aggregation; `views` rank fields (per-attempt vs active-strat
  banner; no-strat → no rank).
- **Live VERIFY:** scrape accuracy vs the site for a few stars; the clock-basis
  gate (§10); Bowser/pipe-entry slug→segment mapping.

## 12. Open items / risks

- Scraper **transport** (backing JSON vs headless DOM) — resolved during
  implementation via network inspection.
- **Bowser/pipe-entry slug → segment-id** map — authored, then live-verified.
- **Clock basis** — must pass the §10 gate before classification is trusted.
- **Per-strat PB lookup** — the banner ranks the best time *under the active
  strat*; confirm the PB lookup is (or becomes) strat-aware where needed.
- **100-coin stars** (`star_id 6`) are unlikely to be on the site → simply no
  standards (shows no rank). Fine.

## 13. Non-goals (YAGNI)

- No in-app "re-scrape" button — scraping is a CLI tool; the UI's
  "reset to community defaults" uses the bundled seed.
- No retroactive DB migration — ranks are derived on the fly.
- No cross-strat "best rank across all strats" mode — classification is
  active-strat-only (per the agreed decision).
