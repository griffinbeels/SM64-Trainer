# Version sync — how JP support gets discovered, confirmed and promoted

The tracker reads Super Mario 64's memory. Every RAM address it reads is kept
**per ROM version** in `src/sm64_events/memory/layout.py`: the US column is
what the trainer has read since day one; the JP column starts empty. Nothing
about US changes while JP fills in — the US values are pinned by a test.

Everything the trainer relies on that could differ between ROMs has a **gate**
(`src/sm64_events/sync/`): an address, the behaviour-symbol base, a timing
constant, or a whole detector end to end. `tools/sync_version.py` walks them,
`/ui/sync.html` shows US beside JP, and `data/version_sync/<version>.json` is
the record.

## The first two runs

**1. The US baseline — the proof nothing regressed.** Load the US Usamune
ROM in Project64, start the server from the primary checkout
(`run-test-server.bat`), open `http://127.0.0.1:8066/ui/sync.html` (every row
reads `missing`), then in a second shell:

```
uv run python tools/sync_version.py --version us
```

It prints one gate at a time — what to do (grab a star on the ground, walk
lobby → basement, jump into the BitDW pipe, press the WF blue-coin switch,
beat Bowser 1 …) — and turns the dashboard's US column green as each verdict
lands. Expected: every gate `verified` except the three marked optional (the
screenshot-scored display lag, the Bowser 3 grand star, the diagnostics-only
section counter), which the summary names. Commit `data/version_sync/us.json`.
A `failed` gate on US is a regression: hand Claude the summary.

**2. JP.** Load the JP Usamune ROM, restart the server (Settings → Game
version → JP, or Auto-detect: the poller stays *held* — `/health` says why —
because JP has no verified addresses yet; the dashboard and the runner still
work), then:

```
uv run python tools/sync_version.py --version jp
```

The address gates come first: most have a candidate already (derived from
the decomp's JP symbol map) and only need the live contract; Usamune's own
timers are hunted — the runner asks you to type what the on-screen timer
reads. Then the behaviour base (automatic), then the calibrations (the JP
number measured beside the US constant), then every feature through the real
detector chain. Play through the checklist; the JP column fills live.

Hand Claude `data/version_sync/jp.json` (or just say the run is done). Claude
promotes each verified address into `memory/layout.py` with its evidence — a
test fails while a verified value is unshipped or a shipped one is refuted —
and acts on any calibration that came back `failed`, which is how a JP-only
constant gets its own value. Then rerun `--version jp`: the feature gates
that were skipped now run.

## Iterating after that

- **A new address, detector event or measured constant on US** must have a
  gate or the suite is red (`tests/test_gates_cover.py`). Build the feature
  on US, then `tools/sync_version.py --version jp --only <gate-id | feature>`
  is the last step.
- `--only "star grab"` reruns one feature's gates; the address gates they need
  must already be verified in the report.
- The rule map for the zone: `.claude/rules/sync.md`. What the primary sources
  say to expect on JP (the camera update at the star grab is US-only; the JP
  key cutscene shows a star model; warps and the star dance are identical):
  `docs/architecture.md` → "Two ROM versions".
