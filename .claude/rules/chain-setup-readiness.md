---
paths:
  - "src/sm64_events/core/capturelayer.py"
  - "src/sm64_events/core/capturelayer_win.py"
  - "src/sm64_events/core/plugin_installation.py"
  - "src/sm64_events/core/plugin_elevation*.py"
  - "src/sm64_events/core/plugin_ownership.py"
  - "src/sm64_events/core/renderer_settings.py"
  - "tools/graphics_diagnostics.py"
  - "src/sm64_events/core/paths.py"
  - "src/sm64_events/core/setup_runtime.py"
  - "src/sm64_events/core/setup_gpu.py"
  - "src/sm64_events/core/setup_profile.py"
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
- **Legacy per-user configuration:** an existing VirtualStore copy of the wrapper
  INI must agree with the physical file. A legacy x86 Project64 can read that
  copy first; physical file identity alone does not prove its effective setting.
  Install and permitted refresh reconcile only this existing owned INI, under the
  original user even when a helper installs protected files as another account.
  Both copies and settings roll back on failure. No shadow is created and no
  repair runs while Project64 is known to be running. The wrapper's configurable
  loader and undo's previous selection remain unchanged.
- **The renderer's settings:** every `GLideN64.ini` Project64 may read (the
  physical file, created when missing; an existing VirtualStore copy, never
  created) loads frame buffer emulation off (`frame_buffer_emulation_off`).
  `core/renderer_settings.py` owns the format and edits one key line by line,
  never through configparser. Install (elevated helper included) and permitted
  refresh write it inside their rollback; status only reads it. A setting that
  is on reads like a stale DLL: refresh fixes it, and after a refused write
  status and the header offer Install, which may elevate. The overlay's
  `renderer_settings` keeps the first earlier value for undo.
- **Setup check, beside readiness:** `core/setup_profile.py` compares the
  emulator's configuration with the tested setup in `data/setup_baseline.json`
  (also the one importance table). It reads the same registry and VirtualStore
  policy, never writes, and says "unknown" without evidence. It never gates
  readiness or completion: the wizard shows its differences as notes.
- **One reader per process:** `setup_runtime.py` attaches one `Pj64Memory` by the
  target's pid and keeps it while that process runs (`alive()`); an observation is
  one header read. A fresh reader per poll re-walked Project64's memory every
  second (38 attaches in one minute of play, 2026-09-28).
- **A proven check holds through a gap:** `onboarding.SteadyChecks` keeps a
  check true until it has been false for 3 s, keyed by Project64 pid, ROM and
  plugin pid; another of any of those proves everything again. A real stall
  therefore reads as not set up after about 6 s (3 s of movement freshness,
  then 3 s of hold). One unreadable header keeps the game it just read.
- **One clock:** server monotonic time bounds fresh evidence to three seconds;
  the browser separately acknowledges progress for about one second. Neither
  polling frequency nor persisted completion can establish current readiness.
  Pictures come from the GPU route's typed observation: ControlV1 live PID/birth,
  generation/token and actual non-repeat muxed-picture receipt/source epoch.
  Active checks require moving lease acknowledgments and pictures. Deliberate
  GPU idle revokes the lease and the native control worker sleeps, so its exact
  positive receipt plus live matching control/ROM and fresh game/neutral-input
  counters stands in for fresh pictures. This exception also needs
  game/input movement on the limited JP path; a live idle process is insufficient.
  An unsupported adapter (`replay/gpuadapter.py`) holds no GPU lease, so the
  recorder's `gpu_unsupported` status lets the desktop's written pictures
  (`pictures_fed`) plus a live control page naming this Project64 and moving
  game/input counters stand in instead. Discovery may establish unsupported
  hardware before GPU demand/channel startup only after a live matching
  GPU-capable control identity is registered, and only when a nonempty complete
  DXGI inventory rules out every adapter. Hybrid/unknown/failed inventories keep
  the actual channel-LUID check. The verdict itself is never picture evidence.
  A proved no-channel startup deadline also permits scoped desktop footage
  (`gpu_fallback`), after native cleanup. The same written-picture/process/game/
  input evidence is required; a deadline alone never completes JP or US setup.

