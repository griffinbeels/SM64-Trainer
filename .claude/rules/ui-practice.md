---
paths:
  - "src/sm64_events/ui/components/practice.js"
  - "src/sm64_events/ui/components/practicecell.js"
  - "src/sm64_events/ui/components/practicelog.js"
  - "src/sm64_events/ui/components/attemptlog.js"
  - "src/sm64_events/ui/components/entitydetail.js"
  - "src/sm64_events/ui/entitysection.js"
  - "src/sm64_events/ui/focustarget.js"
  - "src/sm64_events/ui/rankdisplaymode.js"
  - "src/sm64_events/ui/components/stagebanner.js"
  - "src/sm64_events/ui/components/targetpicker.js"
  - "src/sm64_events/ui/components/entitymodal.js"
  - "src/sm64_events/ui/components/entityicons.js"
  - "src/sm64_events/ui/components/iconpicker.js"
  - "src/sm64_events/ui/components/segments.js"
  - "src/sm64_events/ui/components/segmenttimeline.js"
  - "src/sm64_events/ui/components/routes.js"
  - "src/sm64_events/ui/components/runview.js"
  - "src/sm64_events/ui/components/strategystep.js"
  - "src/sm64_events/ui/components/stratpicker.js"
  - "src/sm64_events/ui/components/stratmodal.js"
  - "src/sm64_events/ui/components/progress.js"
  - "src/sm64_events/ui/components/timeline.js"
  - "src/sm64_events/ui/stagecontext.js"
  - "src/sm64_events/ui/target.js"
  - "src/sm64_events/ui/entities.js"
  - "src/sm64_events/links.py"
  - "tests/test_ui_entities.py"
  - "tests/test_ui_entitymodal.py"
  - "tests/test_ui_practice_context.py"
  - "tests/test_ui_picker_parity.py"
  - "tests/test_ui_section_parity.py"
  - "tests/test_ui_strategy_step.py"
  - "tests/test_segments_editor_ui.py"
  - "tests/test_star_icons.py"
  - "tests/test_ui_entity_section.py"
  - "tests/test_ui_focus_target.py"
  - "tests/test_ui_practice_log.py"
---

# Practice surfaces — where to change what

**`.claude/rules/ui-core.md` loads alongside this** and carries the shell, the
shared primitives and the verification norms — read both.

Full detail, evidence and history: [docs/rules/ui-practice.md](../../docs/rules/ui-practice.md) — read it when changing a practice card, the practice log's membership/ordering/auto-open rules, the recorder, a picker or the strategy controls, or when a live report contradicts one of these rules.

