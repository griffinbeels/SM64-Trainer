---
paths:
  - "src/sm64_events/ui/**"
  - "tests/test_ui_*.py"
  - "tests/test_star_icons.py"
  - "tests/source_scan.py"
---

# UI layer — the always-relevant half

This file loads for ANY file under `ui/`. It holds the shell, the shared
primitives, and the verification norms — the things true of every UI change.

Full detail, evidence and history: [docs/rules/ui-core.md](../../docs/rules/ui-core.md) — read it when you change the shell, a shared primitive, the card-set exchange or a responsive layout and need the reason behind a rule; the verification detail is in [docs/ui-verification.md](../../docs/ui-verification.md).

**The per-feature detail is in narrower files, which load only when you
open a file they own:**

| If you are touching... | Also loaded |
|---|---|
| practice / stage banner / pickers / segments / routes / runs / strategies / graphs | `.claude/rules/ui-practice.md` |
| rank icons + caps, banners, the Rank tab, the MARELO pill | `.claude/rules/ui-ranks.md` |
| the Scorecard card + its goal picker | `.claude/rules/ui-scorecard.md` |
| celebrations, the level-up climb, the tuning inspector | `.claude/rules/ui-climb.md` |
| replay player, compare, failure compilation | `.claude/rules/replay-compare.md` (same file as the backend zone) |

Keep the split: `tests/test_rule_files.py` proves the budget and that every
`paths:` glob still matches a real file.

## The layer itself

