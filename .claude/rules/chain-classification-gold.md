---
paths:
  - "src/sm64_events/tracking/service.py"
  - "src/sm64_events/tracking/views.py"
  - "src/sm64_events/storage/db.py"
  - "src/sm64_events/ui/store.js"
  - "src/sm64_events/ui/components/practicelog.js"
  - "src/sm64_events/ui/components/ranks.js"
  - "src/sm64_events/ui/rankclimb.js"
---
# Chain: a strategy classification earns one GOLD and its celebration

- **Value:** the fastest eligible newly classified attempt, its destination PB
  comparison, and one live receipt carrying the destination's prior rank.
- **Source truth:** classification IDs and the destination baseline captured
  before retagging in `TrackerService`; `tests/test_strategy_auto_pb.py` probes
  both first selection and the newest-attempt correction.
- **Sink:** saved GOLD and the existing Practice rank climb/celebration.
- **One clock:** the requested star clock; segments always use RTA. Rank mode
  and current effective game version must match the live receipt's baseline.

| # | hop | value is true here as | module | probe (reads it) | inject (forces it) | when the hop is broken, the probe shows | when the probe itself is broken, it shows |
|---|-----|-----------------------|--------|------------------|--------------------|------------------------------------------|--------------------------------------------|
| 1 | classification and destination comparison | eligible IDs, prior destination PBs and rank | `src/sm64_events/tracking/service.py`, `src/sm64_events/tracking/views.py` | `tests/test_strategy_auto_pb.py` | seed attempts and a destination PB as those tests do | a failed/grab-only row wins, a tie saves, or the wrong clock decides | no classified rows or a fixture whose destination has no prior PB |
| 2 | saved winner and historical order | canonical PB plus replay callback; migrated PB reasserted without duplicate history | `src/sm64_events/storage/db.py`, `src/sm64_events/tracking/service.py` | saved-latest and migrated-winner cases in `tests/test_strategy_auto_pb.py` | save the source and destination PBs in the test's explicit order | destination winner disappears, duplicate save, or Undo loses history | fixture never saved both strategies, so migration is untested |
| 3 | live reward receipt | `pb_saved.payload.classification`, local nonce, current entity/measurement | `src/sm64_events/ui/store.js`, `src/sm64_events/ui/components/practicelog.js` | `tests/test_ui_classification_pb.py` | dispatch a live classification PB and strategy/settings changes in the hook harness | receipt survives a context round trip or mismatched PB starts a climb | selector-only test omits the event feed and cannot test retirement |
| 4 | first actual banner reveal | receipt claimed once; destination baseline supplied to existing climb | `src/sm64_events/ui/components/ranks.js`, `src/sm64_events/ui/rankclimb.js` | `tests/test_ui_strategy_auto_gold.py` | shipping strategy picker and prior-session target picker on `serve_ui_live` | first mount snaps, later mount replays, or source strategy's rank animates | test never opens the body banner or waits only for its container |

## Counterfactual recipe

Run `uv run python tools/run_tests.py tests/test_strategy_auto_pb.py
tests/test_ui_classification_pb.py tests/test_ui_strategy_auto_gold.py` in the
actual checkout. Isolate a save failure with seeded service attempts (hops 1-2);
for a feedback failure, dispatch the known classification receipt in the hook
harness (hop 3), then drive the shipping picker and first actual banner reveal
(hop 4). Reload and switch-away/back controls must remain silent. Fixtures use
isolated temporary databases and never attach the live recorder.

## Failure catalogue

- 2026-09-30 hop 2, agent review: a saved latest row migrated after the destination winner could replace it even when slower; preserve/reassert the canonical destination snapshot at the database/service boundary, with Undo coverage.
- 2026-09-30 hop 4, browser verification: prior-session adoption first mounted the banner after GOLD and snapped instead of climbing; claim the shared live receipt at the first actual banner mount, then consume it across remounts.
- 2026-09-30 hop 3, final agent review: a closed body banner left a pending receipt that survived strategy/measurement round trips; retire it in the event feed when its context changes, retaining delayed reveal while context stays current.
