---
paths:
  - "src/sm64_events/core/capturelayer.py"
  - "src/sm64_events/core/capturelayer_win.py"
  - "src/sm64_events/core/paths.py"
  - "src/sm64_events/core/setup_runtime.py"
  - "src/sm64_events/core/onboarding.py"
  - "src/sm64_events/server/setup_api.py"
  - "src/sm64_events/ui/setupflow.js"
  - "src/sm64_events/ui/components/setupmodal.js"
  - "src/sm64_events/ui/components/setupmotion.js"
  - "src/sm64_events/ui/components/setuppages.js"
---
# Chain: installation evidence to the onboarding completion screen

- **Value:** whether the available setup is installed and currently working.
- **Source truth:** verified Project64 target, loaded cartridge header, plugin PID
  and moving picture/input/game counters. A grading preference is not detection.
- **Sink:** Settings → Setup and the first-run wizard's completion page.
- **Installation reuse:** the physical wrapper hash and INI/registry configuration
  qualify an existing installation, independently of a consent timestamp. Its
  folder/installation record is shared per Windows user, while gameplay readiness
  and checkout-local completion are separate. A matching install suppresses first use.
- **One clock:** server monotonic time bounds fresh evidence to three seconds;
  the browser separately acknowledges progress for about one second. Neither
  polling frequency nor persisted completion can establish current readiness.
  During intentional recorder idle, a positive decoded-picture receipt from the
  current source's original plugin PID substitutes for fresh picture delivery.
  Heartbeat, ROM, game and neutral input sampling must still be live. The receipt
  cannot qualify a stopped recorder, desktop fallback, or another plugin PID.

| # | hop | value is true here as | module | probe (reads it) | inject (forces it) | when the hop is broken, the probe shows | when the probe itself is broken, it shows |
|---|-----|-----------------------|--------|------------------|--------------------|------------------------------------------|--------------------------------------------|
| 1 | Plugin installation and movement | wrapper files/selection, consent, plugin PID, recent heartbeat and picture sequence | `src/sm64_events/core/capturelayer.py` | GET /api/setup emu installation fields; `tests/test_capturelayer.py` | FakeRegistry, FakeProcesses and stream_header in that test | Missing/stale wrapper, wrong selection, or frozen stream | An unchanging fake heartbeat cannot earn active status |
| 2 | Runtime observation | actual ROM identity and fresh counters belonging to the selected process | `src/sm64_events/core/setup_runtime.py` | GET /api/setup emu target, rom and checks; `tests/test_onboarding.py` | Runtime fakes in that test vary PID, ROM, counters and recorder source | PJ64 without ROM, wrong PID, desktop fallback or stalled counters remain incomplete | A first counter observation or missing observer stays unverified |
| 3 | Applicable readiness | verification.step, ready and limited | `src/sm64_events/core/onboarding.py` | `tests/test_onboarding.py` readiness cases | Remove one check at a time from observation() | A missing US check prevents completion; JP excludes unsupported tracking only | Making every check false still reports ready, so the probe missed its subject |
| 4 | API and durable outcome | fresh verdict plus separate onboarding record | `src/sm64_events/server/setup_api.py` | GET /api/setup and completion response; `tests/test_onboarding.py` | Inject the observer; POST completion through TestClient | Premature finish returns 409 with no completion record | A test reads an old localStorage value instead of the server record |
| 5 | Revealed page and feedback | current page/substep, review position, visible acknowledgment | `src/sm64_events/ui/setupflow.js`, `src/sm64_events/ui/components/setupmodal.js`, `src/sm64_events/ui/components/setupmotion.js`, `src/sm64_events/ui/components/setuppages.js` | `tests/test_ui_setup_modal.py` drives the real Header and modal and samples motion | serve_ui(capture_layer_status=..., setup_observer=...) with changing dictionaries | Future steps appear, Back auto-advances, or setup completes on header detection alone | Missing UI lab skips the render; syntax-only checks cannot prove paint or flow |

## Counterfactual recipe

Use the offline fixture in `tests/test_ui_setup_modal.py`; never launch a second
live recorder. Hold the UI observation at a supported US ROM with only the plugin
heartbeat true. Completion must stay hidden. Supply pictures, inputs and game
movement, and completion must appear after its visible acknowledgment. If that
works but live setup does not, inspect GET /api/setup upstream checks. Vary one
check/PID/counter at a time through `tests/test_onboarding.py` to locate the first
incorrect transformation. For motion, sample computed positions during a real
click; a declared transition or a settled screenshot alone cannot prove movement.

## Failure catalogue

- 2026-09-07, hop 2: AFK revoked picture demand as designed, then setup rejected
  the stalled delivery counter despite a live plugin, ROM, inputs and game.
  `/api/replay/status` showed idle=true, recording=true and 3348 delivered;
  `/api/setup` had only pictures=false. Setup now accepts the source's PID-bound
  delivered-picture receipt during intentional idle, including first opening
  Setup while already AFK. Active stalls still fail. `test_onboarding.py` tests
  both entrances plus broken evidence; `test_pluginsource.py` binds the receipt
  to the original producer; the browser test uses the real SetupRuntime and
  checks both AFK success and subsequent heartbeat failure.

- 2026-09-07, hop 2: Project64 was found but rejected while both playing and idle.
  Wermi v7's LINK executable has no Windows VERSIONINFO resource; requiring that
  resource confused compatibility with metadata availability. Exact known-build
  SHA-256 recognition now covers that unversioned binary. Unknown bytes or an
  explicitly unsupported resource version remain rejected. `test_capturelayer_win.py`
  changes a byte to disprove recognition, and the real adapter was checked against
  the same running executable before/after without restarting it. An unresolved
  target now stays on Connect Project64 instead of jumping to installation.

- 2026-09-07, hops 2–3: “It marked as complete & active WHEN I OPENED JUST PJ64.”
  The old UI treated pj64_running and the grading preference as ROM evidence.
  Actual header detection and applicable readiness now own completion.
- 2026-09-07, hop 1: “It glitched back and forth between active / inactive.”
  A heartbeat delta belonged to the previous HTTP read, so readers consumed each
  other's edge. A bounded movement window survives repeated reads and expires
  when the source stalls; a new PID or reset must establish movement again.
- 2026-09-07, hop 1: “We already set this up!!!” A fresh worktree had no local
  consent record even though Project64 already used the matching wrapper. Installation
  recognition now checks physical evidence; shared installation metadata preserves
  discovery while Project64 is closed. Tests vary the hash and missing INI, and
  migrate between two checkout roots without sharing their practice databases.
