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
**The per-feature detail is in three narrower files, which load only when you
open a file they own:**

| If you are touching... | Also loaded |
|---|---|
| practice / stage banner / pickers / segments / routes / runs / strategies / graphs | `.claude/rules/ui-practice.md` |
| rank icons + caps, banners, the Rank tab, the MARELO pill | `.claude/rules/ui-ranks.md` |
| celebrations, the level-up climb, the tuning inspector | `.claude/rules/ui-climb.md` |
| replay player, compare, failure compilation | `.claude/rules/replay-compare.md` (same file as the backend zone) |

Splitting them was not cosmetic: this was ONE file of ~26,000 tokens that
auto-loaded on every UI edit, with a single table cell of 18,301 characters.
Every routine tweak paid for the whole rank subsystem's history before reading
a line of source, and nobody can skim an 18k-character cell. After the split
(2026-07-28) a practice tweak loads ~12k, a cap-colour change ~14k, a climb
tuning round ~11k, a replay fix ~7k. **Nothing was deleted** — every sentence
kept its home, behind a narrower door; `tests/test_rule_files.py` proves the
budget and that every `paths:` glob still matches a real file.

## The layer itself

Built-in viewer: `ui/index.html` — served per request: edit + refresh, no
restart. Components/store/API client: `ui/components/` · `ui/store.js` ·
`ui/api.js` · `ui/app.js`; vendored Preact in `ui/vendor/`. Shared formatting:
`ui/format.js` (fmtIgt mirrors core/timefmt.py — pinned by
`tests/test_cross_language_parity.py`, which also pins the rank ladder, the
rank-mode registry and stat-chip identity across the two languages). Design
system: one big CSS block in
index.html (cosmic "observatory": navy+gold, card-based, OBS-stable
fixed-height slots); design contract/anti-slop rules in
`.claude/skills/sm64-uiux/` (the `.agents/` path this used to name is now a
pointer to it).

## Shell and shared primitives