| To change... | Edit |
|---|---|
| The SELECTOR — the quick-select row, its cells and their art | Split out 2026-08-08 into `.claude/rules/ui-selector.md`, which loads when you open `stagebanner.js` / `practicecell.js` / `entityicons.js` / `entities.js` or the art under `ui/assets/`. It holds the stage-banner row dispatch and every Bowser rule, and the ONE entity-icon chain, and the shared practice cell. The per-piece cell toggle (`components/celltoggles.js`) it used to also hold is deleted (round 31, task 3, 2026-08-10) — a piece nests inside its parent's practice-log card now, with nothing left on the selector to switch. This file kept everything the CARDS and the LOG do |
| Authoring a movement's STEPS (the ordered stops between Start and Finish) | **The recorder is the intended path, the builder the fallback.** `segmenttimeline.js` derives steps from the recorded walk (`GET /api/segments/synthesize` → `tracking/synthesize.py::walked_steps`) whenever exactly TWO moments are picked, every stop starting REQUIRED; a third pick makes the middles waypoints instead. Every chip toggle re-runs backtest AND lint through **ONE `definitionFor` builder — backtest, lint and save send the same object**. Strict once any stop is required, loose when none is. `segments.js::thenSection` is the hand-authoring half, deliberately not shaped like `section()`. `waypoints` is in `SAVE_FIELDS`: **the editor must always send the FULL list**. A castle stop reached by a level edge must be a subarea-pinned `level_enter`, not `area_enter` (which never arms outside the castle) |
| Editing a SHIPPED movement, and undoing it | `segments.js`'s `.builder-seeded` panel at the top of the editor. Editing a seeded row sets `seed_dirty=1`, which blocks reconcile's update unconditionally — **the cost and the undo (`POST /api/segments/{id}/reset`) must stay visible at the point of the edit** |
| Segments builder UI | `ui/components/segments.js` — **100% vocab-driven**: a new trigger type in tracking/segments.py needs zero JS. The Icon row writes `/api/icon` and previews `entityIconSrc(t, "segment:<id>")` (what the app actually draws). `flow` level/subarea dropdowns filter via vocab `connections` (`allowedIds`); **the CURRENT value always stays listed**; setParam clears siblings the topology rules out. `.builder-matchmode` options, descriptions and default come from `vocab().match_modes` — never a JS copy or a literal `"loose"`; `match_mode` stays in `SAVE_FIELDS`. Backtest, lint, split/merge and the recorder are segment-only by construction (rule 11 asymmetry, stated on purpose; no parity test should guard it) |
| The RECORDER — "what I just did", turned into a definition | `ui/components/segmenttimeline.js`, entered from segments.js's workshop-hero. See [The recorder](#the-recorder) |
| Auto-picking the ONE thing a route leaves practicable here | `ui/loneoption.js` (import-free) + `stagebanner.js::useLoneRouteOption`, the single wiring for the star row AND castle segment row. Picks only when **a ROUTE narrowed the list (a null filter never picks), exactly one option remains, and the hand is empty**. The segment row reads the route-filtered list, never the `segs` fallback. The write is `{quiet: true}` — no refusal notice, no release of the celebration hold. Not on the Bowser rows. Gates: `tests/test_ui_lone_route_option.py`, `tests/test_ui_lone_route_render.py`; the positive write has no gate yet |
| Moving the practice target (every surface) | `ui/target.js` — **`requestTarget(t, body)` is the ONLY place `"/api/target"` is named** (`tests/test_single_source.py`). The endpoint can refuse; the helper raises the server's sentence via `t.setNotice` (`.app-notice`, fixed, out of flow) and returns whether it landed — never a bare `await send(...)` in a click handler; `strategystep.js` gates `onClose()` on that boolean |
| Whether a pinned card still belongs where the player is standing | `ui/stagecontext.js` — `practicedHere(section, t)`: the course comparison AND `sec.places.includes(t.stage.node)` ([[segment reach]], detail in `.claude/rules/segment-topology.md`); both inputs server-derived, **this file owns no domain rule**. Setting a target needs the exact node; STAYING only needs not having walked into a different course — the projector's retirement rule (caveat 12), pinned against it by `tests/test_ui_practice_context.py`. **Compare a STATIC entity fact against `t.stage`**, never a server "is it here" boolean (it could not be frozen mid-celebration). ARMED pins are exempt |
| Whether there is anything to practice where the player is standing | `ui/stagecontext.js` — `hasPracticeContext(t)`, `practiceMode(t)`, `armedSegments`; import-free. **stagebanner.js and practice.js both ask this one function.** `PRACTICE_MODES` is pinned to `STAGE_ROWS` both ways; **no other file under `ui/` may name a mode literal or read `stage.mode`**. It asks about the PLACE, never the target. Suppress at `activeStar`/`pinnedSegs` in practice.js, not inside the cards. A RUNNING segment is its own context. Server half: `tracking/practicable.py` |
| Entity pickers (level / course / star / segment) | `ui/components/entitymodal.js` — `EntityPicker`: a two-layer GRID in a dialog; **`depth` is a PROP, never inferred**; no search box; cells are real `<button>`s (Escape backs out of a drilled group, focus moves to Back on drill-in); **never claim `role="grid"`**; a `placeholder` renders a clear cell emitting `null`. Optional `nextStep` adds a third layer that owns the write. `PickerDialog` is exported for callers whose own element is the trigger. `ui/entities.js` holds the pure builders: a segment's level derives from `origin`, **never `start_levels`**; ids are strings (`"8:1"`, `"segment:12"`); `visibleGroups` drops emptied groups and keeps the current value listed; `optionIcon` is THE art chain. Target-picker layer 2 is a union of a course's stars and segments starting there, plus one tile per castle region ordered by `vocab.origins` (empty regions draw nothing; region-less segments keep a trailing Castle group). **Filtering lives at the CALL SITE (`allow`); groups come from the server, never re-derived in JS.** Add a call site by USING `EntityPicker`, not by growing `tests/test_ui_picker_parity.py`'s allowlist |
| The badge that says a saved time doesn't mean what the rank implies | `ui/components/marks.js` — `CAVEATS` + `cellBadge`/`cardBadge`; the KEY is server-derived (`tracking/caveats.py`, predicates in `.claude/rules/tracking-storage.md`), this file only draws. The corner badge is the only style — do not re-add alternatives behind a flag. **`suppressFloor` belongs to the CAVEAT, never the badge.** Drawn through `PracticeCell`'s `caveat` prop at every cell call site; practice-log rows (`AttemptRow`, from `attempt_caveat`) wear it too, with `.caveat-chip-word` dropped at every width; the `title` rides the chip. Target-picker grid and Rank coverage strip are not wired yet (needs a payload change) |
| Authoring a movement's steps as CHIPS (the recorder's review row) **— and why no practice surface shows them any more** | `ui/components/steptrack.js` — `StepChip` and `StepPicker`. **`StepTrack` on practice cards is deleted by Griffin's ruling — do not re-add a compact version.** The server half (`card_step_labels`, `steps`/`progress`/`total`) stays. Inverted guards: `tests/test_practice_ui.py`, `tests/test_log_card_heads_match.py`, `tests/test_fixture_reaches_the_real_page.py`. Marker glyphs stack in ONE `inline-grid` cell (never absolute children) and swap by opacity, sequential per ui-core.md; short names from `addresses.node_short_label`. `.step-track` never wraps — do not read chip `top` differences as a wrap |
| Route library grouping | `ui/components/routes.js` — `category` is a free-text `/`-separated PATH fed to `buildTree`/`GroupedList`; seeded groups lead, user groups follow, Uncategorized last; numeric-aware sort. `CategoryModal` offers existing names to stop fragmentation; clearing needs `category: null` to survive the PATCH (`model_dump(exclude_unset=True)`). `.library-actions` is two columns |
| Segment library grouping | `ui/components/segments.js` — nests castle region → place off the server's `origin`; **ORDER comes from `vocab().origins`, never re-derived membership**. A non-empty search passes `forceOpen`. "Library category" overrides via `POST /api/segments/{id}/origin`, "Auto" names the detected place. Saving keeps the saved segment open. Segments only (rule 11) |
| Route builder UI | `ui/components/routes.js` — Routes tab: picker + CRUD, step editor (reorder/add/remove/`need`), candidate chips, import/export, "Run starts when:" (reuses `ClauseRow`), and the seeded-route restore panel (`.builder-seeded`, `POST /api/routes/{id}/reset`). Every edit PUTs steps and re-fetches. Gate: `tests/test_ui_route_reset.py` |
| Route Practice focus | **DELETED** by Griffin's ruling (`RouteFocus`, its fetch and CSS); do not restore it. `.routenum` stays (routes.js, runview.js). Choosing a route lives on the header's route rank card (`.claude/rules/ui-ranks.md`); `store.js`'s reconcile clears a deleted route |
| Run view UI (Run tab) | `ui/components/runview.js` — route picker (selecting ARMS it — **no Start button**); **always-on clock** (idle=`0:00`+offset + preview, active=ticks, finished=frozen only when the LATEST run finished) off `started_utc`+offset **minus paused_ms**; per-step cumulative + ± vs PB + gold ★; Pause/Resume/Reset, Focus, click-to-hide (localStorage `sm64.runFocus`/`sm64.runHidden`); history + graph from `GET /api/run/history`. `store.js` refetches `run` on `run_*`/`game_reset` |
| Practice-target picker (the dialog + its rank badges) | `ui/components/targetpicker.js` — `useTargetPicker(t)` returns `[open, dialog]`; the caller owns the trigger. **ONE instance per page**, mounted in `Practice`. Renders `PickerDialog` directly with `nextStep=${StrategyStep}` and `placeholder=${null}`. Rank badges from `GET /api/target/ranks`, **fetched keyed on the dialog's own `editing` state, never on host renders**. The trigger stays even where nothing can be picked (the 409 sentence explains); the empty card's copy says why first |
| The star/segment kind dispatch (identity, clock, PB, display name) | `ui/entitysection.js` — **the ONE place** for `entityKey`/`entityNoun`/`sectionClock`/`sectionPb`/`displayName`; import-free apart from `redsfamily.js` (`tests/test_ui_entity_section.py`). A star section has no `kind`: **always test `sec.kind === "segment"`, never the inverse**. Segments are RTA-only. Reds/pipe family naming resolves through `redsfamily.js`'s `familyLabel` |
| Whose history the analysis card and detail drawer draw (browse vs. active) | `ui/focustarget.js` — `resolveFocus(manual, live)`: a card click browses without moving the target, and **any change of `activeKey`, `stageKey` or `newestAttemptId` releases it**. Import-free (`tests/test_ui_focus_target.py`). `Practice()` reads the three signals off the FROZEN (celebration-held) snapshot, never live |
| Attempt rows, sort/filter controls, the graph-pick reveal, rank-banner helpers | `ui/components/attemptlog.js` — `AttemptRow`/`AttemptTable`, sort/filter/hide controls, `StatMenuTrigger`, `PbTag`, rank-banner helpers. The card paints ONE `RankBanner`; `showsEntityBanner` only says whether it offers a separate Overall view. **It lives here to keep the import graph a DAG — never export this machinery from `practice.js`.** `useGraphPick` is page-level while pagination is per-card (`LogCard` widens itself); read `LogCard` before changing either half |
| The analysis card and detail drawer — page-level, one copy each | `ui/components/entitydetail.js` — `EntityAnalysis`, `EntityDrawer`, `wipeSection`, **mounted once in `Practice`** following the focused entity; `sec=${null}` → empty analysis, no drawer. **Never add a per-kind copy.** The standards ladder lives in each practice-log card, closed by default, fetching on OPEN; which ladder is `entitysection.js`'s `standardsIdentity` |
| The practice log — page-level, recency-ordered entity cards | `ui/components/practicelog.js` — `orderedSections(view, activeKey)` (newest first by `last_activity`, active entity hoisted), `LogCard` (art, live `StratPicker`, one switchable rank banner, PB tag, fold, paginated table with `ROWS_PER_PAGE=10`, clamped), `PracticeLog` (`CARDS_PER_PAGE=5`, **unassigned bucket always last and never paginated**, target-picker chip in its heading). Membership: [Being armed stopped earning a card](#being-armed-stopped-earning-a-card); order and open slot: [The active entity leads](#the-active-entity-leads-and-a-target-only-card-can-now-disappear), [Leading the list…](#leading-the-list-and-holding-the-open-slot-are-two-different-rules); banner: [Strategy / Overall](#practice-log-strategy--overall-rank-view). `forceOpen` and page turns are **one-shot nudges keyed on `focus.nonce`, never standing overrides**. The card `activeKey` names wears `.log-card-active` + Ready/Running. The PB tag's jump reuses the graph-pick machinery. Each [[subsection]] renders INSIDE the parent's `Disclose` body via `nestSubsections` (`ui/subsections.js`) through ONE `cardFor`: disabled pieces leave the log; a piece draws under every carded parent; area-parented orphans stay top-level, star-parented orphans are hidden; a parent with a nesting child earns a card — **nesting runs before membership** (`hasEarnedACard` injected) |
| Stat chips + the menu that chooses them | `ui/components/entitydetail.js` — `StatChipsRow` (value chips only, filtered by `t.showDust`/`DUST_STAT_KEYS`) inside `EntityDrawer`. **`StatMenuTrigger` is defined only in `attemptlog.js` and used exactly once**, in `PracticeLog`'s heading (`tests/test_ui_section_parity.py`: one `.attempts-tools` row per page). It stays `position: fixed` off its own rect |
| Per-star event graph | `ui/components/timeline.js` (marker styles via `MARKERS` registry) |
| The PB tag, WHO may save one, a foreign row's delta/button/dim | `ui/components/attemptlog.js`, `practicelog.js` — **full detail in `.claude/rules/pb-strategy.md`** (lifted there 2026-08-23 at this file's size ceiling; that rule loads for these two files too) |
| Completion-time graph | `ui/components/progress.js` — per-star and per-segment; gold = saved PBs; a node click goes through `useGraphPick`, **the same path as `PbTag`**. A caller wanting its own empty state must agree with `progressSessions`/`hasProgressPoints` exactly |
| Active-strategy picker (star + segment) | `ui/components/stratpicker.js` — THE strat dropdown + "new strat…" modal; owns `POST /api/strat`, the dropped-write alert and phantom-pick snap-back. **It HOLDS the pick until the store's refresh carries it** — `pending` clears only after `await onChanged()` (`tests/test_ui_strat_pick_settles.py`). One component for both kinds; `submit` prop retargets the write (attempt rows use `POST /api/attempts/{id}/strat`). `groups` draws server-shipped `<optgroup>`s for 100-coin stars; **option VALUE stays the qualified name, and the current/`active` value always stays listed**. Both call sites pass `allowBlank=!sec.default_strat`; a caller that narrows `groups` itself owns its own value |
| Strategy-creation modal | `ui/components/stratmodal.js` — name + full ladder (time + optional video per rank); save = create → PUT thresholds → PUT videos, idempotent. "US and JP timed differently" (off by default) PUTs JP times with `?version=jp` AFTER the US loop. 100-coin stars ask for the exit star from `exit_star_options`; **follow-up PUTs and `onSaved` use the name the create POST RETURNS — never compose it here**; duplicates compare leaves from `strategy_groups`. "Include in ranking" writes `POST /api/marelo/exclude` LAST and **only if touched**. **Do NOT give it `.modal-field`** |
| Per-star external links | `links.py` — `ukikipedia_url(entity_key, label)` resolves against the generated `ukikipedia_titles.py` (refresh: `tools/scrape_ukikipedia.py`) and returns None where no page exists; **callers draw only on non-null**. Aliases are rows in `_MOVEMENT_ALIASES`/`_TOAD_BY_COURSE`. **`ukikipedia.net/wiki` appears in this file alone** (`tests/test_single_source.py`). `xcams_url(entity_key)` is identity-derived; secret-star prefix VERIFY |