| # | hop | value is true here as | module | probe (reads it) | inject (forces it) | when the hop is broken, the probe shows | when the probe itself is broken, it shows |
|---|-----|-----------------------|--------|------------------|--------------------|------------------------------------------|--------------------------------------------|
| 1 | Plugin installation and movement | renderer + wrapper files (compared with the bundle by build id), the ini naming the renderer, selection, consent, producer PID and captured-picture receipts; the control page's `rom_open` is true only for a practice ROM (`rom_baseline` marks another cartridge) | `src/sm64_events/core/capturelayer.py` | GET /api/setup emu installation fields; `tests/test_capturelayer.py` | FakeRegistry, FakeProcesses and a fake GPU observation in that test | Missing/stale renderer or wrapper, an ini wrapping another plugin, wrong selection, or a producer that stops acknowledging | An unchanging fake observation cannot earn active status |
| 2 | Runtime observation | actual ROM identity and fresh counters belonging to the selected process | `src/sm64_events/core/setup_runtime.py` | GET /api/setup emu target, rom and checks; `tests/test_onboarding.py` | Runtime fakes in that test vary PID, ROM, counters and recorder source | PJ64 without ROM, wrong PID, unqualified desktop fallback or stalled counters remain incomplete | A first counter observation or missing observer stays unverified |
| 3 | Applicable readiness | verification.step, ready and limited | `src/sm64_events/core/onboarding.py` | `tests/test_onboarding.py` readiness cases | Remove one check at a time from observation() | A missing US check prevents completion; JP excludes unsupported tracking only | Making every check false still reports ready, so the probe missed its subject |
| 4 | API and durable outcome | fresh verdict plus separate onboarding record | `src/sm64_events/server/setup_api.py` | GET /api/setup and completion response; `tests/test_onboarding.py` | Inject the observer; POST completion through TestClient | Premature finish returns 409 with no completion record | A test reads an old localStorage value instead of the server record |
| 5 | Revealed page and feedback | current page; the shown step and its setup stage (`setupflow.js` `shownNext`: progress at once, a step back after 2 s); review position; acknowledgment of a stage seen 1.5 s | `src/sm64_events/ui/setupflow.js`, `src/sm64_events/ui/components/setupmodal.js`, `src/sm64_events/ui/components/setupmotion.js`, `src/sm64_events/ui/components/setuppages.js` | `tests/test_ui_setup_modal.py` drives the real Header and modal, records every committed card and samples motion; `tests/test_ui_setup_stages.py` runs the machine poll by poll | serve_ui(capture_layer_status=..., setup_observer=...) with changing dictionaries, or a `PollScript` serving one observation per poll | Future steps appear, Back auto-advances, setup completes on header detection alone, or a one-poll flicker swaps a card | Missing UI lab skips the render; syntax-only checks cannot prove paint or flow |

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

- 2026-10-30, hop 1: "we need to ship the default
  https://github.com/Luna-Project64/GLideN64/releases settings ... MAKE SURE
  THAT FRAME BUFFER EMULATION IS DISABLED. This is very important." The trainer
  never wrote `GLideN64.ini`; a tester's Jabo-only Project64 had none, so
  GLideN64 wrote its compiled defaults (frame buffer emulation on): ghosts did
  not draw and the game lagged (reproduced on his NVIDIA machine). LINK's
  package ini equals those defaults except that key. `test_renderer_settings.py`
  covers the format, install, refresh, VirtualStore, rollback and undo;
  `test_ui_renderer_settings.py` drives the wizard to a working Install.

