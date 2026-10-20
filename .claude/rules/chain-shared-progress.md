---
paths:
  - "src/sm64_events/tracking/views.py"
  - "src/sm64_events/community/progress_snapshot.py"
  - "src/sm64_events/community/progress_sync.py"
  - "src/sm64_events/server/player_api.py"
  - "src/sm64_events/server/player_comparison.py"
  - "src/sm64_events/ui/components/playerpage.js"
  - "src/sm64_events/ui/components/progresssettings.js"
  - "cloud/community/src/progress-store.js"
---
# Chain: current saved PBs on a claimed player's shared Rank page

Read this before editing the named modules. Trace a reported mismatch to the
first divergent hop; do not patch the displayed number before checking its rows.
Keep practice history, playable ghosts and calibration evidence distinct.

- **Value:** the account owner's current saved PB for each target/strategy/ROM.
- **Source truth:** latest saved PB slots in the owner's database; `latest_pbs_by_strategy` selects current saves, including slower replacement and Undo.
- **Sink:** the shared player's Rank and read-only Scorecard, evaluated in the viewer's requested scope and exclusions.
- **One clock:** stored frames plus clock/ROM identity. Saved, achievement, capture and sync UTC fields retain separate meanings; absent facts remain null.

| # | hop | value is true here as | module | probe (reads it) | inject (forces it) | when the hop is broken, the probe shows | when the probe itself is broken, it shows |
|---|-----|-----------------------|--------|------------------|--------------------|------------------------------------------|--------------------------------------------|
| 1 | saved slots | current per-strategy and per-ROM PB rows | `src/sm64_events/tracking/views.py` | `tests/test_progress_snapshot.py` save/Undo tests | replace or remove a saved row in the test Database | an obsolete best survives a slower save or Undo | missing fixture rows fail the asserted row count |
| 2 | portable projection | schema1 rows, stored frames, raw/effective regions, honest coverage | `src/sm64_events/community/progress_snapshot.py` | `tests/test_progress_snapshot.py` projection and signature tests | project fixture PBs with private/edited/default segments | identities leak, dates change meaning or coverage miscounts | invalid shapes raise rather than becoming zero rows |
| 3 | authorized publication | one revision-fenced current snapshot and content hash | `src/sm64_events/community/progress_sync.py`, `cloud/community/src/progress-store.js` | `tests/test_progress_sync.py`, `cloud/community/test/players.test.js` | use their fenced fake account/store, never live controls | old publisher wins, withdrawn data returns or unchanged content rewrites | unmatched transport routes fail the fixture |
| 4 | public read | validated UUID/hash-bound owner facts | `src/sm64_events/server/player_api.py` | `tests/test_player_api.py` identity and private-state tests | replace fixture public snapshot or claimed header | mixed accounts or private payloads appear | malformed UUID/hash is explicitly refused |
| 5 | scoped comparison | published rows through existing Rank/Scorecard readers; scope-specific completeness | `src/sm64_events/server/player_comparison.py` | `tests/test_player_rank_coverage.py`, `tests/test_player_api.py` | project known public/private fixtures and vary actual ranked membership | unrelated private rows hide rank, or unknown relevant PBs become zero | empty scope or unsupported rows remain explicit in assertions |
| 6 | rendered page | actual ghost time, shared PB Scorecard and valid scoped rank | `src/sm64_events/ui/components/playerpage.js` | `tests/test_ui_player_pages.py`, `tests/frontend/playerpage.test.js` | offline Account fixture over the real app | missing rank, extra comparison time, stale owner or unexpected write | ready selectors time out and unexpected console/API faults fail |

## Counterfactual recipe

Run `python tools/run_tests.py tests/test_player_rank_coverage.py tests/test_player_api.py`.
The same publication can be complete for a public scope and incomplete for a
scope containing a relevant private target. Excluding that target restores the
rank through the canonical scorer. Unsupported rows stay unknown in every scope.
For an actual report, make an online read-only DB backup and run the real readers
on the copy; compare published and local results under the same calibration.
Never initialize another recorder or mutate the user's publication for a probe.

## Failure catalogue

- 2026-10-19 hop5 — "For my profile, it's getting stuck here. Some PBs are unavailable" — publication-wide coverage suppressed all ranks for four unrelated private rows. The actual profile had116ranked members, including8public segments. Portable identity and actual membership now determine scope completeness; the old and new adapters were compared on the same isolated profile data.
- 2026-10-19 hop6 — extra saved-PB comparison numbers confused ghost browsing. Ghost rows now show one actual recording time; PB comparisons stay in the Scorecard. The disabled own Add ghost explanation is a hover/focus tooltip with measured bounds.
