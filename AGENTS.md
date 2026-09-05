# SM64 Trainer — agent guide (Codex and any non-Claude harness)

**Read [`CLAUDE.md`](CLAUDE.md) now. It is the guide.** Everything that was
duplicated here — the module map, the domain rules, the parallel-work zones,
the definition of done — lives there and in `.claude/rules/*.md`, and this file
carries no rules of its own.

## Why this file is four paragraphs instead of two hundred

This was a full hand-synced copy of CLAUDE.md from 2026-07-24 to 2026-07-28.
By the end it was missing *every* dev-process rule CLAUDE.md had gained in
those four days — render-verify for UI work, exit-code honesty, the
one-door/single-source law, star↔segment parity, the route-step-order contract
— plus domain rules 11 and 12, plus `tools/dev_cleanup.py`. A Codex session
reading it was working to a four-day-old standard and had no way to know.

Nothing about the two harnesses justifies two documents. Codex can read
`CLAUDE.md`; what it cannot do is auto-load a `paths:`-scoped rule file the way
Claude Code does, so **that one difference is what this file exists to cover** —
the table below is the manual version of that mechanism.

## Read the rule file for the zone you are touching

Claude Code injects these automatically when a matching file is opened. You
have to open them yourself. Read the one for your zone *before* editing, not
after:

| Zone | Dirs (under `src/sm64_events/` unless noted) | Read |
|---|---|---|
| Memory reads, detectors, event recipes | `memory/`, `detectors/`, `core/snapshot.py`, `core/events.py`, `tools/find_timer.py`, `tools/hunt_value.py` | `.claude/rules/memory-detectors.md` |
| Version sync — the per-ROM memory layout, behaviour symbols, gates, the sync runner + report, the dashboard | `memory/layout.py`, `memory/behaviours.py`, `memory/version_probe.py`, `sync/`, `server/sync_api.py`, `ui/sync.*`, `tools/sync_version.py` | `.claude/rules/sync.md` |
| Tracking, storage, stats, routes/runs/segments, defaults corpus | `tracking/`, `storage/`, `stats/`, `data/`, `tools/corpus_*.py`, `tools/build_defaults_seed.py` | `.claude/rules/tracking-storage.md` |
| **Imported times** — an attempt he brought rather than played: three doors (by hand, an Ultimate Sheet column, a link to his own sheet) onto one back room; a paste and a LiveSplit door are backlog task 0103, last carried by commit `d70721b9` | `tracking/importing.py`, `tracking/import_names.py`, `library/import_runner.py`, `library/sheet_link.py`, `server/import_api.py`, `ui/components/addtime.js`, `ui/components/import*.js` | `.claude/rules/import.md` |
| The world-graph rules a movement is judged against (topological cancels, resurrection) | `tracking/topology.py`, `tracking/segments.py`, `tools/measure_topology_cancels.py`, `tools/why_cancelled.py`, `tools/topology_map.py` | `.claude/rules/segment-topology.md` |
| When a segment's clock STARTS, and what number it records when it stops | `tracking/segments.py`, `detectors/igt_clock.py`, `detectors/counter_epoch.py` | `.claude/rules/segment-clock.md` |
| The star grab's time drawn hop by hop, so a report is diagnosed not guessed | `detectors/igt_clock.py`, `detectors/star_grab.py`, `tracking/segments.py` | `.claude/rules/chain-star-grab-time.md` |
| The segment recorder — the journal read back as pointable sentences | `tracking/eventlabel.py`, `tracking/synthesize.py`, `ui/components/segmenttimeline.js` | `.claude/rules/recorder.md` |
| Server, REST/WS APIs, wiring, paths | `server/`, `main.py`, `core/paths.py`, `core/logging_setup.py` | `.claude/rules/server.md` |
| UI — always `ui-core.md`, plus the narrowest that matches | `ui/`, `links.py`, `tests/test_ui_*.py` | `.claude/rules/ui-core.md` **and** one of `ui-selector.md` / `ui-practice.md` / `ui-ranks.md` / `ui-scorecard.md` / `ui-climb.md` / `replay-compare.md` |
| Replay capture/encode/extract, compare, compilation | `replay/`, `compare/`, `core/recorder_lock.py` | `.claude/rules/replay-compare.md` |
| Desktop shell, self-update, build, release | `desktop/`, `bootstrap/`, `core/update*`, `tools/build_exe.py`, `tools/release.py` | `.claude/rules/desktop-update-release.md` |
| Ranks (classify, standards, scraper) | `ranks/`, `tools/scrape_ranks.py` | `.claude/rules/ranks.md` |
| The Ultimate Sheet library (read, classify, map, snapshot) | `library/`, `tools/scrape_sheet.py` | `.claude/rules/library.md` |
| The 100-coin star (its exit-star variants, and why one reset is one row) | `tracking/hundred_coin.py`, `ranks/standards.py` | `.claude/rules/hundred-coin.md` |
| What a saved time MEANS, what a row may DO about a PB, which strategy is ACTIVE | `tracking/caveats.py`, `tracking/pbaction.py`, `tracking/activestrat.py` | `.claude/rules/pb-strategy.md` |

Tests mirror modules: `tests/test_<module>.py` — read the test file first, it
is the executable spec.

## Hooks

`.codex/hooks.json` runs the same scripts as `.claude/settings.json`, from
`.claude/hooks/`. There is no `.codex/hooks/` directory; there was one, and it
missed `no-app-server.py` — the guard that stops an agent seizing the recorder
lock during a live practice session — for two days. `tests/test_agent_config_parity.py`
fails if the two harnesses stop running the same set, or if this file grows
rules of its own again. **Since 2026-09-04 the Codex file is generated, never
hand-written:** `python ~/.claude/harness/install.py --repo .` renders it from
`.claude/settings.json` (the harness repo owns the renderer), and the parity
test fails when the committed file differs from what the settings generate.
Edit the settings; regenerate; commit both.
