# UI verification norms — full reference

The detail behind the verification norms in
[`.claude/rules/ui-core.md`](../.claude/rules/ui-core.md#ui-verification-norms),
moved here on 2026-09-22 so it no longer auto-loads on every edit. The
rule file holds the checklist; this page holds the evidence, history and
full explanations. Read it when you build or trust a UI probe, fixture,
driven test or before/after comparison.

## UI verification norms

- **An instrument is not shipped until you have READ ITS OUTPUT on real data.**
  Not "its tests pass" — the actual first line, with a value you can check by
  hand. `tools/star_to_screen.py` shipped 2026-08-04 with every part verified
  in isolation (node-driven marks, a render test confirming its records land)
  and the CHAIN verified nowhere, and it had three faults, each visible in its
  first real output: a reader querying the wrong element (empty row list, every
  render, forever), marks claimed only when some surface changed (a 17-second
  "render"), and a join on a `seq` that RESTARTS at 0 every server run while
  the log it joins to persists — pairing a grab with a paint from another day.
  His verdict after one run: *"Reads like noise to me, some of those numbers
  make no sense."* Two of the three were found by reading output, one line
  each; the method works, and it was applied one step too late.
- **The three habits that would have caught all of them**, in the order they
  pay: (1) drive one KNOWN input end to end and assert the number that comes
  out — a self-test of the instrument, not of its parts; (2) make every guard
  assert CONTENT, never existence — "a record was posted" passed while its row
  list was empty, which is a *vacuous guard* written fresh; (3) never assert a
  guarantee you have not tested — this tool printed "exact — no timestamp
  matching" in its own output, and the join was never unique. That last one is
  the mirror of the global rule about untested LIMITS, and it is the more
  dangerous direction: a false limit stops you doing something, a false
  guarantee makes everyone downstream believe a wrong number.
- **A RENDER gate can be masked by a fallback at a different layer, so assert
  at the layer the bug lives in.** 2026-08-11: a new server rule risked
  publishing a practice-log section for a course nobody had touched, and its
  no-phantom guard was a render check — which stayed GREEN under the mutation,
  because `ui/subsections.js`'s own "an orphaned star only shows if it earned a
  card" fallback hid the bad payload client-side. The guard asserted real
  content and got it, through a path that would survive the server rule being
  wrong in either direction. The fix was a second test asserting directly on
  `build_session_view`'s `stars`/`segments` lists, where nothing can mask the
  value; the same mutation turns it red immediately. **Both were kept, and that
  is the shape**: the unit test proves the payload, the render test proves the
  card actually draws — neither substitutes for the other. Read it as the
  layered twin of the vacuous-guard rule above: there, the assertion was too
  weak; here, the assertion was fine and the LAYER was wrong. When a fix lives
  on the server, a browser-level guard is one forgiving fallback away from
  proving nothing.
- **A reader that can return "nothing" must not be able to look like "nothing
  happened".** `ui/uilog.js`'s own header names this failure and the new reader
  reproduced it exactly — the guard for it is the RENDER test asserting a
  non-empty result, because a class-name scan cannot see whether one element is
  actually INSIDE another.

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
- **Wait for the CONTROL you are about to assert on, not for its container.**
  A modal's shell mounts a tick before anything it fetches: the 100-coin
  strategy modal renders `.modal-body` immediately and its exit-star field only
  after `/api/ranks/standards` answers, so a probe that waited for the shell
  failed about **one run in six** — in either test order, so it read as
  order-dependence and was not. What makes this expensive rather than annoying
  is the message: `no exit-star control on a 100-coin star` is
  indistinguishable from the feature being absent (2026-08-03).
- **A ONE-SHOT `if (el) el.click()` cannot undo something that has not happened
  yet, and it fails silently in exactly the case it was written for.** The
  Library auto-opens onto the last-practiced target, so two setup helpers
  backed out to the course grid with a guarded single click. Measured
  2026-08-11, 12 runs of 12: `.library-page` and its grid cells render at
  6-16ms and the auto-open lands at 30-49ms, so the back button was **never
  present** at the moment either helper looked for it. The click was always a
  no-op; what decided the outcome was whether the following
  `wait_for('.entity-grid button')` got its first look in before the auto-open
  replaced the grid. It usually did — hence "flaky" — and when it did not, the
  buttons never became visible again and the test died on a 15s timeout.
  Reproduced at **2 runs in 4**, and it took down a different test in each of
  two release builds. The shape that works: click back until nothing is left
  to go back from, then require the destination to survive a second look past
  the late-arrival window. Waiting for the auto-open itself would pin the
  helper to a fixture that happens to seed a last-practiced target.
- **A frame-sampled threshold must allow ONE FRAME of travel, or it is a coin
  flip.** `tests/test_ui_rank_line.py` asserted that the fade's outgoing text
  was under 0.05 opacity on the last frame before the wording changed. One
  frame of that fade covers about 0.05 at the exchange pivot, so where the
  sample lands inside it is sub-frame phase: measured over 15 runs (30
  exchanges, frame gaps 15.0-18.5ms — no dropped frames, so this was never
  about machine load), the value landed anywhere in 0.000-0.052 and **2 of 30
  cleared the threshold**. A bigger constant is not the fix, because at 30fps
  one frame covers ~0.10 and any fixed number is wrong again; take the step
  from the recorded frames themselves and allow it. It keeps its teeth for
  free — a value that changes without animating has a per-frame step of ~0 for
  the same reason it has an opacity of ~1.
- **A driven test writes to the REAL store, and `data/` is gitignored, so its
  edits outlive its fixture.** `serve_ui` leaves `rank_standards_path()`
  pointing at the worktree's own `data/rank_standards.json`; a test that edits a
  cutoff through the UI leaves it edited for every later run. It surfaced as a
  screenshot taken for review showing the TEST's `1'21"32` in two columns
  instead of the seeded cutoffs (2026-08-03). Restore what you change, through
  the same control — or the rig measures a page only it has ever seen.
- **`subprocess.run(text=True)` decodes with the Windows ANSI codepage.** A
  node-driven probe returning any non-ASCII comes back mojibake'd —
  `100c + Reds · Half Cycle` arrives as `100c + Reds Â· Half Cycle`, and the
  KeyError names the data rather than the encoding. Pass `encoding="utf-8"`.
  Same trap the `concurrent-agents` skill names for `git show`; it bites
  anything that shells out, not only git (2026-08-03).
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
- **When a style is built from a custom property the COMPONENT supplies, assert
  the painted value — the markup stays correct through the failure.** This file
  hands several components a colour that way (`--band-tint` on the rank ladder,
  `--std-band` on the standards table's exit-star bands, `--icon-size` on every
  rank-icon slot), and the failure has no DOM signature at all: with a fallback,
  `color-mix(in srgb, var(--std-band, transparent) 24%, #131b26)` computes to
  plain `transparent`, a perfectly valid colour; without one the declaration is
  invalid at computed-value time and the property quietly reverts to what the
  cascade already had. Either way the class is on the element, the `style`
  attribute is exactly where you put it, `node --check` is happy, and nothing
  paints. So a class-name or attribute assertion is green through precisely the
  bug it was written for — read `getComputedStyle()` and reject
  `rgba(0, 0, 0, 0)`. Worked case, mutation-proved both ways (one hue for every
  band; the property dropped):
  `tests/test_ui_hundred_coin_render.py::test_each_exit_star_band_wears_its_own_colour`.
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

- **A hand-picked example can satisfy the assertion through the wrong path.**
  A search test typed `"lblj"` to prove that an APPROACH's name finds its
  target — and passed, because "LBLJ" is in several target LABELS too, so the
  mechanism under test never ran (2026-08-10). Not a vacuous guard: it
  asserted real content and got it, by a route that would survive the feature
  being deleted. Where the corpus is the input, DERIVE the example from it at
  test time — that search now computes a word some approach carries and no
  label does, and reports "the index is not shipping approach_names" when
  there is none, which is the mutation proof and the calibration in one.

- **SCOPE every query in a driven test — a bare class selector finds the
  HIDDEN copy, because two tabs stay mounted while you are not looking at
  them.** Compare and the Library both render with `display:none` when you
  leave them (library.js's own docstring names the trick), and both reuse
  shared components: the Library's course browser is the SAME
  `entitymodal.js` picker the recorder's "what is this a piece of?" dialog
  is. So `document.querySelector('.entity-grid')` answers with the Library's
  invisible grid, which sits earlier in the DOM and legitimately drills where
  the visible dialog would have closed — a passing app reported as a broken
  feature. Measured, not guessed: `offsetParent` null, ancestors
  `.library-courses` → `.library-page`, present BEFORE the dialog opens
  (2026-08-09, the sheet-library merge). **Twice now** — the same trap ate
  `.library-search` in `tools/uilab_project.py` a day earlier, where the
  Library's hidden search box swallowed every keystroke meant for the
  Segments tab's. The tell is identical both times: no error, plausible
  elements, a verdict about the wrong surface. Anchor to the container that
  identifies YOUR surface (`.record-review .entity-grid`,
  `.segments-page .library-search`), and prove the anchor by mutation —
  a scope that matches nothing looks exactly like a feature that is absent.

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

## A `<button>` carries the global rule with it (2026-08-21)

`index.html`'s global `button` rule sets **`min-height: 34px; display:
inline-flex`**. Every element that becomes a button inherits both, and neither
is undone by the usual text-reset (`appearance: none; background: none; border:
none; padding: 0; font: inherit`).

**The measured cost.** The ranked-leaderboard branch turned a runner's name from
a `<span>` into a `<button>` so the name could open that runner's page — inside
the Library's *existing* ladder list, which the same branch was forbidden to
change. `.library-plain-entry` went **24.75px → 41.75px** and
`.library-example-meta` **40.38px → 48.38px**. A band holds dozens of plain
rows, so the ladder grew about 70% taller in silence. `min-height: 0; display:
inline` restored it exactly; the sibling `.library-example-plus` had carried
`min-height: 0` since it was written.

**Nothing caught it.** The responsive sweep hunts defects, not growth, and the
ladder's own geometry test measures TOC→band gaps with divisions COLLAPSED. It
took a whole-branch review measuring a baseline worktree to find.

**It is now guarded**, by `tests/test_ui_button_flex.py::
test_a_button_reset_to_inline_text_states_its_min_height` — beside the
`justify-content` guard, because both enforce halves of the same global rule.
The discriminator is the FULL text reset (background AND border AND all
padding gone), which is what "this should flow as text" looks like in
declarations. Deliberately narrow, and the exemptions are the point: a rule
declaring its own `display` has taken control of its box (`.std-tier-btn`); one
keeping background, border or padding is a chip-shaped control that wants the
34px (`.version-switch-seg`, `.library-mode-seg`); and a PARTIAL padding reset
leaves the element box-shaped (`.candx`, `.vidbtn`).

Those last two sit just outside the guard and have **not** been judged by
rendering — they strip background and border but keep horizontal padding, so
they inherit the 34px and may carry the same latent bug. Widening the guard to
cover them before judging them would trade one that fires reliably for one that
gets switched off.

## "Unchanged" is a measurement, not a claim (2026-08-21)

Twice on one branch a surface that was supposed to stay untouched changed, and
both times the implementer's report asserted it had not. One wrote
"pixel-identical to before this task" about a page it had never rendered before
its own change; the regression was a wrapper element collapsing a grid's gaps,
15.98px → 6.39px between every band.

**The method that actually settles it**: build a worktree at the pre-change
commit, render the same selector in both, and diff real
`getBoundingClientRect()` numbers — not a contact sheet against a memory of
what the page used to look like.

**The trap that makes the comparison lie**: this repo is installed editable, so
importing `sm64_events` from a worktree can resolve to the MAIN checkout and
both runs are then served the SAME code. The first attempt at this comparison
reported identical numbers for exactly that reason. Give the baseline worktree
its own venv, run with `PYTHONPATH="$PWD/src" .venv/Scripts/python.exe`, and
**print `module.__file__` to prove which tree answered** before believing any
before/after.