| To change... | Edit |
|---|---|
| Library grouping (routes + segments) | `ui/group.js` (`buildTree` — the grouping ENGINE: `{of,label,order}` per level, `/`-joined node paths, numeric-aware and array-composite sort keys) + `ui/components/grouplist.js` (`GroupedList` + `useOpenGroups` — the collapsible chrome, ANY depth; indent is expressed by the DOM nesting itself — ONE `.lib-group` rule (margin + padding + guide line) that COMPOUNDS with depth, NOT a `--depth` custom property, which was dead CSS removed 2026-07-24. The depth-0 exception that cancelled the indent left sub-group HEADERS flush with their parent and was reverted 2026-07-25; the general rule — anything implying parent/child indents the child one level — lives in `.agents/skills/sm64-uiux/`). Consumers supply only their grouping POLICY and row renderer. Open state is an open-SET in localStorage per consumer key (`sm64.routeCatsOpen`, `sm64.segOriginsOpen`) so "nothing stored" = all collapsed; a new consumer MUST use a new key. Rows indent by margin with `width: auto` — `width: 100%` plus a margin overflowed the column and gave the library a horizontal scrollbar (live report 2026-07-24) |
| Live event feed | `ui/components/feed.js` |
| Top control bar | `ui/components/header.js` — **four cards, one row**: session / **the MARELO rank bar** / clock / **Grading** (the rank MODE), plus the settings drawer. The PRACTICE TARGET card that used to sit in slot 2 was removed 2026-07-26 (user): it named a target the Active-target card and the quick-select row already name, and its own pick was mostly a dead end — you cannot practice Shifting Sand Land while loaded into Lethal Lava Land. Its picker moved to `ui/components/targetpicker.js` (see its own row below) and the MARELO bar took the freed column, which retired the separate `.marelo-row` second header row with NO change to `.context-bar`'s 4-column template at any of its three breakpoints. The bar's cell is a `.marelo-slot` wrapper and that is load-bearing: `MareloBar` renders null until `/api/marelo` lands, and a null grid child is no child at all — without it the clock card slides one column left for a beat. The rank-mode card is labelled **Grading**, not Rank, since it now sits two cards from the thing that shows what your rank IS (id/name/endpoint unchanged). The tab-independent "⏱ <segment> running" armed chip rode the deleted card and was DROPPED with it (user's explicit call): armed state shows on the Practice tab via the stage banner's own running chip and the pinned segment card, and nowhere else. + Rank picker + Star icons control. **Every context card is ONE hit target** (user, 2026-07-25: the practice-target card highlighted and opened from anywhere because it IS a `<button>`; the three select cards only reacted on the select itself, and the mismatch read as a bug). Session/clock/rank render through the shared `ContextSelect`, which draws the value + chevron itself and stretches the native `<select>` over the whole card (`.context-select > select` in index.html). Hidden with **`opacity: 0`, never transparent colours** — Chromium themes a select's popup off its computed background, and a transparent one gets a WHITE popup (see the dropdown row below); at opacity 0 the select keeps the shell's opaque dark background. An opacity-0 element also cannot show its own focus ring, so the CARD wears it via `:has(> select:focus-visible)`. Value and `<option>`s come from ONE `options` list, so the closed state can never disagree with the list; `title` rides the select — it covers the card, so a hover anywhere still answers, and it reaches the combobox as a description. Both halves pinned by `tests/test_header_ui.py` (dropping either silently restores the small hit target). The target picker itself now lives in `ui/components/targetpicker.js` — see its own row below |
| Dropdown lists (every `<select>` popup) | `ui/index.html` — the popup is drawn by the browser, not the page: Chromium themes it off the SELECT's own computed background, and paints the highlighted row light-blue **choosing dark text for it only when the author has not named an option colour**. So the rule is: set `option { background-color }` (that is what makes the list navy instead of Chromium's grey) and NEVER `option { color }` — the row text colour is the browser's to choose. An author colour there is what made the highlighted row light-on-light, the one unreadable state in the shell (live audit 2026-07-25). Corollary: every select needs an opaque dark background of its own; the two that look transparent do it without going transparent (`.context-select` = opacity 0, `.sort-control select` = the wrapper's own `#101f31`). `html { scrollbar-color }` is named for the same reason — `color-scheme: dark` alone still left Chrome 150 drawing the OS's light scrollbar. Rejected 2026-07-25: `appearance: base-select` (Chrome/WebView2 135+, both at 150 here) styles the list beautifully as real DOM, but without author `<button><selectedcontent>` markup in all 25 call sites the CLOSED control cannot clip or ellipsise its own text — a long value spills across the neighbouring UI, and `overflow: hidden` on the select does not contain it |
| Empty "nothing here yet" panels | `ui/components/emptystate.js` — the 404-shaped state for the four Practice panels that can legitimately have nothing to draw (attempt timeline, performance trend, practice log, and the whole analysis card with no target). Each was previously an empty box inside a card with a hard 458px height, which reads as a broken render rather than an honest state (live report 2026-07-25, with the reference: "like a 404 message"). Shape is fixed — dimmed cast art, headline naming what is missing, the one action that fills it, a quip — and the CALLER owns the words: `AttemptLogEmpty`/`TrendEmpty` in practice.js keep the copy in one place per surface so the star and segment cards can't drift. "Nothing recorded" and "everything is filtered out" are DIFFERENT states with different remedies (practise vs. the two toggles in the card's own footer), hence `hasAttempts`. `CAST` ↔ `ui/assets/empty/*.png` coverage is pinned both ways by tests/test_ui_empty_states.py; `pickCast` excludes the previous character by NAME, not by stem — two of the six PNGs are Ukiki and two are Toad, and stem-exclusion put ukiki_1 beside ukiki_2, which still reads as the same monkey twice. The pick is per-MOUNT (`useState`'s lazy initializer), never per render: the view refetches on every WebSocket event and re-rolling there flickers a new character through the panel mid-run. The attempt timeline is the one panel with NO art — at 112px it has no room, and a third character on one screen is noise; it gets a one-line `.stable-empty compact` note above the `+ marker` chip, which stays available (markers can be placed before any attempt exists) |
| Number-animation primitive (plain numbers — a RANK uses the climb, see the row below) | `ui/useTween.js` — `useTween(value, durationMs=700)`: requestAnimationFrame, ease-out-cubic, `prefers-reduced-motion: reduce` snaps instead of animating. THE one tween every numeric surface that can celebrate a rank change routes through — RankBanner's division-fill bars (ranks.js), MareloBar's track+score (marelo.js), and the Rank tab card's rating/Mastery/Coverage (rankpage.js). No component rolls its own requestAnimationFrame loop; `null` passes through with no animation (first-ever value shows immediately, only a later CHANGE tweens) |
| Shared modal shell | `ui/components/modal.js` — `Modal({title,onClose,footer,children})`; onClose optional (absent = not dismissable) |
| Update popup | `ui/components/update.js` — modal: version + patch notes (escaped-then-rendered) + GitHub link + exact `download_bytes` + Update/Skip/Later; polls `/api/update/status`. Backdrop/Esc dismiss as "Later" (offer) or "Close" (failed); inert during an active install. Mounted at app root in `app.js` (browser↔GUI parity) |

## UI verification norms

- **htm COLLAPSES the whitespace between a text node and an interpolation or
  an element**, so `into <code>x</code>` renders as `intoy`, `but ${n}ms`
  renders as `but220ms`, and `each (350ms…)` renders as `each(350ms…)`. Three
  separate sentences shipped mangled this way in one session (2026-07-27) and
  every one of them passed `node --check` and every unit test — only a render
  shows it. Write the space as `${" "}` wherever a run of text meets an
  interpolation or a tag. There is no lint for this; the tell is a screenshot
  with two words fused.

- **Never put a backtick inside an `html\`\`` template** — including in an
  HTML comment. The first one ENDS the template literal and everything after
  it parses as JS; the page then dies with something unrelated-looking
  (`ReferenceError: factor is not defined`, `html(...) is not a function`)
  while `node --check` passes, because the file is still valid syntax. Cost
  two round trips on 2026-07-25 writing a comment about a CSS class. Same
  rule for string-concatenating around a template: build the string first,
  interpolate it once.

- **`node --check` returns 0 on JS that cannot be imported.** Measured twice in
  one session (2026-07-27): a scripted edit that ate the closing `}` of an
  exported object literal, and a `const` used above its own declaration (a
  temporal dead zone). Both printed `exit=0`; the first was caught by 82 red
  tests and the second only by rendering the page, which reported
  `Uncaught ReferenceError: Cannot access 'settledText' before initialization`.
  `node --check` answers "is this parseable", never "does this load or run", so
  it is a spell-check and not a build. The cheap upgrade for a module with no
  DOM in it is one line —
  `node --input-type=module -e "import('./file.js').then(...).catch(...)"` —
  which does execute it.

- After any UI change, verify with a headless render or the chrome-devtools
  MCP — unit tests + `node --check` alone shipped an invisible feature once.
  Fixture-server recipe, harness-page technique + headless-Chrome fallback:
  see auto-memory `verify-ui-effects-with-harness-page`.

- **`--dump-dom` + `--virtual-time-budget` RENDERS the app but cannot DRIVE
  it.** Clicks dispatch, DOM listeners fire, and no Preact re-render ever
  happens — `document.body.innerHTML.length` came back byte-identical after
  clicking a plain nav tab (2026-07-26). Anything needing interaction
  (opening a modal, walking a multi-step flow) must go through CDP:
  launch `chrome --headless=new --remote-debugging-port=N`, take the page's
  `webSocketDebuggerUrl` from `http://127.0.0.1:N/json`, then
  `Runtime.evaluate` with `awaitPromise: true`. Collect
  `Runtime.consoleAPICalled` and `Runtime.exceptionThrown` as they arrive
  between command replies — dropping those events is how a broken FIXTURE
  looks like a broken feature.
- **Start every driven run with a CONTROL interaction on something unrelated
  and known-good** (a nav tab), and assert it changed the DOM. Without it a
  harness fault is indistinguishable from the bug you are hunting: a whole
  session's evidence pointed at "the new picker never opens" when the app was
  frozen for an unrelated reason and nothing at all responded.
- **A fixture must answer the query the UI actually sends, not just the
  path — byte for byte, including its ENCODING.** `standards.js` fetches
  `/api/ranks/standards?entity=…`, which returns a per-entity
  `{entity, clock, strategies, …}`; the same route with no param returns the
  WHOLE store `{version, entities}`. A fixture server that ignores query
  strings serves the wrong one, `Object.keys(undefined)` throws **inside
  Preact's render**, and the entire tree stops updating while DOM listeners
  keep firing — an app that looks alive and is not. The encoding half bit
  separately (2026-07-26): the Rank tab sends
  `/api/marelo?scope=route%3A4` (`encodeURIComponent` on the scope id) while
  the capture ran the plain `scope=route:4` — same request, different bytes,
  so the lookup missed and `data.entities` was undefined. Compare
  `urllib.parse.unquote`d paths, and let a captured path with a query answer a
  bare request but NEVER the reverse (a queried request falling back to the
  bare path's payload is how the wrong shape gets served).
- **Print every unmatched path from the fixture server.** A miss returning
  `{}` is indistinguishable from a component that renders nothing, and the
  exception it eventually throws names the COMPONENT, not the missing
  fixture. One `print("MISS", path)` turned "why is CoverageStrip crashing"
  into a four-line diff (2026-07-26).
- Generate fixtures for endpoints the RUNNING instance does not have (a new
  route on your branch, or one whose shape your branch changed) by calling
  the builders against a SQLite **online-backup** snapshot of the live db
  (`sqlite3.Connection.backup`, never a file copy — a copy can catch a torn
  WAL). `TrackerService(db, broadcaster, ranks=…)`: omit `ranks=` and every
  rank builder short-circuits to empty, which reads as a broken builder.
- **"Clicking anywhere on it works" is a hit-testing claim, and a screenshot
  cannot show it.** Sample `document.elementFromPoint` at the corners, the
  icon lane, the label and the centre, write the results into a `<pre>` on the
  page, and read them back with `--dump-dom` — the whole-card selects were
  proved that way at five widths (2026-07-25). Same for a state only a real
  input produces: a native `<select>` popup opens on a genuine click, so drive
  it with the MCP `click` and read `expanded` off the a11y snapshot.
- **Prefer serving the REAL `index.html`/`app.js` against captured API
  fixtures over hand-building a harness page.** `GET /api/session?clock=&scope=`
  (plus marelo/segments/vocab/routes/pause/run) off a running instance is a
  perfectly-shaped fixture; a ~120-line static server that returns those and
  serves `/ui/*` from disk gives the real shell, CSS, container queries and
  component tree by construction — which is the entire ancestors problem
  below, solved rather than re-litigated. Mutating the captured JSON is also
  how you reach states live data does not currently hold (no target, all
  attempts filtered, a segment card): the empty-state pass verified five
  scenarios that way, and caught a real bug (two Ukikis side by side) that no
  unit test would have. Reads off the live server are GETs and safe while the
  user plays; never START `python -m sm64_events.main` for this. Working
  example: `fixture_server.py` in that session's scratchpad, described in the
  memory above.
- Kill any `python -m http.server` (or fixture server) you start, in the same
  session.
- **A hand-built harness must mount inside the real ancestors, or it measures
  a layout that does not exist** (rank-banner ellipsis, 2026-07-25 — measured
  wrong three times, twice by eye and once by a harness). Two clauses, both
  load-bearing (three, counting the CSS itself):
  0. Wearing the real **stylesheet**, fetched out of `ui/index.html` — the
     design system is one `<style>` block in that file, so a harness that
     imports the components and nothing else measures unstyled blocks. It
     reported a 1547px panel and 25 rows for a grid that renders 9 across
     (2026-07-25, picker redesign), i.e. it "found" the scrolling the change
     had already removed.
  1. Inside the real **app shell**. `.app-shell` is
     `grid-template-columns: var(--sidebar-wide) minmax(0,1fr)` — 206px,
     dropping to `--sidebar-rail` 76px at 1180px and `display:none` under
     760px. A harness rendering into bare `<body>` hands every card ~206px
     it never gets, and reported ZERO overflow at 1400px when the real
     answer was eight.
  2. Inside the real **`.practice-page`**, which is what declares
     `container-type: inline-size`. Without it every `@container` rule
     silently never matches, so the harness cannot show a container-based
     fix working and reports it as ineffective when it is correct — a worse
     failure than the width error, because it looks like a verdict.
  - Sanity check before trusting any number from a harness: compare its
    rendered card width against a real screenshot of the app.
- **An element that WRAPS inside a fixed-height card costs nothing visible and
  clips its sibling.** The pending-target chip wrapping `.shead` to a second
  line grew the header 30px → 62px while `.stagebanner`'s height and
  `.starrow`'s computed height stayed byte-identical — so the 32px came out of
  the star cells' bottoms, under `overflow: hidden`, where a screenshot of
  either end state looks fine (2026-07-26). Measure it by removing the element
  from the DOM (`el.remove()` over CDP, no re-render in between) and diffing
  the geometry with and without: identical card/row heights next to a taller
  header IS the clip. Do not reason about whether a card "has room".
- **Sweep the width continuum, not three sample points.** For the rank
  banners, 1400/900/700 all passed while every window from ~1101px to
  ~1500px was broken — 900px passes only because the layout stacks there.
- **Viewport width is often the wrong signal in this app**: the sidebar step
  at 1180px means the pane a card lives in is NOT monotonic in window width
  (a 1181px window gives a card a 947px pane; a 1180px window gives it
  1076px). Gate card-internal layout on `@container` against
  `.practice-page`, the way the star row's `cqw` sizing already does.
- **A test that reads source text asserts on `strip_comments(source)`**
  (`tests/source_scan.py`) and is probed in both directions. A raw substring
  cannot tell code from prose: `assert "Escape" in MODAL` stayed green with the
  handler deleted (the header comment names it), and five `not in` guards were
  rewritten in one session because a comment explaining the absent code tripped
  them (`WORLD_EDGES`, `--depth`, `.route-cat`, `start_levels`, `role="grid"`).
  Express the check as a function of source text so a probe test can feed it a
  comment-only sample and a real-code sample — `test_the_guards_can_still_fail`
  in `tests/test_ui_picker_parity.py` is the pattern.
- When a `@container`/media rule `display:none`s an element, its own `title`
  is not a fallback — a hidden element cannot be hovered. Move the text onto
  an element that is always rendered (the rank banner folds its basis line
  into the progress track's tooltip).

## Responsiveness — the law, and the three tests that hold it

**Component-internal layout gates on `@container` against its own pane.
`@media` is for the SHELL only** — `.app-shell`, `.app-sidebar`, `.app-brand`,
`.app-main`, `.app-notice`, `.nav-*`, `.sidebar-*`, `.mobile-*`, `.workspace`,
`.context-*`, `.view-pane`, `.sheet-*`, plus the `prefers-*` blocks. That list
lives in ONE place, `tools/css_blocks.py::SHELL_PREFIXES`; widening it widens
the law and is a reviewed edit, never a way to make a test pass.

The reason is measured, and this file's `@container` section already states it
for the rank banners: the sidebar is 206px wide above 1180px and a **76px rail
below it**, so the pane a card lives in is **not monotonic in window width** —
a 1181px window gives a card a 947px pane, a 1180px window gives it 1076px. No
viewport threshold can express "this card is too narrow"; every one of them is
wrong on one side of that jump. The insight was written down on 2026-07-25 and
applied to two rules; 145 component-internal rules were still viewport-keyed
three days later, and the Active Target card clipped its own "Ready" row at
900×1180 as the direct result. **All 145 were converted on 2026-07-28 and
`LEGACY_VIEWPORT_RULES` is empty** — a new one is now a red build with no
precedent to point at.

Two placement rules the conversion paid for, both of which produced a fix that
read as correct and did nothing:

1. **A converted block goes AFTER the rules it overrides**, not where its old
   `@media` block sat. Relocating changes cascade position, and the Compare
   rules landed 2,200 lines above `.compare-transport .primary-transport` —
   identical specificity (0,2,0), later wins, so the base rule kept its 92px
   min-width and the sweep reported the identical overlap through the fix.
   The converted blocks are therefore one section at the END of the stylesheet,
   ordered wide→narrow.
2. **A shell element cannot be gated on a container it is not inside.**
   `.context-bar` lives in the header, outside `.view-pane`, so when the
   conversion swept its rule into `@container (max-width: 1060px)` the query
   could never match and the overflow came back on all seven tabs at once.

Beware the translation trap: `@media (max-width: 760px)` does **not** become
`@container (max-width: 760px)`. Below 760px the sidebar is gone, so the pane
is *wider* than the viewport number suggests. Every threshold is re-derived by
measurement, never renamed. And note that the shipped 760px block does two
jobs — "the shell went mobile" (genuinely viewport) and "every card is now
narrow" (container) — which is why it was invisible: at that width the two
signals nearly coincide.

Three tests, none of which can be satisfied by a comment:

| Test | Fails when |
|---|---|
| `tests/test_responsive_structure.py` | a `@media` rule styles a component selector. `LEGACY_VIEWPORT_RULES` carries the pre-existing debt as one row per rule, so the count is honest; a second test fails when a row outlives its rule |
| `tests/test_responsive.py::test_every_declared_breakpoint_is_probed_on_both_sides` | a threshold exists in the stylesheet with no probe point at N and N+1 — i.e. a breakpoint nobody checks |
| `tests/test_responsive.py::test_no_layout_defects_across_the_matrix` | the rendered app overflows, clips inside a fixed-height box, truncates an opted-in element, overlaps a flow sibling, or hides a tab at some size |

Run the sweep directly while working: `uv run python tools/responsive_sweep.py`
(add `--shots` for a contact sheet). It boots the REAL app offline via
`tools/ui_fixture.py` — never `python -m sm64_events.main`, which would attach
to PJ64 and take the recorder lock out from under a live recording.

**What none of it catches:** anything that measures fine and looks wrong — bad
hierarchy, ugly wrapping, a control that is reachable but awkward. Assertions
cannot reach that; the contact sheet is for a human eye, and it is a review
aid, never a gate.
