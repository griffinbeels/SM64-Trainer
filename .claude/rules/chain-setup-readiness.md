---
paths:
  - "src/sm64_events/core/capturelayer.py"
  - "src/sm64_events/core/capturelayer_win.py"
  - "src/sm64_events/core/plugin_installation.py"
  - "tools/graphics_diagnostics.py"
  - "src/sm64_events/core/paths.py"
  - "src/sm64_events/core/setup_runtime.py"
  - "src/sm64_events/core/setup_gpu.py"
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
  The GPU route has a separate typed observation: ControlV1 live PID/birth,
  generation/token and actual non-repeat muxed-picture receipt/source epoch.
  Active checks require moving lease acknowledgments and pictures. Deliberate
  GPU idle revokes the lease and the native control worker sleeps, so its exact
  positive receipt plus live matching control/ROM and fresh game/neutral-input
  counters replaces the legacy heartbeat requirement. This exception also needs
  game/input movement on the limited JP path; a live idle process is insufficient.

| # | hop | value is true here as | module | probe (reads it) | inject (forces it) | when the hop is broken, the probe shows | when the probe itself is broken, it shows |
|---|-----|-----------------------|--------|------------------|--------------------|------------------------------------------|--------------------------------------------|
| 1 | Plugin installation and movement | renderer + wrapper files (compared with the bundle by build id), the ini naming the renderer, selection, consent, producer PID and captured-picture receipts | `src/sm64_events/core/capturelayer.py` | GET /api/setup emu installation fields; `tests/test_capturelayer.py` | FakeRegistry, FakeProcesses and a fake GPU observation in that test | Missing/stale renderer or wrapper, an ini wrapping another plugin, wrong selection, or a producer that stops acknowledging | An unchanging fake observation cannot earn active status |
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

- 2026-09-13: a source server's two-second refresh replaced a manually copied
  wrapper with its older bundled DLL before PJ64 reopened. Hash inequality did
  not establish which build was newer. `CaptureLayer` defaults to manual updates;
  only frozen composition opts into the packaged automatic refresh. Source builds
  keep explicit Install and show a differing version without claiming it is newer.
  `test_default_refresh_preserves_a_manually_replaced_wrapper` and the composition
  source/frozen test protect that ownership. The real setup modal test exercises
  manual replacement through the existing API. Packaged refresh also requires
  consent and a last-install hash matching the current disk bytes; an external
  replacement or unknown installation history offers explicit Install instead.
  `plugin_installation.verified_copy` verifies bytes and rolls back failed copies
  and caller settings. Its persisted receipt and log identify the server PID,
  reason, source/destination and before/source/after hashes. The diagnostic's
  `--expected-wrapper` compares candidate, bundle, disk and current PID/birth-bound
  source build separately. Neither build labels nor hashes establish version
  ordering. Older running applications retain their old updater code until closed.

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

- 2026-09-10, hop 1: the manually installed wrapper was active and delivering
  pictures, but its source copy in the trainer build was absent. Setup's
  unavailable state described the missing installer source, not a failed native
  hook. Main now passes the bundle resolver into CaptureLayer, which resolves it
  per operation; restoring or removing the file is recognized without caching
  startup absence. Installation hashes remain required. The offline rendered
  regression uses the real CaptureLayer/API, restores the source while the
  modal is open, and verifies completion without touching emulator settings.
