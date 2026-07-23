# Average Rank Mode — Design

Date: 2026-07-23 · Status: approved-pending-user-spec-review

## Problem

Ranks today grade a single time: the saved per-strategy PB. Crossing a
threshold ONCE locks the rank in, which rewards a lucky outlier rather than
consistency. The user wants selectable rank modes that grade an AVERAGE of
valid runs instead, switchable everywhere, at any time.

## Prior Art

- **WCA speedcubing averages (Ao5/Ao12)** — the canonical "rolling average of
  your last N attempts" graded against fixed standards. WCA uses a trimmed
  mean (drop best + worst); we deliberately use the plain mean the user
  specified. The "last N" framing and the motivation (consistency over
  one-off luck) transfer directly.
- **LiveSplit "Average Segments" comparison** — mean over full attempt
  history as a first-class alternative to PB comparison; our Lifetime mode.
- **In-repo**: route `avg_rank` (tracking/views.py) already averages RANK
  SCORES nearest-tier for route steps; per-strategy PB grading
  (`_strat_rank`) is THE single grading path. Both patterns are reused, not
  replaced.
- The **Best-N** variant (mean of your N fastest ever) is the user's own
  addition — labeled novel-with-justification: more motivating because every
  new good run can only improve it (monotone, like a sum-of-bests, but
  outlier-resistant because it needs N good runs).

## Semantics

### Modes (THE registry — one row per mode)

| key        | UI label | grades                                             |
|------------|----------|----------------------------------------------------|
| `pb`       | PB       | the saved per-strategy PB row (today's behavior)   |
| `avg10`    | Avg 10   | mean of the LAST 10 valid runs (chronological)     |
| `avg50`    | Avg 50   | mean of the LAST 50 valid runs                     |
| `best10`   | Best 10  | mean of the 10 FASTEST valid runs ever             |
| `best50`   | Best 50  | mean of the 50 FASTEST valid runs ever             |
| `lifetime` | Lifetime | mean of ALL valid runs                             |

One global setting `rank_mode` (default `pb`), persisted server-side in the
`ui_state` KV — browser tab and desktop GUI stay in sync (parity rule).
Registry lives in `ranks/classify.py`: key → `{label, window, order}` with
`order ∈ {recent, top}`; adding a future mode (e.g. Best 25) is one row.

### Valid run

An attempt counts toward an average iff ALL of:

- `outcome == "success"`;
- not cleared — neither manually purged nor auto-ignored (`cleared` False,
  which already encodes the time-filter auto-ignores);
- `strat_tag` equals the strategy being graded (per-strategy ranking rule:
  runs on another strat never count);
- it has a time on the grading clock (`igt_frames`/`rta_frames` not None),
  excluding the known junk rta==0 reset-race rows on the rta clock.

### Averaging + grading

- "Last N" = the N most recent valid runs by attempt order (journal ids are
  chronological). "Best N" = the N fastest valid runs ever.
- Fewer than N valid runs → mean of what exists; the banner reports the
  count (e.g. "avg of 4/10"). Note: under N, Avg-N, Best-N and Lifetime all
  coincide (each is the mean of every valid run).
- Zero valid runs → unranked sentinel (see UI).
- The mean is computed in frames, rounded, and graded via the existing
  `display_cs` → `rank_for` path, so the rank always agrees with the average
  time displayed next to it.
- Clock per display site mirrors today's grading exactly: section banner =
  the view clock; quick-select star grid + route star candidates = igt;
  segments = rta everywhere.
- `pb` mode is byte-for-byte today's behavior: it grades the saved
  per-strategy PB row (`pbs_by_strat` / `db.current_pb`), NOT attempt
  history. Avg modes grade attempt history directly — a run never saved as
  PB still counts.

## Architecture

### ranks/classify.py (pure)

- `RANK_MODES` registry as above (+ tuple of keys for validation).
- `average_frames(frames_list, window, order) -> tuple[int, int] | None` —
  (mean_frames, count_used) or None on empty input. Pure; no attempt
  knowledge.

### tracking/views.py

- One shared resolver `_grading_basis(mode, pb, history, strat, clock)` →
  `{"frames": int, "count": int, "window": int|None} | None`, applying the
  valid-run filter. In `pb` mode it simply wraps the pb row's frames
  (count 1, window None; None when there is no pb row) so callers have ONE
  shape whatever the mode. `_strat_rank`, `_section_banner`, and `_candidate_rank`
  all route through it (attempt histories are already loaded at every call
  site, including `build_route_view`). `_attempt_rank` (per-attempt medals:
  attempt rows, progress-graph dots) is UNTOUCHED — those grade one run.
- Section banner payload gains `"mode"` and, in avg modes, `"basis":
  {"display", "frames", "count", "window"}`. `band()`'s next-tier gap/fill
  now measures from the average in avg modes (no change to band itself).
- Session view payload gains top-level `"rank_mode"`.
- `rank_by_star` and route step ranks/`avg_rank` update automatically —
  they already render server-graded ranks.

### tracking/service.py + server/ranks_api.py

- `service.set_rank_mode(mode)`: validate against the registry (invalid →
  ValueError → HTTP 409), `db.set_state("rank_mode", mode)`, broadcast
  `rank_mode_changed` (broadcast-only, like `rank_standards_changed`).
- REST: `PUT /api/ranks/mode` body `{"mode": "avg10"}`. Reads come free via
  the session view. Unknown stored value reads back as `pb` (forward-safe).
- Not journaled: display preference, not history.

### UI

- `ui/components/header.js`: `Rank:` dropdown next to the Clock selector,
  options mirrored from the registry (PB / Avg 10 / Avg 50 / Best 10 /
  Best 50 / Lifetime), value from `view.rank_mode`; onchange PUT + refresh.
- `ui/store.js`: refetch the view on `rank_mode_changed` (second window /
  GUI follows).
- `ui/components/ranks.js` RankBanner: in avg modes a small basis line —
  "avg of 7 runs · 1:23.45", "avg of 4/10 · …" under the window, "best 10
  avg · …" for top-order modes — and the unranked sentinel reads "no valid
  runs on this strategy yet" (vs PB mode's "no PB on this strategy yet").
- Medal rendering everywhere else is unchanged (server sends the rank).

## Non-goals

- Per-attempt medals stay per-run (entity-level displays only switch).
- No trimmed mean, no per-entity/per-strat mode override, no journaling,
  no persistence migration (ui_state KV suffices).
- Route `avg_rank` formula unchanged (nearest-tier mean of step ranks —
  the step ranks themselves are now mode-graded).

## Error handling

- Invalid mode on PUT → 409 (ValueError taxonomy, same as ranks_api today).
- Corrupt/unknown stored `rank_mode` → treated as `pb` on read.
- Empty ladders / no strat / no standards: existing sentinel reasons
  (`no_strat`, `no_ladder`) unchanged; `unranked` now also covers "no valid
  runs" in avg modes.

## Testing

- Pure: `average_frames` windowing (recent vs top order), under-N, empty;
  registry completeness (every mode has label/window/order).
- Views: valid-run filter (cleared / failed / other-strat / timeless /
  rta==0 race rows excluded); banner rank + basis under each mode;
  `rank_by_star` and route candidate ranks switch with the mode; `pb` mode
  payload identical to today (regression); unranked sentinel per mode.
- API: PUT validates (409 on junk), persists, broadcasts; view carries
  `rank_mode`.
