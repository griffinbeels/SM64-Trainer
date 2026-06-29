# RANK-CUTOFF-VIDEOS Implementation Plan

> Intent: `.planning/rank-cutoff-videos/intent.md` (gitignored). Confirmed via
> socratic-gate 2026-06-25/29. Builds on the 2026-06-23 `rank-videos` feature.

**Goal:** Per rank-standard cutoff, link the time text to the fastest example
video that *ranks that tier* (band model). The Mario row = the strat's fastest
video = the overall strat link. Each cutoff is also a **manual-override slot** so
the user can paste a tier-appropriate example by hand. Add a section-header link
to the xcams "Daily Star" page for that exact star. No video → renders like today.

**Key finding (live audit):** xcams hosts only near-WR videos, so auto-extraction
fills mostly the Mario row; lower tiers populate via the manual override or stay
plain. The xcams star-page link covers "find your own examples."

**Architecture:** Additive to the ranks pipeline; classification/banner/medals/
routes untouched. Scraper emits per-strat timed `clips`; a pure resolver bands
them into `{rank:url}` (user overrides win); the store gains `clips`/`user_videos`
accessors + override CRUD + reconcile-preservation; the GET endpoint returns the
resolved `cutoff_videos` + `user_videos` + `xcams_url`; the UI links each cell and
adds per-cell override inputs + the section link. xcams URL derives read-time in
`links.py` from `(course,star)`/segment identity (no seed field).

## Global constraints
- `uv run pytest -q` green (baseline 965). Additive only; no change to the
  `strategies` ladder shape or classify/banner/medal/route ranking.
- Times compared in DISPLAYED centiseconds (reuse `classify.rank_for`).
- Manual overrides live in the existing rank_standards.json under per-entity
  `user_videos`; `_reconcile` preserves them across seed bumps.
- Browser↔GUI parity. uv only. UI: htm/ES-modules; verify `node --check`.

## Contracts (new)
- `classify.resolve_cutoff_videos(ladder_cs: dict, clips: list[[cs,url]],
  overrides: dict[rank,url] | None) -> dict[rank,url]` — band: fastest clip per
  `rank_for(cs)`; overrides win (and may add a tier with no clip).
- `RankStandards.clips(ek) -> {strat:[[cs,url],...]}`,
  `.user_videos(ek) -> {strat:{rank:url}}`,
  `.cutoff_videos(ek) -> {strat:{rank:url}}` (resolved),
  `.set_video(ek,strat,rank,url)`, `.clear_video(ek,strat,rank)`.
- `links.xcams_url(entity_key: str) -> str | None`.
- GET `/api/ranks/standards?entity=` gains `cutoff_videos`, `user_videos`, `xcams_url`.
- PUT/DELETE `/api/ranks/standards/{entity}/{strategy}/{rank}/video`.

## Tasks (TDD; commit per task; stage explicit paths)
1. **Scraper clips** — `strat_clips()` (all timed cams, sorted, deduped);
   `build_seed` attaches `clips`; `SEED_VERSION=3`. Tests: test_scrape_ranks.
2. **Pure resolver** — `classify.resolve_cutoff_videos`. Tests: test_ranks_classify.
3. **Store** — clips/user_videos/cutoff_videos accessors + set/clear + reconcile
   preserves user_videos. Tests: test_ranks_standards.
4. **xcams URL** — `links.xcams_url` (main courses confirmed; bowser `bow_*`;
   secret stars best-effort + VERIFY; movement segments None). Tests: test_links.
5. **API + service** — GET returns new fields; PUT/DELETE video endpoints;
   `service.set_rank_video`/`clear_rank_video` broadcast `rank_standards_changed`.
   Tests: test_ranks_api (+ service).
6. **UI** — standards.js: per-cell time links (`cutoff_videos`), header link =
   Mario-or-primary, edit-mode per-cell override input, section "examples on
   xcams →" link. Verify `node --check` + frontend smoke.
7. **Live re-scrape** — `uv run python tools/scrape_ranks.py` → v3 seed WITH clips
   (needs network; else documented live gate). Spot-check + full suite.
8. **Docs** — CLAUDE.md module-map rows (ranks/links/standards.js), README API
   surface, architecture.md finding (xcams = top-tier only). Merge `--no-ff`.

## Self-review hooks
- "overall = Mario row" holds: header uses `cutoff_videos.Mario || videos[strat]`,
  and in the common case the fastest clip ranks Mario so they coincide.
- No double storage file; user data survives a seed bump (reconcile test).
- Secret-star xcams prefix is the one VERIFY item — flagged, low-harm (a link).
