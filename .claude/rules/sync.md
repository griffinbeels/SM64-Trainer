---
paths:
  - "src/sm64_events/sync/**"
  - "src/sm64_events/memory/layout.py"
  - "src/sm64_events/memory/version_probe.py"
  - "src/sm64_events/server/sync_api.py"
  - "src/sm64_events/ui/sync.html"
  - "src/sm64_events/ui/sync.js"
  - "tools/sync_version.py"
---

# Version sync — JP parity as one probe run, where to change what

The loop (2026-08-15, his design): develop every feature on US; the LAST
step is `uv run python tools/sync_version.py --version jp` with the JP ROM
loaded — it walks every **gate** in dependency order, tells him what to do,
writes each verdict into the **sync report** (`data/version_sync/jp.json`)
as it lands, and the **sync dashboard** (`/ui/sync.html`) fills in live
beside the US column. Then Claude reads the report and promotes verified
addresses into `memory/layout.py` with their evidence. Run on US it is the
non-regression proof: every gate must read `verified` — except the few
marked `optional=True` (a screenshot-scored constant, the Bowser 3 grand star,
the diagnostics-only section counter), which the summary lists by name and
the exit code ignores.

| To change... | Edit |
|---|---|
| Add a RAM address the tracker reads | ONE row in `memory/layout.py::LAYOUT_ROWS` + the field on `Layout` + the US value in `US` (+ the pin in `tests/test_layout_us.py`) **and** an `address.<field>` gate in `sync/address_gates.py` — `tests/test_gates_cover.py` is red until both exist. Derivation: a decomp symbol (then `data/symbols_<v>.tsv` gives every version a candidate — regenerate with `tools/import_stroop_maps.py <MappingUS.map> <MappingJP.map>`) or a hunt (Usamune's own globals) |
| Add a detector / an event kind | its `feature.<type>` gate in `sync/feature_gates.py` (`test_gates_cover.py` scans `type="..."` in `detectors/*.py`) — the check runs the REAL chain through `sync/stack.py::DetectorRun` and waits for the event with the payload he confirms |
| Add or move a measured constant (display tick, a window, a threshold) | a `cal.*` gate in `sync/calibration_gates.py` with `backs="<dotted.name>"` — the check imports the constant, never restates it; a JP number that disagrees is a `failed` verdict carrying `measured` (both sides), and only THEN does that constant grow a version |
| A test that stubs the registry | `sync.gates.GATES` is module-global: save/clear/restore it in a fixture (`tests/test_sync_gates_contract.py`'s shape) AND import `sync.registry` at the top of the module first — a lazy `import registry` inside the router re-registered every real gate on top of a fixture's stubs and every id collided (2026-08-15, both dashboard test modules). The 850px guard in `tests/test_ui_sync_page.py` renders the REAL registry (captured before the fixture clears it) because the overflow it guards came from real labels |
| The gate contract itself | `sync/gates.py` (Gate, Verdict, FEATURES, register, ordered) — `sync/registry.py` imports every gate module; add a module = one line there |
| Verdict functions (tick rate, the exact-value hunt, action edges) | `sync/checks.py` — pure, tested without an emulator; the gate modules do the reading and prompting |
| The runner (walk order, skipping on unmet needs, the working layout, posting to the server) | `sync/runner.py`; the CLI is `tools/sync_version.py` (`--version`, `--only <gate-id \| feature>`, `--server`, `--timeout`). It refuses when `memory/version_probe.py::detect_version` disagrees with `--version` — a wrong-ROM run would write garbage into the report. Read-only, no instance lock, no recorder lock: safe beside a live session |
| The report file | `sync/report.py` — atomic rewrite per verdict; committed as evidence. `tests/test_layout_matches_report.py` fails when a verified address is not shipped in the layout or a shipped one is refuted |
| The dashboard | `ui/sync.html` + `ui/sync.js` over `GET /api/sync` + the `sync_verdict` WS event (`server/sync_api.py`, mounted unconditionally in `server/app.py`); documented in `docs/api.md` |
| The server boots with a version whose layout is EMPTY (JP today, or AUTO with a JP ROM) | `core/snapshot.py::reader_for` hands the poller an `UnreadyReader`: nothing is read, nothing journaled, `/health.held` and the boot log carry the reason and the fix. It never falls back to reading US addresses off a JP ROM (garbage snapshots would journal garbage events) and never crashes the boot — the two branches (the Game version setting, the version-keyed layout) each did the right thing alone and would have taken the server down together (2026-08-15, `tests/test_boot_with_incomplete_layout.py`) |
| Which ROM is loaded | `memory/version_probe.py` — the cartridge header's country byte (`E` us / `J` jp), read off the ROM image PJ64 holds (`Pj64Memory.rom_header()`); **both byte orders are accepted and which one PJ64 1.6 uses is a live-gate item** (`version.rom`) |

**Promotion is deliberate.** The runner never writes into `layout.py`; a
mis-hunt would ship itself. Claude writes the JP row with the report's
evidence, uppercase hex (the single-source row's tokens are uppercase), and
adds the new address prefixes to `tests/test_single_source.py`'s "a RAM
address" tokens (JP's gMarioObject sits under 0x8035).

**What the research says to expect on JP** (primary sources, 2026-08-15 —
STROOP maps, the decomp's own `#ifdef VERSION_JP`s, Ukikipedia): the warp
logic is identical beyond sounds; the star-dance actions are identical; the
textbox state machine is identical (JP differs in text CONTENT and opens a
sign with B only); **the camera update at the star grab is US-only**
(`interact_star_or_key`), so `cal.star.*` is the calibration most likely to
move; the B1/B2 key cutscene shows a STAR model on JP (`feature.key_grabbed.b1`
asserts no `star_collected` leaks); Bowser's hitbox stays live after the
Grand Star on JP. Detail: `docs/architecture.md` → "Two ROM versions".