## Practice-log caveats sit before strategy, never inside the PB track

`LogCard` draws the PB caveat once, as `cardBadge` in a reserved
`.log-card-caveat-slot` immediately LEFT of the strategy picker; `PbTag` gets
`showCaveat=false`. The slot exists even when empty; its width follows
`container: log-card-identity` (glyph-only below 430px). Gate:
`tests/test_log_card_caveat_layout.py`.
Detail: [docs/rules/ui-practice.md#practice-log-caveats-sit-before-strategy-never-inside-the-pb-track](../../docs/rules/ui-practice.md#practice-log-caveats-sit-before-strategy-never-inside-the-pb-track).


## Practice-log Strategy / Overall rank view

The card always renders ONE banner with pressed-state `Strategy` / `Overall`
buttons in the kicker slot; Strategy is the default and `ui/rankdisplaymode.js`
persists the choice per entity key. A shared ladder (or no separate entity
payload) shows only an inert selected `Overall`. Mode changes pass `rankMode` to
`RankBanner.swapKey` and reuse `useRouteSwap` + `mareloTuning()` exactly — an
exchange between measurements, **never a rank-up climb**.


## Being armed stopped earning a card

`hasEarnedACard(sec, activeKey, t)` has exactly TWO ways in: a real recorded
attempt, or `activeKey` names it AND `practicedHere(sec, t)` holds. **Being
ARMED is not one of them** — the retired-rule test in
`tests/test_ui_practice_log.py` is its inverse; do not restore the armed clause.
Detail: [docs/rules/ui-practice.md#being-armed-stopped-earning-a-card](../../docs/rules/ui-practice.md#being-armed-stopped-earning-a-card).

## The recorder

`ui/components/segmenttimeline.js`. Five properties, none a preference:
**no live mode** (the `view=all` checkbox is not one); **opens onto history**;
**newest first as a display choice only** (`shown = [...rows].reverse()`; ids
sent back keep journal order); **rows land via a tail fetch** keyed on `t.feed`
(`?after_id=`), serialised, not debounced, labels from
`tracking/eventlabel.py::label_event` — no browser labeller; **N moments are
the definition in JOURNAL order**, never click order (client and server both
sort). `parents` is asked HERE and nowhere else (the only door into a
[[subsection]]) as a list of picker option ids translated once by
`entities.js::entityKeyForOption`; keep `parents` and `origin` distinct. Area
tiles drill and lead with a whole-area cell (`group.pick`). **Save is ABSENT
below two picks, not disabled.** Recordings save STRICT; ONE `definitionFor`.
Gates: `tests/test_ui_recorder_latency.py`, `tests/test_fixture_reaches_the_real_page.py`,
`tests/test_segments_editor_ui.py`. Look: `tools/contact_sheet.py --recorder` / `--recording` / `--waypoints`.
Detail: [docs/rules/ui-practice.md#the-recorder](../../docs/rules/ui-practice.md#the-recorder).

## The active entity leads, and a target-only card can now disappear

`orderedSections(view, activeKey)` sorts by recency then hoists the section
`activeKey` (`practice.js`'s `live.activeKey`) names. Card membership is a
client-side question about the PLAYER's position (`hasEarnedACard`, rules in
the section above). Pinned by `tests/test_ui_focus_target.py` and
`tests/test_ui_practice_log.py`.
Detail: [docs/rules/ui-practice.md#the-active-entity-leads-and-a-target-only-card-can-now-disappear](../../docs/rules/ui-practice.md#the-active-entity-leads-and-a-target-only-card-can-now-disappear).

## Leading the list and holding the open slot are two different rules

**Leading is unconditional; the OPEN slot must be earned** by recorded attempts
(`hasRecordedAttempts`/`playedEntityKeys`, asked by both `topEntityKey` and
`activeHasPlayed`). The slot is resolved **inside `PracticeLog` against the
list it renders** by `autoOpenKey(sections, activeKey, playedKeys)`, with
frozen `playedKeys`; an empty active entity falls back to the most recently
played card on screen. The active entity takes the slot on its own only while
it LEADS (`nextActivation`): he picked it (`target.js::useTargetPick`, an
accepted gesture), the page opened on it, or it became the newest thing played.
When the game moves the target, the card he was reading stays open until the
new target's first row (round 13; `tests/test_ui_log_auto_open_render.py`). **The practice log leads the page in the DOM at every
width — no CSS `order` on the page's cards** (`tests/test_practice_page_order.py`).
Detail: [docs/rules/ui-practice.md#leading-the-list-and-holding-the-open-slot-are-two-different-rules](../../docs/rules/ui-practice.md#leading-the-list-and-holding-the-open-slot-are-two-different-rules).

Retired 2026-08-10, the Reds/Pipe one-card rule (reds star now nests in its movement's card): [docs/rules/ui-practice.md#the-redspipe-pair-shows-exactly-one-card--retired-2026-08-10-kept-for-its-evidence](../../docs/rules/ui-practice.md#the-redspipe-pair-shows-exactly-one-card--retired-2026-08-10-kept-for-its-evidence).