- 2026-10-24, hop 1: a beta.30 tester: "wait there is 2 graphics plugins now:
  sm64 trainer 1.0 and link v4.2(sm64 trainer)". Project64 lists every DLL in
  `Plugin\`, and the renderer sat there as `GLideN64_SM64Trainer.dll`; picking
  it skips capture (the 2026-09-15 entry below). It now installs as
  `sm64_trainer_gfx.renderer`, which the wrapper loads by full path. Refresh
  and Install move an older install: copy, rewrite both INIs, then delete
  the DLL last, only ours by build id and never while `Graphics Dll` names it.
  `test_capturelayer_one_plugin.py` fails if the renderer is a DLL again or
  the older DLL survives a move.

- 2026-10-22, hop 1: disposable-file reproduction of installation with an older
  VirtualStore wrapper INI returned `installation_verified: true`, left the older
  renderer selected in that shadow, and skipped automatic refresh because physical
  files were current. The friend report contained virtualized plugin logs, but
  omitted the INI contents, so this repairs a demonstrated setup class without
  assigning that machine's ghost failure to it. `test_capturelayer_shadow.py`
  covers install, configuration-only refresh, mismatch status, original-user UAC
  commit/rollback, registry failure, missing shadows, unrelated files and live-game
  protection. Windows' legacy read precedence is documented in
  [User Account Control for Game Developers](https://learn.microsoft.com/en-us/windows/win32/dxtecharts/user-account-control-for-game-developers).

- 2026-10-19, hop 1: denied DLL writes were classified as a running emulator
  merely because the destination stayed unchanged. Explicit install now checks
  sharing/loaded-module evidence and retries eligible access failures through a
  scoped UAC helper while the page stays Installing. The original process owns
  HKCU/settings; helper files roll back before commit, and per-user selection
  restores on failure. A lost final acknowledgment can leave complete DLL files
  without selecting the wrapper; see `docs/error-reporting.md`. Background refresh
  never prompts. `tests/test_plugin_elevation.py`
  covers actual mapped DLLs, denied access, cancellation and privileged rollback;
  `tests/test_ui_reporting.py` covers the uninterrupted pending state. Real UAC
  interaction remains a separate verification boundary.

- 2026-10-17, hop 2: "the Setup Checked for some reason gets detected, then
  undos itself ... We detected JP -- we're done. Move on." Each check needed
  continuous fresh movement, and the recorder's idle-to-active turn restarts
  its picture count: GET /api/setup at 4 Hz showed `pictures` false for 0.6 s
  at each turn, while his first seconds in the game toggled idle four times.
  `SteadyChecks` now holds a proven check through gaps under 3 s;
  `test_a_brief_gap_never_moves_setup_back_but_a_new_game_starts_over` fails
  without the hold.
- 2026-10-17, hop 5, after that fix: "Wizard still is super sketchy, I think
  it's because it's bouncing visually between so many steps?" The install page
  swapped a card, with an acknowledgment, for each of six server steps, and
  swapped back on any step back. It now shows four setup stages; a step back
  shows only after 2 s; only a stage shown 1.5 s is acknowledged; the modal
  paints nothing until the first observation picks its page.
  `test_opening_with_everything_running_shows_one_calm_card` fails without
  the grouping, the hold or that first-observation wait.

- 2026-09-28, hop 1: his server and Project64 started together. The boot
  refresh found no Project64, Project64 then loaded both plugins (its log:
  21:02:58.060), and the copy was refused; the rollback rewrote DLLs it had
  never touched, which Project64 also held, and three tracebacks reached his
  console. A held destination is now the same "not now" as a running
  Project64 (`plugin_installation.in_use` before copying, `DestinationInUse`
  when sharing/loaded-module evidence confirms the refusal): one INFO line, the refresh loop
  retries, and `restore_on_failure` only rewrites files the block changed.
  `test_a_refresh_waits_quietly_while_project64_holds_the_dlls` holds the
  files as a loaded plugin does.
- 2026-09-23, hop 1: every plugin rebuild needed a manual Install with PJ64
  closed, and the frozen app then copied its OLDER bundle over it within two
  seconds of PJ64 closing: all trainers share one overlay, and each read "the
  installed DLL is mine" from the same hash. Installs now record their
  installer (frozen app, or a checkout's folder) and build (link time, build
  id); a trainer refreshes only an installer-like-it's install and never to
  an older link time, and only frozen installs write the legacy
  `installed_sha256` older apps trust (`core/plugin_ownership.py`). This
  supersedes the source-checkouts-never-refresh rule below.
  `test_only_an_installer_like_this_one_refreshes_its_install` and
  `test_a_refresh_never_replaces_a_newer_build` cover the pairings.

- 2026-09-17, hop 5: "Looks like it stalls on the 'setup checked' screen. It
  should automatically continue once we've successfully confirmed the setup."
  Readiness was true on the server; the wizard waited for `document.hasFocus()`
  before its one-second acknowledgment, and he plays in Project64 with the
  trainer beside it, so the trainer never had focus. Headless tests always have
  focus, so none saw it. `useSetupAttention` now waits only while the page is
  hidden; `test_confirmed_setup_continues_while_visible_and_waits_while_hidden`
  fails on the focus rule.
- 2026-09-15, hop 1: "this candidate doesn't have any stuttering as far as I
  can tell", yet every picture read "Input unavailable". Project64's graphics
  plugin pointed at the renderer DLL directly, so the wrapper never loaded (its
  build wrote no log line) and the recorder photographed the desktop: 1313
  rows, 0 exact. The setup screen now installs the pair and selects the
  wrapper, which names the renderer in `sm64_trainer_gfx.ini`.
- 2026-09-15, hop 1: a rebuilt candidate read "differs from this build".
  Setup compared bytes and link timestamps differ between builds; a plain
  64-hex pattern also matched the renderer's decimal tables, and scanning the
  renderer cost 178 ms every 2 s. Setup compares the embedded build id with its
  role suffix, cached per file stat (`capturelayer._files_match`).

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
  Setup while already AFK. Active stalls still fail. Since the CPU path's
  deletion the receipt is the GPU observation's: `test_setup_gpu.py` covers
  the idle receipt against nine missing-evidence faults and the JP limitation.

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