`ui/index.html` is served per request: edit + refresh, no restart.
Components/store/API: `ui/components/` · `ui/store.js` · `ui/api.js` ·
`ui/app.js`; vendored Preact in `ui/vendor/`. Formatting: `ui/format.js` —
**Every displayed time is bare** (his rule, 2026-09-24: "0'11"43 should be
11"43, while 1'11"43 is correct"): `fmtIgtShort`/`fmtSeconds`, and
`core/timefmt.py::format_igt_short` for server text. **`fmtIgt`/`format_igt`
keep `0'` and are identifiers only** (clip filenames, journaled payloads;
`tests/test_bare_times.py` holds the list);
format a rank standard (stored in seconds) with `fmtSeconds`, never via
frames. Pinned by `tests/test_ui_time_format.py` and
`tests/test_cross_language_parity.py`. Design system: the one CSS block in
index.html; design contract in `.claude/skills/sm64-uiux/`.
Detail: [docs/rules/ui-core.md#the-layer-itself](../../docs/rules/ui-core.md#the-layer-itself).

## Shell and shared primitives

Detail for every row: [docs/rules/ui-core.md#shell-and-shared-primitives](../../docs/rules/ui-core.md#shell-and-shared-primitives).

| To change... | Edit |
|---|---|
| Library grouping (routes + segments) | `ui/group.js::buildTree` (the engine) + `ui/components/grouplist.js` (`GroupedList`, `useOpenGroups`); consumers supply only policy + row renderer. Indent via DOM nesting and the ONE compounding `.lib-group` rule, never a `--depth` property; a child always indents one level. **A new consumer MUST use a new localStorage open-set key.** Rows indent by margin with `width: auto`, never `width: 100%` |
| Live event feed | `ui/components/feed.js` |
| Refetching the session view — after a WebSocket event OR after a component's own write | `ui/store.js` + `ui/coalesce.js` — ONE door: every `REFRESH_ON` event and `t.refresh()` goes through the coalescer (one run in flight; a mid-run request settles with the follow-up). **Components never hold the raw `refresh`** (mount, socket-open, never-loaded retry only). Guards: `tests/test_ui_coalesce.py`, `tests/test_ui_strat_pick_settles.py::test_a_pick_never_runs_two_view_fetches_at_once` |
| What the page DREW, recorded for a live report | `ui/uilog.js` — `useUiLog(rootRef, readers)` reads the RENDERED DOM (never the store) and POSTs `/api/uilog`. **A new surface is three edits: reader, call site, and `core/uilog.py::SURFACES`** (an unknown surface is dropped silently). Dedupe on the rendered snapshot; posts serialised, never debounced. Pinned by `tests/test_ui_log_selectors.py`, `tests/test_ui_log_records_the_real_page.py` |
| Settings → Game → Game version | `ui/components/header.js` — one `select` (Auto-detect / JP / US — "JP", never "Japan") over `GET|PUT /api/mode`; applies live, no restart copy; note names the graded version, "unsupported" for emulator + explicit JP |
| Top control bar | `ui/components/header.js` — three cards (session / `RouteRankCard` / **Grading**) + settings drawer; the clock select lives in the drawer; armed state shows only on the Practice tab. Slot cell `.marelo-slot` carries `container-type: inline-size`. **Every context card is ONE hit target** via `ui/components/contextselect.js` (`CardSelect`/`ContextSelect`, used by all three cards): `CardSelect` returns a fragment (the `.context-select > select` child combinator); hide the select with **`opacity: 0`, never transparent colours**; the card wears focus via `:has(> select:focus-visible)`; value + options from ONE `options` list; `title` on the select. Pinned by `tests/test_header_ui.py`. Target picker: `ui/components/targetpicker.js` |
| Dropdown lists (every `<select>` popup) | `ui/index.html` — set `option { background-color }`, **NEVER `option { color }`**; every select needs an opaque dark background of its own; keep `html { scrollbar-color }`. Do not use `appearance: base-select` (the closed control cannot clip its text) |
| Empty "nothing here yet" panels | `ui/components/emptystate.js` — fixed shape; the CALLER owns the words (`AttemptLogEmpty`/`TrendEmpty` in practice.js); "nothing recorded" ≠ "all filtered out" (`hasAttempts`). `pickCast` excludes the previous character by NAME; **pick per mount, never per render**. Attempt timeline has no art (compact note). `CAST` ↔ `ui/assets/empty/*.png` pinned by `tests/test_ui_empty_states.py` |
| Number-animation primitive (plain numbers — a RANK uses the climb, see the row below) | `ui/useTween.js` — `useTween(value, durationMs=700)`, ease-out-cubic, reduced-motion snaps; the ONE tween for numeric surfaces (ranks.js, marelo.js, rankpage.js). **No component rolls its own requestAnimationFrame loop**; `null` passes through, only a later change tweens |
| How a SET of cards changes on screen (the selector's rows) | `ui/exchange.js` (the machine, import-free) + `ui/components/cellrow.js` (`CellRow`, the ONE container every row draws through) + `ui/selectortuning.js` (the numbers) + `/ui/tuneselector.html` (his inspector). **Full detail below: [The card-set exchange](#the-card-set-exchange)** |
| Refetching live data without blanking the page | `ui/refetch.js::useIdentityFetch(identity, staleness, load)` — identity change clears + refetches; staleness bump (`t.mareloRev`) refetches IN PLACE. **A new surface keyed on `mareloRev` uses this hook, never its own effect.** Guard: `tests/test_ui_scorecard.py::test_a_completed_attempt_does_not_blank_the_card_he_is_reading` (per frame) |
| The filterable dropdown popup (every LONG dropdown) | `ui/components/searchselect.js` — `SearchMenu` + `SearchSelect`. Every long dropdown uses it; short ones (a handful of options) stay native selects; entity pickers are not dropdowns. **Only this module may render the `.search-menu` class family** (`tests/test_single_source.py`) |
| Shared modal shell | `ui/components/modal.js` — `Modal({title,onClose,footer,children})`; onClose optional (absent = not dismissable) |
| Update popup | `ui/components/update.js` — modal: version + patch notes (escaped-then-rendered) + GitHub link + exact `download_bytes` + Update/Skip/Later; polls `/api/update/status`. Backdrop/Esc dismiss as "Later" (offer) or "Close" (failed); inert during an active install. Mounted at app root in `app.js` (browser↔GUI parity) |

## The card-set exchange

The user never sees an intermediate set: old options fade away, new ones
appear. Contracts:

- `ui/exchange.js` (`nextState`, `rowStyle`, `phaseMs`, `paintsShown`) stays
  import-free; `tests/test_ui_exchange.py` proves no intermediate set paints.
- **A row paints children only when `paintsShown(id, state)` says their
  identity is shown** — anchor to `shownId`, never to a phase test.
- The fade window is the coalescing window: a further change moves only
  `pendingId`; absorption re-arms the wait, bounded by `maxHoldMs`.
- Two granularities, one owner each: `SurfaceExchange` in `StageBanner` (never
  unmounts) for surface swaps, `CellRow` for cell-set changes;
  `selectorSurfaceId` lives in `stagecontext.js` and stays coarser than "the
  cells changed".
- **The painted subtree is keyed on `shownId`, never on the arriving id.**
- Guard stale content with "once a set has left it is never seen again"
  (`tests/test_ui_one_animation_per_change.py`), not a flash counter.
- `CellRow` is the single door: no row renders `<div class="starrow">` itself;
  it keeps keyless cells. ONE element carries the opacity — no per-cell
  wrapper, no stagger.
- The numbers are his: `ui/selectortuning.js` + `/ui/tuneselector.html`.
  `tests/test_ui_row_exchange_plays.py` drives a real route pick per frame.

Detail: [docs/rules/ui-core.md#the-card-set-exchange](../../docs/rules/ui-core.md#the-card-set-exchange).

## UI verification norms

- Read an instrument's first output on real data before shipping it: drive one
  known input end to end and assert the number; guards assert CONTENT, never
  existence; never print a guarantee you have not tested.
- Assert at the layer the bug lives in — a server fix gets a payload test as
  well as the render test. A reader that can return "nothing" must not look
  like "nothing happened".
- htm collapses whitespace between text and an interpolation/tag: write
  `${" "}`. Never put a backtick inside an `html` template, even in a comment.
- `node --check` only parses; import the module (`node --input-type=module -e
  "import(...)"`) or render the page.
- After any UI change, render the changed state (headless render or the MCP);
  unit tests alone are not evidence.
- `--dump-dom` renders but cannot drive: interact over CDP and collect console
  and exception events. Read after a dispatch in a SEPARATE call; set dependent
  controls one per tick.
- Start every driven run with a known-good CONTROL interaction.
- Fixtures answer the query and encoding the UI actually sends (compare
  unquoted paths; a queried request NEVER falls back to the bare path's
  payload); print every unmatched path. Generate missing fixtures from an online-backup snapshot of
  the live db (pass `ranks=`). Prefer the real `index.html` over captured API
  fixtures to a hand-built harness; never start `sm64_events.main` for it.
- Kill every fixture/http server you start, in the same session.
- Wait for the control you assert on, not its container. Never undo a
  late-arriving state with a one-shot click: repeat until settled and look twice.
- A frame-sampled threshold allows one recorded frame of travel.
- A driven test restores what it changes in the real store (`data/`).
- `subprocess.run(text=True)` needs `encoding="utf-8"`.
- A hand-built harness mounts inside the real stylesheet, `.app-shell` and
  `.practice-page`; check its card width against a real screenshot.
- Measure a wrapping element's clip by removing it and diffing geometry.
  Sweep the width continuum; gate card layout on `@container`, not viewport.
- For a component-supplied custom property, assert the painted
  `getComputedStyle()` value and reject `rgba(0, 0, 0, 0)`.
- Source-scan tests assert on `strip_comments(source)` (`tests/source_scan.py`)
  and are probed both ways (`test_the_guards_can_still_fail`).
- Before debugging a UI report, confirm what code the page is running
  (`fetch('/ui/<file>.js')` for a symbol of the fix).
- Derive a test's example from the corpus so it exercises the mechanism.
- Scope every driven query to your surface's container; hidden tabs stay
  mounted.
- A uilab limit is a bug to fix in uilab; measure a limit before writing it
  down. Use `uilab.trace.record()`, not a hand-rolled sampling loop. Always
  pass `serve_ui()` a `snapshot_db` copy via `db_path=`, never the live path.
- Swapping text is SEQUENTIAL via `climbcurve.js::exchangeFade`, never a
  crossfade; share one pivot across the transition
  (`tests/test_ui_text_exchange.py`).
- A `display:none`d element's `title` is no fallback; move the text onto an
  always-rendered element.

Full norms, incidents and recipes: [docs/ui-verification.md](../../docs/ui-verification.md#ui-verification-norms).

## A `<button>` carries the global rule with it (2026-08-21)

The global `button` rule sets `min-height: 34px; display: inline-flex`; a
button reset to inline text must also state `min-height: 0; display: inline`.
Guard: `tests/test_ui_button_flex.py::test_a_button_reset_to_inline_text_states_its_min_height`.
Detail: [docs/ui-verification.md#a-button-carries-the-global-rule-with-it-2026-08-21](../../docs/ui-verification.md#a-button-carries-the-global-rule-with-it-2026-08-21).

## "Unchanged" is a measurement, not a claim (2026-08-21)

Claim "unchanged" only after diffing `getBoundingClientRect()` against a
worktree at the pre-change commit with its own venv, printing
`module.__file__` to prove which tree answered.
Detail: [docs/ui-verification.md#unchanged-is-a-measurement-not-a-claim-2026-08-21](../../docs/ui-verification.md#unchanged-is-a-measurement-not-a-claim-2026-08-21).

## Responsiveness — the law, and the three tests that hold it

- **Component-internal layout gates on `@container` against its own pane.
  `@media` is for the SHELL only**; the shell list lives in ONE place,
  `tools/uilab_project.py::SHELL_SELECTORS` — widening it is a reviewed edit,
  never a way to pass a test. `LEGACY_VIEWPORT_RULES` stays empty.
- Converted `@container` blocks go AFTER the rules they override (the section
  at the end of the stylesheet, wide→narrow). A shell element cannot be gated
  on a container it is not inside. Re-derive every threshold by measurement,
  never rename `@media` to `@container`.
- The tests (`tests/test_responsive.py`):
  `test_component_layout_gates_on_the_container`,
  `test_no_layout_defects_at_each_viewport[<WxH>]`,
  `test_every_exemption_names_a_viewport_in_the_matrix`. Improve the machinery
  in **uilab**, then run its `tools/check_consumers.py`; keep
  `tools/uilab_project.py` project-only (under a screen). Boot via
  `tools/ui_fixture.py`, never the app's entry point.
- The fixture must reach the state AND content you measure (active target,
  `ready_selector`, a multi-strategy star — `ui_fixture.py::FIXTURE_STAR`). Count
  repeated elements and measure the longest string; when the corpus cannot
  supply the hard input, plant it.
- Declare pseudo-element bleed exceptions in `uilab_project.py::may_bleed`,
  never by widening the rule.
- **The supported minimum width is 850px**: `desktop/window.py::MIN_WINDOW_WIDTH`
  and `uilab_project.py::min_viewport_width` must agree (a test compares them).
- Take a contact sheet WHILE implementing (`tools/contact_sheet.py`); it is a
  review aid, never a gate.

Detail: [docs/rules/ui-core.md#responsiveness--the-law-and-the-three-tests-that-hold-it](../../docs/rules/ui-core.md#responsiveness--the-law-and-the-three-tests-that-hold-it).
