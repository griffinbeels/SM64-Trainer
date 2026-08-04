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
`ui/format.js` — **`fmtIgt` is the CANONICAL form and is not display-only**: `core/timefmt.py::format_igt` mirrors it byte for byte AND builds saved clip FILENAMES (`replay/service.py`), so it is an identifier too and keeps its `0'`. **`fmtIgtShort`/`fmtSeconds` are the DISPLAY form** — the same notation with an empty minutes field dropped (`23"00`, not `0'23"00`; user, 2026-08-03) — and `fmtSeconds` exists as a second entry point rather than a conversion because a rank standard is stored in SECONDS at centisecond precision: routing 76.66 s through frames would print 76.63 and move a published cutoff. Both are transformations OF `fmtIgt`'s shape, pinned to it in `tests/test_ui_time_format.py` (including every one of the 1,474 seeded cutoffs round-tripping). (fmtIgt mirrors core/timefmt.py — pinned by
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
| What the page DREW, recorded for a live report | `ui/uilog.js` — reads the RENDERED practice page back (the selector's `.starcell`s + every `.objective-card`) and POSTs `/api/uilog` whenever the painted snapshot changes; ONE call site, `components/practice.js`'s `useUiLog(pageRef)`. Store + why this is not a journaled event: `core/uilog.py`. It reads the DOM rather than the store DELIBERATELY — a model-based recorder logs what we believe is on screen, and the belief is what is in question whenever a "it was just there a couple frames ago" report arrives. The cost is that a class rename empties the log SILENTLY, which is indistinguishable from "nothing was on screen" — the one answer it exists to give — so `tests/test_ui_log_selectors.py` pins every class the reader looks for against the components that render it, and `tests/test_ui_log_records_the_real_page.py` renders the real app and fails if nothing lands. Dedupe is on the RENDERED snapshot, not on props: two states that paint identically are not a change the human saw. Posts are serialised through one queue because ORDER is the entire question these reports ask, and nothing is debounced because a state that lasted three frames IS the evidence |
| Top control bar | `ui/components/header.js` — **four cards, one row**: session / **the route rank card** / clock / **Grading** (the rank MODE), plus the settings drawer. Slot 2 was the MARELO bar (a `<button>` jumping to the Rank tab) until 2026-07-28; it is now `RouteRankCard` and its one gesture is the ROUTE PICKER, because the practice toolbar's route select and this card's scope were already the same thing — see `.claude/rules/ui-ranks.md`. The PRACTICE TARGET card that used to sit in slot 2 was removed 2026-07-26 (user): it named a target the Active-target card and the quick-select row already name, and its own pick was mostly a dead end — you cannot practice Shifting Sand Land while loaded into Lethal Lava Land. Its picker moved to `ui/components/targetpicker.js` (see its own row below) and the MARELO bar took the freed column, which retired the separate `.marelo-row` second header row with NO change to `.context-bar`'s 4-column template at any of its three breakpoints. The card's cell is a `.marelo-slot` wrapper, which carries `container-type: inline-size` so the card's own `@container` rules measure THIS COLUMN and not the viewport (the sidebar's 1180px step makes a column's width non-monotonic in window width — `.claude/rules/ui-core.md`'s responsiveness law). It used to ALSO hold the grid cell open because `MareloBar` rendered `null` until `/api/marelo` landed, and a null grid child is no child at all; `RouteRankCard` never renders null — it hosts the route picker, and a control cannot wait for a rating to arrive — so `.marelo-slot:empty` went with that (2026-07-28). The rank-mode card is labelled **Grading**, not Rank, since it now sits two cards from the thing that shows what your rank IS (id/name/endpoint unchanged). The tab-independent "⏱ <segment> running" armed chip rode the deleted card and was DROPPED with it (user's explicit call): armed state shows on the Practice tab via the stage banner's own running chip and the pinned segment card, and nowhere else. + Rank picker + Star icons control. **Every context card is ONE hit target** (user, 2026-07-25: the practice-target card highlighted and opened from anywhere because it IS a `<button>`; the three select cards only reacted on the select itself, and the mismatch read as a bug). All FOUR cards now go through `ui/components/contextselect.js`, which is where that mechanism lives since 2026-07-28: `CardSelect` renders the chevron + the absolutely-stretched native `<select>` and nothing else, and `ContextSelect` (icon + copy + `CardSelect`) is what session/clock/grading use. The rank card has its own richer body and calls `CardSelect` directly, so the fourth card uses the SAME mechanism rather than a hand-rolled lookalike — which is also why the module exists at all: `marelo.js` needs it and `header.js` imports `marelo.js`, so keeping it in header.js would close an import cycle. `CardSelect` returns a FRAGMENT deliberately — `.context-select > select` is a child combinator, so wrapping the select in a div silently drops the whole rule and with it the hit target. Hidden with **`opacity: 0`, never transparent colours** — Chromium themes a select's popup off its computed background, and a transparent one gets a WHITE popup (see the dropdown row below); at opacity 0 the select keeps the shell's opaque dark background. An opacity-0 element also cannot show its own focus ring, so the CARD wears it via `:has(> select:focus-visible)`. Value and `<option>`s come from ONE `options` list, so the closed state can never disagree with the list; `title` rides the select — it covers the card, so a hover anywhere still answers, and it reaches the combobox as a description. Both halves pinned by `tests/test_header_ui.py` (dropping either silently restores the small hit target). The target picker itself now lives in `ui/components/targetpicker.js` — see its own row below |
| Dropdown lists (every `<select>` popup) | `ui/index.html` — the popup is drawn by the browser, not the page: Chromium themes it off the SELECT's own computed background, and paints the highlighted row light-blue **choosing dark text for it only when the author has not named an option colour**. So the rule is: set `option { background-color }` (that is what makes the list navy instead of Chromium's grey) and NEVER `option { color }` — the row text colour is the browser's to choose. An author colour there is what made the highlighted row light-on-light, the one unreadable state in the shell (live audit 2026-07-25). Corollary: every select needs an opaque dark background of its own; the two that look transparent do it without going transparent (`.context-select` = opacity 0, `.sort-control select` = the wrapper's own `#101f31`). `html { scrollbar-color }` is named for the same reason — `color-scheme: dark` alone still left Chrome 150 drawing the OS's light scrollbar. Rejected 2026-07-25: `appearance: base-select` (Chrome/WebView2 135+, both at 150 here) styles the list beautifully as real DOM, but without author `<button><selectedcontent>` markup in all 25 call sites the CLOSED control cannot clip or ellipsise its own text — a long value spills across the neighbouring UI, and `overflow: hidden` on the select does not contain it |
| Empty "nothing here yet" panels | `ui/components/emptystate.js` — the 404-shaped state for the four Practice panels that can legitimately have nothing to draw (attempt timeline, performance trend, practice log, and the whole analysis card with no target). Each was previously an empty box inside a card with a hard 458px height, which reads as a broken render rather than an honest state (live report 2026-07-25, with the reference: "like a 404 message"). Shape is fixed — dimmed cast art, headline naming what is missing, the one action that fills it, a quip — and the CALLER owns the words: `AttemptLogEmpty`/`TrendEmpty` in practice.js keep the copy in one place per surface so the star and segment cards can't drift. "Nothing recorded" and "everything is filtered out" are DIFFERENT states with different remedies (practise vs. the two toggles in the card's own footer), hence `hasAttempts`. `CAST` ↔ `ui/assets/empty/*.png` coverage is pinned both ways by tests/test_ui_empty_states.py; `pickCast` excludes the previous character by NAME, not by stem — two of the six PNGs are Ukiki and two are Toad, and stem-exclusion put ukiki_1 beside ukiki_2, which still reads as the same monkey twice. The pick is per-MOUNT (`useState`'s lazy initializer), never per render: the view refetches on every WebSocket event and re-rolling there flickers a new character through the panel mid-run. The attempt timeline is the one panel with NO art — at 112px it has no room, and a third character on one screen is noise; it gets a one-line `.stable-empty compact` note above the `+ marker` chip, which stays available (markers can be placed before any attempt exists) |
| Number-animation primitive (plain numbers — a RANK uses the climb, see the row below) | `ui/useTween.js` — `useTween(value, durationMs=700)`: requestAnimationFrame, ease-out-cubic, `prefers-reduced-motion: reduce` snaps instead of animating. THE one tween every numeric surface that can celebrate a rank change routes through — RankBanner's division-fill bars (ranks.js), MareloBar's track+score (marelo.js), and the Rank tab card's rating/Mastery/Coverage (rankpage.js). No component rolls its own requestAnimationFrame loop; `null` passes through with no animation (first-ever value shows immediately, only a later CHANGE tweens) |
| How a SET of cards changes on screen (the selector's rows) | `ui/exchange.js` (the machine, import-free) + `ui/components/cellrow.js` (`CellRow`, the ONE container every row draws through) + `ui/selectortuning.js` (the numbers) + `/ui/tuneselector.html` (his inspector). **Full detail below: [The card-set exchange](#the-card-set-exchange)** |
| Shared modal shell | `ui/components/modal.js` — `Modal({title,onClose,footer,children})`; onClose optional (absent = not dismissable) |
| Update popup | `ui/components/update.js` — modal: version + patch notes (escaped-then-rendered) + GitHub link + exact `download_bytes` + Update/Skip/Later; polls `/api/update/status`. Backdrop/Esc dismiss as "Later" (offer) or "Close" (failed); inert during an active install. Mounted at app root in `app.js` (browser↔GUI parity) |

## The card-set exchange

**Live report 2026-08-02**: *"when we invalidate / add / remove cards from the
[selector] menu here, it feels more like a bug / error than intentional… we need
a better mechanism in place here / process for updating the displayed stars +
segments… internally we're doing some shuffling / heartbeats / validations, but
the user should never see that: they should see their old options fade away, and
their new options appear, no intermediate."*

One walk through a door changes the set three or four times inside ~100 ms — the
level edge, the target retiring, several topological cancels arriving on the
frame heartbeat, then the view refetch. Every one of those was its own repaint,
and a row reshuffling four times in a tenth of a second is the flicker.

- **`ui/exchange.js`** is the machine: `nextState` (a total reducer), `rowStyle`,
  `phaseMs`, `paintsShown`. Import-free, so node drives it directly and the "no
  intermediate set is ever painted" claim is PROVED
  (`tests/test_ui_exchange.py`) rather than inferred from frames a screenshot
  happened to catch.
- **The load-bearing rule is `paintsShown(id, state)`, and it is not a phase
  test.** A row paints arriving children only when their identity is the one the
  machine calls shown. Phrased as "during the fade, paint the held snapshot" it
  left a one-frame hole that was the whole bug back: a reducer cannot be
  dispatched during render, so the new set renders once while the phase is still
  idle. Anchored to `shownId`, no unadopted set has a frame to appear in.
- **The fade window IS the coalescing window.** While the old set leaves, a
  further change only moves `pendingId`; the row adopts the newest when the fade
  lands. So a burst is one exchange, and the repaint count stops depending on how
  the server sequenced its events. A burst that cancels itself (away and back)
  comes straight back up instead of swapping to an identical set.
- **TWO granularities, one owner each** (his second round, same day: *"it doesn't
  fire in ALL situations… if there previously were no options available, but I
  transition to a stage with options… right now it incorrectly cuts"*). A row
  component that unmounts takes its exchange state with it, so a swap that
  replaces the ROW — no context → placeholder, stars → castle movements, a course
  → a Bowser stage — cuts by construction, and no care inside a row could cover
  it. `SurfaceExchange` therefore sits in `StageBanner`, the one thing here that
  never unmounts, wrapping everything it can draw including the placeholder;
  `CellRow` keeps the cell-set changes inside a surface that stays put. They nest
  without double-fading: a surface swap remounts the inner row (so it starts idle
  at full opacity), and a cell change leaves the outer identity untouched.
  `selectorSurfaceId` lives in `stagecontext.js` beside `practiceMode` — same
  reason that module exists — and is deliberately COARSER than "the cells
  changed": the castle's three areas are three sets of movements, a course's own
  subareas are the same seven stars, so entering SSL's pyramid must not blink.
- **Adopting an identity REMOUNTS what it paints, and that is correctness.** The
  painted subtree wears `key=${state.shownId}`, so a child cannot survive an
  adoption — and a child that cannot survive one cannot animate across it. Live
  report: *"when swapping between courses, it briefly flashes the previous
  course's stars and then flashes again."* Two courses use the SAME row
  component, so Preact patched it rather than unmounting it; the inner `CellRow`
  lived through the surface swap and ran its own exchange while the outer one
  faded the surface back in. **Measured: nine frames, peaking at 0.21 opacity, of
  the previous course's stars, after they had gone.** The claim above it ("a
  surface swap remounts the inner row") held only when the MODE changed too, and
  it was a comment rather than a test. Key on `shownId`, never on the arriving id:
  keying on the arrival tears the outgoing content down mid-fade, the one frame
  this exists to hide.
- **Absorption re-arms the wait; the ceiling is `maxHoldMs`.** His rule: *"when
  the selector DISAPPEARS, we need to figure out how we're coalescing. Figure out
  the result. Then display the final result."* An answer arriving 250 ms into a
  210 ms window used to buy a second animation; now each one extends the hidden
  window, and only the settled answer is painted. The bound is not optional — a
  window something can hold open indefinitely is a selector that never comes back.
- **A flash COUNTER cannot see a stale-content flash, and this cost a green gate.**
  Counting `visible → hidden → visible` read those nine frames as one long dip,
  because the effective opacity never crossed back above the visible threshold.
  The property that works is about CONTENT, not the curve: *once a set has left,
  it may never be seen again* (`tests/test_ui_one_animation_per_change.py`). The
  discriminator was taken from a printed per-frame trace of a real swap — the
  first metric was a hypothesis, and it was wrong in the direction that passes.
- **`CellRow` is the single door**, and `tests/test_ui_exchange.py` fails if any
  row renders `<div class="starrow">` itself: a second door looks completely
  correct and reintroduces the flicker in one row while the others stay smooth.
  It flattens htm's ragged children, keeps a KEYLESS cell (the Bowser row's Reds
  cell) and lets its index speak for it — dropping the unkeyed ones was the first
  version and it deleted that cell from the row outright.
- **ONE element carries the opacity** — never a wrapper per cell (it would break
  every `.starrow > .starcell` child-combinator rule) and no per-cell stagger
  (*"ALL of cards fade away"*).
- **The numbers are HIS**: `ui/selectortuning.js` + `/ui/tuneselector.html`, the
  third surface on the `tuning-demo` pattern, with a Burst ×3 button because the
  case worth judging is a burst rather than a single swap.
- `tests/test_ui_row_exchange_plays.py` is the other half and a different claim:
  it drives a real route pick in a real browser, samples the row every frame, and
  fails if the card count ever changes while the row is visible. Mutation-proved
  by pointing one row back at a plain div.

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
- **Anything you read in the SAME `evaluate` that dispatched an event is the
  PRE-render value, and any second control you set in that tick is handed a
  stale closure.** Preact commits after the tick, so both look like the app
  ignoring you. Bit twice in one session (2026-08-01) driving the climb
  inspector's rank pickers: setting the tier and the division together sent the
  division straight back (its handler still held the tier from the last
  render), and reading `.tune-showing` right after the dispatch reported the
  old rank, so a probe asserted on a destination that had already changed.
  Dispatch, `wait_ms(~120)`, then read in a SEPARATE call — and set dependent
  controls one per tick.
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
- **Before debugging a UI report, ask what code the page is actually running.**
  Two rounds of this branch went into a bug that was already fixed: the server
  was serving an older worktree. It is one request —
  `fetch('/ui/<file>.js').then(r => r.text()).then(t => t.includes('<a symbol
  the fix introduced>'))` — and it is the FIRST thing to run when a report
  survives a fix you have verified, ahead of any hypothesis about the code.
  UI files are served `no-cache` with ETags, so a page reload always
  revalidates; a stale page therefore means a stale SERVER, not a stale
  browser. `run-test-server.bat` prints the commit it is running for the same
  reason, but that is on the console and this is queryable.

- **uilab is the shared rig; a limit you hit in it is a bug to FIX there, and
  two of the three "limits" written here were never real (2026-07-29).** Both
  false ones were written from a symptom without measuring the cause, and both
  then justified a workaround. What is actually true:
  1. **`evaluate` DOES await a Promise, and a bare expression DOES return.**
     This file previously said the opposite of each. The real fault was uilab's
     `evaluate` wrapper choosing its form by `startswith("(")`, so every plain
     expression became `() => { expr }` with no `return` and came back `None` —
     silently, which is indistinguishable from a page that has nothing. Fixed
     in the driver and pinned by
     `uilab/tests/test_driver_conformance.py::test_evaluate_returns_a_plain_expression`.
     Do not hand-roll a Python sampling loop: **`uilab.trace.record()`** records
     at the page's own frame rate and answers the questions directly
     (`starts_at_rest`, `comes_to_rest`, `monotone`, `overlaps`, `together`),
     with correlated screenshots via `film()`.
  2. **Several simultaneous clients ARE expressible.** `get_driver()` used to
     mint a fresh driver per call, which made the Playwright driver's
     reference-counted `sync_playwright()` per-object and produced "Sync API
     inside the asyncio loop" on the second `launch()` — an error naming asyncio
     nobody wrote, which read as a hard library limit. `get_driver()` caches one
     instance per name now, pinned by
     `test_driver_conformance.py::test_three_clients_can_be_open_at_once`.
     `tests/test_ui_route_switch.py` was skipped for two days on that
     misreading; it is live again and mutation-proved (5/10 trials fail against
     the pre-fix reconcile, with the reported symptom exactly: the select shows
     the picked route while the card says "Overall" and `active_route` is
     `None`).
  3. **`tools/ui_fixture.py::serve_ui()` degrades SILENTLY to an empty
     database** whenever `data/tracker.db` is absent — which is every worktree,
     since `data/` is gitignored. An empty fixture renders no target, no
     practice log and no detail drawer, so a sweep over it reports clean and a
     render check proves nothing. Always `snapshot_db(LIVE, scratch)` from the
     primary checkout and pass `db_path=`; never hand it the live path, which
     it opens read-write.

  The transferable part is not any of the three. It is that **a skip's stated
  reason is a claim, and an unmeasured one rots into permanent lost coverage
  while looking like diligence** — a named gap reads as honest bookkeeping, so
  nobody re-checks it. Measure the limit before writing it down, and prefer a
  one-line probe against the rig to any reasoning about what the library
  "cannot" do.

- **Swapping one piece of text for another is SEQUENTIAL, never a crossfade.**
  The outgoing string reaches 0 before the incoming one leaves it.
  `climbcurve.js::exchangeFade(progress, at)` returns the `{out, in}` pair and
  is THE way to do it — import-free, so `tests/test_ui_text_exchange.py` drives
  it directly at three exchange points and asserts no frame renders both.
  A simultaneous crossfade (`opacity: 1 - p` beside `opacity: p`) puts BOTH at
  0.5 across the middle of its run, and two different strings stacked in one
  grid cell at half opacity do not read as a blend — they read as a rendering
  fault ("70 Star (Standard)" written across "16 Star — No LBLJ (Standard)",
  live report 2026-07-28). **No duration fixes it**: the overlap is the shape
  of the curve, not its speed, which is why the guard samples the whole run
  rather than one frame. The user's rule is general — "same for all text here,
  and generally just for how fades should work in general". Pass the SAME
  pivot the rest of the transition turns on (the route swap hands it its own
  `swapExchangeAt`, computed once in `routeswap.js`) so the icon, the rank name
  and the scope name all exchange on one frame instead of each choosing its own.
- When a `@container`/media rule `display:none`s an element, its own `title`
  is not a fallback — a hidden element cannot be hovered. Move the text onto
  an element that is always rendered (the rank banner folds its basis line
  into the progress track's tooltip).

## Responsiveness — the law, and the three tests that hold it

**Component-internal layout gates on `@container` against its own pane.
`@media` is for the SHELL only** — `.app-shell`, `.app-sidebar`, `.app-brand`,
`.app-main`, `.app-notice`, `.nav-*`, `.sidebar-*`, `.mobile-*`, `.workspace`,
`.context-*`, `.view-pane`, `.sheet-*`, plus the `prefers-*` blocks. That list
lives in ONE place, `tools/uilab_project.py::SHELL_SELECTORS`; widening it
widens the law and is a reviewed edit, never a way to make a test pass.

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
| `test_component_layout_gates_on_the_container` | a `@media` rule styles a component selector |
| `test_no_layout_defects_across_the_matrix` | the rendered app overflows, clips inside a fixed-height box, truncates an opted-in element, overlaps a flow sibling, **or paints a `::before`/`::after` onto a box it does not own**, at any declared breakpoint |
| `test_the_known_defect_list_does_not_outlive_its_defects` | a row in `known_defects` describes a defect that no longer occurs |

All three live in `tests/test_responsive.py` and are three lines each, because
the machinery is **uilab** — a machine-level module at `Desktop/code/uilab`,
installed editable and shared with every project here. `tools/cdp.py`,
`tools/css_blocks.py`, `tools/responsive_probe.js` and
`tools/responsive_sweep.py` were deleted on 2026-07-28 when it was extracted;
improve the instrumentation THERE, then run its `tools/check_consumers.py`.

What stays local is `tools/uilab_project.py`: how to boot the app, where the
stylesheet is, the shell list, what must never truncate, the component STATES
worth measuring, and the defects currently owed. If it grows past a screen,
something generic has leaked back into it.

Run it while working: `uv run pytest tests/test_responsive.py -q`. It boots the
REAL app offline via `tools/ui_fixture.py` — never the app's own entry point,
which would attach to PJ64 and take the recorder lock out from under a live
recording.

**The fixture must reach the page you think it is measuring.** It seeds
attempts AND sets an active target, and declares a `ready_selector` so uilab
waits for the view to render before measuring. Without the target the practice
page shows "No active objective" and files the populated star into the practice
index inside a CLOSED `<details>`; without the wait, the sweep measures the
loading state. Either one reports a clean page nobody is looking at — which is
how 26 real defects stayed invisible while a feature built on top of them
rendered zero times without a single error (2026-07-28).

**Reaching the card is not the same as reaching its CONTENT, and that is one
level deeper.** The seeded star must have MORE THAN ONE strategy in the bundled
standards, or the strategy ladder is also the star's best ladder, the two ranks
are one measure, and the card draws a **single** combined banner
(`views.py::ranks_share_ladder`). The fixture seeded a one-strategy star until
2026-07-29, so every sweep ever run measured a one-banner card and the whole
class of "the two banners crowd each other" defects was *unreachable by the
gate* — the user reported the stacked washes overlapping three times over two
days and it could only be measured by hand against his own database. The
constants and the reason are in `ui_fixture.py::FIXTURE_STAR`; the rule is
general, so check it whenever a card's layout depends on how many of something
it holds.

**One level deeper again: those two banners both render at the Capless V
FLOOR.** `seed_practice` publishes its attempts BEFORE `_seed_target` posts
`/api/strat`, so every attempt is tagged with no strategy, the saved PB is
keyed with no strategy, and a banner grading under `FIXTURE_STRAT` honestly
reports `unranked` — which draws the floor default. Every sweep and every
contact sheet therefore measures a card whose bars are EMPTY and whose
next-step lines read `→ Capless 4`, so anything that only appears on a GRADED
rank is invisible to the rig. That is the *third* instance of this one root
cause, after the missing target and the one-strategy star: on 2026-07-29 the
whole rank-bar anchoring change rendered byte-identically before and after, and
had to be measured against a hand-built fixture instead. To reach a graded
rank the order is stage → attempts → `POST /api/target` (it refuses until
attempts have landed and the player has a place) → `POST /api/strat` →
attempts AGAIN → `POST /api/pb` on a strat-TAGGED success. Fixing the shipped
fixture is OWED, not done: it changes what the entire matrix measures, so it
belongs in its own change with its own `known_defects` reckoning.

**A defect can be entirely in PAINT, and four of the five probes walk the DOM.**
A pseudo-element is not in the DOM, so nothing that queries the tree can see
one. The rank banners' colour wash is a `::before`; it bled sideways onto the
next grid column and vertically onto the banner stacked beneath it, and three
consecutive sweeps called the page clean while the user reported the overlap
three times. Probe class 5 (`decoration`) closes that: it derives an absolutely
positioned pseudo's rect from its host's padding box and fails when the rect
covers a box that is neither the host nor inside it. Bleeding into an
*ancestor's* padding or grid gap stays legal — that is what a bleed is for.
Declare the genuine exceptions (scrims, focus rings, full-bleed washes) in
`uilab_project.py::may_bleed`, never by widening the rule. Still invisible to
it: a *statically* positioned pseudo pushed out of its host by a negative
margin, which has no derivable geometry.

**A measured constant is only as good as the state the fixture reached.**
`--objective-card-narrow` was measured honestly and was 39px short at every
width in its band, because at the time the fixture could only render the
card's "Nothing to practice here" state. Re-measure after any change to what a
fixed-height card can contain: `uv run python tools/measure_objective_card.py`.

**The supported minimum width is 850px** (user, 2026-07-29; height is
unconstrained). One number, in two places that a test compares:
`desktop/window.py::MIN_WINDOW_WIDTH` — which drives the window's `min_size`,
its default geometry AND a clamp on restored geometry, because `min_size`
constrains dragging but not the size a window opens at — and
`uilab_project.py::min_viewport_width`, which drops narrower widths from the
matrix. A floor the app does not enforce would not narrow the supported range,
it would hide defects inside it. What it costs, so nobody rediscovers it: the
WCAG 320px reflow probe no longer runs, and the mobile shell under `@media
(max-width: 760px)` is no longer measured while still shipping.

**Take a contact sheet WHILE implementing, not after** — `uv run python
tools/contact_sheet.py .objective-card` renders one surface at 1500/1200/900/850
into a single image. Assertions answer "is something broken"; only a picture
answers "is this the thing you meant". Both of the expensive failures here were
obvious on sight and invisible to every probe: a fixture drawing ONE rank banner
where the real card draws two, and two washes overlapping by 15px. "You could
probably solve a lot of your bugs by simply taking screenshots and going 'oh…
there's only one rank standard' while you're thinking" (2026-07-29).

**What none of it catches:** anything that measures fine and looks wrong — bad
hierarchy, ugly wrapping, a control that is reachable but awkward. Assertions
cannot reach that; the contact sheet is for a human eye, and it is a review
aid, never a gate.
