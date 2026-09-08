# Living rank calibration

Strategy, target Overall, and route MARELO answer separate questions. Their
shared contract lives in the production modules below; the REST fields are
documented in [the API guide](api.md#ranks--standards).

| Layer | Meaning | Owner |
| --- | --- | --- |
| Strategy | Execution of one named strategy against its own fitted ladder and personal cutoff edits | [library/ladders.py](../src/sm64_events/library/ladders.py), [ranks/standards.py](../src/sm64_events/ranks/standards.py) |
| Target Overall | Absolute time through compatible strategy families and observed community performances | [library/populations.py](../src/sm64_events/library/populations.py), [ranks/overall.py](../src/sm64_events/ranks/overall.py) |
| Route MARELO | Target scores across the route's required slots, including coverage | [ranks/scopes.py](../src/sm64_events/ranks/scopes.py) |

A personal Strategy cutoff does not move generated Overall. Overall pins do
not edit Strategy standards. Resetting Strategy preserves Overall pins;
resetting Overall returns to the current generated curve. The persistence,
region, refresh, and rollback contracts are exercised by
[test_living_rank_standards.py](../tests/test_living_rank_standards.py).

## Evidence and target identity

The population is eligible **Sheet participants**, not all SM64 players or a
measured percentage of learning. Each runner contributes their best compatible
time across the target, and separately within a family. Exact runner IDs, when
present, distinguish people; similar display names do not merge identities.
Malformed or nonpositive times, unsupported regions, and incompatible clocks
cannot supply a score. Unannotated regions follow the effective
`unannotated_region` setting (`both` or `exclude`). Library display visibility
is broader than scoring eligibility and does not choose a time's ROM.

Known families use stable mappings. Unknown rows contribute evidence
immediately; overlapping distributions can join a family, while sparse new
families receive limited influence. Metadata reports family membership,
population counts, estimates, policy revision, and frontier evidence. The elite
frontier refits with compatible observations, including a new valid record.
Proxy evidence remains labeled as estimated and never increases the observed
runner count. A single effective family uses the community model.

Local segment IDs are resolved from definitions and assignments. Policy
selectors use stable star or segment seed identities, never a Sheet row index
or an installation's numeric segment ID. The full HMC detours remain separate:

- `seg:hmc-toad-result`: HMC result through Toad to HMC re-entry.
- `seg:hmc-toad-door`: HMC door through Toad to HMC re-entry.

The supported 16-star presets use result-start. Both targets retain their own
US/JP populations; a pickup-only time measures a different stretch. Bowser reds
star-grab and full reds-to-pipe times likewise grade different Overall targets.
Their legacy strategy storage remains paired with the star; the independent
`scoring_rows` mapping supplies the actual Overall endpoint. See
[the placement regressions](../tests/test_ranking_clock_placement.py).

## Tune a parameter or choose a model

[ranking_policy.json](../src/sm64_events/data/ranking_policy.json) holds shipped
defaults and scoped patches. [RankingPolicy](../src/sm64_events/ranks/policy.py)
validates a partial JSON patch and resolves each layer independently by
increasing selector specificity, then declaration order. A temporary patch
adds to the shipped scoped records, preserving their family mappings.

For a 75% family-milestone / 25% community blend on Caged Island, save this as
`caged-policy.json` and use the comparison command below. Omitting `version`
applies the patch to both supported regions.

```json
{
  "layers": {
    "overall": {
      "patches": [{
        "target_id": "star:2:4",
        "parameters": {"milestone_weight": 0.75}
      }]
    }
  }
}
```

This sets the blend, not fixed rank times; refreshed data still changes the
curve. The family model limits sparse-family influence, and a single family
uses the community fit regardless of this weight. To compare the supported
community-only model, set `parameters` to `{"model": "community"}`. Supported
Overall builders are `community` and `family_milestones`; adding another model
requires a pure builder in `ranks/overall.py`, policy registration/validation,
and model/curve contract tests. Configuration does not execute formula strings.

Personal fixed pins use `RankStandards.set_overall_threshold` or
`PUT /api/ranks/overall/{entity}/{rank}?version=us|jp` with seconds. They live in
the saved standards document's separate `overall_overrides` namespace. Invalid
new pins are rejected. Existing pins survive later refits; the resolver can
adjust unpinned targets or expose a labeled legacy fallback when the saved pins
cannot satisfy current whole-frame spacing. `DELETE /api/ranks/overall/{entity}`
resets both regions; `?version=us` or `jp` resets only that region.

Route aggregation currently remains equal contribution per required slot,
best-K selection within choice groups, zero for unpracticed rankable slots, and
absence for unrankable targets. Coverage and denominator changes can move
MARELO independently of curve changes. Route weights are unsupported: enabling
them requires extending `ranks/scopes.py` and its contracts; changing a policy
weight is not an implemented tuning control.

## Compiled curve and refresh contract

The [CompiledCurve](../src/sm64_events/ranks/curve_types.py) wire value contains
`schema_version`, `interpolation`, full `[displayed_centiseconds, score]`
`nodes`, derived `ladder_cs`, and `metadata`. Generated curves use frame-aware
PCHIP. [ranks/curves.py](../src/sm64_events/ranks/curves.py) and
[ui/timecurve.js](../src/sm64_events/ui/timecurve.js) evaluate the same value.
The browser does not fit populations. Eight display cutoffs cannot reconstruct
the generated curve. Explicit `legacy` payloads retain legacy ladder semantics;
an empty curve is unrankable. Unsupported or malformed payloads fail explicitly.

Consumers use `resolve_curve` and `curves.progress_for_time`; inverse division
goals use `curves.time_for_score` so displayed goals earn their stated score.
Python/browser parity and attained-goal coverage live in
[test_rank_curves.py](../tests/test_rank_curves.py),
[test_cross_language_parity.py](../tests/test_cross_language_parity.py), and
[test_overall_model_js.py](../tests/test_overall_model_js.py).

`library.calibration.prepare` builds a detached complete generation containing
observations, display/import assignments, scoring assignments, fitted Strategy
layers, Overall curves, stable identities, and policy revision. Library refresh
prepares and validates that generation, writes the prepared Sheet snapshot, and
publishes it once. A same-date observation correction or policy change can
activate; identical content can be a no-op, and older Sheet data is refused.
Fitting or write failure retains the active generation and saved snapshot.
Assignment transactions also restore saved assignment bytes on failure.

GET readers pin one generation and standards state; the API exposes
`X-Rank-Calibration`, while rank payloads carry `calibration_revision`. The board
cache includes source content and the effective calibration revision. A Sheet
timestamp or seed version alone is insufficient. On restart, the store rebuilds
from the saved/bundled observations and current saved configuration. Relevant
failure and reader tests are in
[test_library_calibration_store.py](../tests/test_library_calibration_store.py).

Successful refresh activates automatically, with no publish approval or season
boundary. Regrading updates celebration watermarks without awarding a PB or an
earned rank-up. Saved times and attribution remain unchanged.

History retains its existing meaning: saved performances evaluated against
**current standards and current route membership**. PB mode uses the latest
deliberately saved PB per strategy, original ROM, and clock, including a slower
saved replacement. Average windows keep separate entity/strategy/ROM/clock
buckets. Imported attempts retain their recorded ROM; older attempts without
that context use the selected grading region. The optional `context_scorer`
in `history_series` carries event context while preserving two-argument scorer
callers. See [test_overall_consumers.py](../tests/test_overall_consumers.py).

## Compare before changing shipped tuning

```powershell
python tools/compare_rank_calibrations.py --output comparison.json
python tools/compare_rank_calibrations.py --policy caged-policy.json --format md --output comparison.md
```

The first compares legacy fastest-strategy-envelope scoring with production
Overall on the same compatible observations. `--policy` compares shipped
Overall with the temporary patch. Neither command changes policy or source
observations. `--sheet`, `--defaults`, and `--standards` accept supplied files;
defaults use the bundled public data and a disposable seeded database.
`--regions us jp` selects regions and `--frame-samples 129` controls the bounded
broad sample grid. Output cannot overwrite an input; the JSON records source
file hashes and verifies source observations remain unchanged.

The JSON report includes full curves, families/counts/provenance, division
targets, sampled per-frame gains, the named 13.86 Caged example when present,
and actual complete/partial runner portfolios for every supported 16-star
preset. It includes all 16 collected stars plus rankable segments, preserves
real scope groups/denominators, and separates common-slot score changes from
coverage changes. Remaining targets receive an explicitly labeled sweep.
Per-frame sampling is bounded, not exhaustive. The tool is a read-only tuning
comparison; runtime refresh does not wait for this report.
