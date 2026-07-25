---
paths:
  - "src/sm64_events/ranks/**"
  - "tools/scrape_ranks.py"
  - "tests/test_scrape_ranks.py"
---

# Ranks — where to change what

| To change... | Edit |
|---|---|
| Rank classification (pure) | `ranks/classify.py` — THE rank ORDER (Mario..Iron) + RANK_SCORE + display_cs/rank_for/next_tier/band; compares DISPLAYED centiseconds so rank never disagrees with the shown time; Iron is the unbounded FLOOR (no threshold, never settable — `standards.set_threshold` rejects it), so its bar cannot measure from a start: it fills ASYMPTOTICALLY, `easiest_cutoff / time` — 1.0 AT that cutoff, decaying, never 0. Same curve as `ranks/scoring.py`'s Iron tail ON PURPOSE, so bar and MARELO score never tell one run two stories; a flat 0% means "never attempted", never "slow" (user decision 2026-07-25); ALSO `resolve_cutoff_videos(ladder_cs, clips, overrides)` — bands timed example clips into `{rank:url}` (fastest per tier, via the SAME rank_for; user overrides win); ALSO `RANK_MODES` (pb/avg10/avg50/best10/best50/lifetime) + `average_frames` — THE rank-mode registry (average mode: entity-level medals grade the mean of valid runs — successful, uncleared, strat-tagged, timed; views.py `_grading_basis` is the ONE resolver; per-attempt medals stay per-run) |
| Rank standards store (editable JSON, seeded) | `ranks/standards.py` over `data/rank_standards.json`; RANK_COLORS; entity_key; seed/corrupt→empty fallback; CRUD; per-cutoff videos: `clips()` (auto, from seed) + `user_videos()` (hand-attached) merged by `cutoff_videos()`; `set_video`/`clear_video` write overrides under the entity's `user_videos`, which `_reconcile` preserves across a seed bump; `seeded_strategies` = the custom-vs-default distinction |
| Rank standards scraper | `tools/scrape_ranks.py` — fetches the xcams Next.js chunk, extracts the embedded JSON.parse standards blob, maps keys→entities, writes `src/sm64_events/data/rank_standards.seed.json`; emits per-strat `videos` (primary) + `clips` (ALL timed cams via `strat_clips`, for band resolution); `SEED_VERSION` gates reconcile (now 4); re-run to refresh. Bowser keys map three ways — `<n>n`→pipe segment (No Reds), `<n>x`→fight segment (Battle), `<n>r`→the COURSE'S 8-red-coin star (`_BOWSER_REDS`, star:16/17/18:0; missing this dropped every reds ladder). `tests/test_scrape_ranks.py` pins the bundled seed's nine-Bowser-target coverage — a mapping hole is invisible in unit tests, only the OUTPUT shows it |

Seed-fix rule: editing a seeded value only fixes FRESH installs — existing dbs
need a guarded repair migration too (memory `seed-fix-needs-repair-migration`).
