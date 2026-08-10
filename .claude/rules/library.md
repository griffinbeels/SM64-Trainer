---
paths:
  - "src/sm64_events/library/**"
  - "src/sm64_events/ui/components/library*.js"
  - "tools/scrape_sheet.py"
  - "tests/test_library_*.py"
  - "tests/test_ui_library_*.py"
---

# The Library — where to change what

Phase 1 of spec `2026-08-04-ultimate-sheet-library-design.md`, which is a LOCAL
working file: anything load-bearing from it belongs here or in the glossary
before it goes away. Phases 2 (fitted ladders), 3 (serving + refresh), 4
(adoption) and 5 (the Library page, runner profiles) are not built.

| To change... | Edit |
|---|---|
| Reading the `.xlsx` export | `library/workbook.py` — stdlib only, deliberately: the server owns this parse from phase 3, so a dependency here ships in the exe, and openpyxl cannot reach `HYPERLINK()` formula targets and cached values in one load (its formula and cached-value modes are exclusive). Videos live in BOTH cell forms — 3,833 relationship hyperlinks and 3,164 formulas, measured 2026-08-04 — and dropping either silently halves the library. **Attribute ORDER is the trap, twice:** the live export writes `<sheet state="visible" name=...>` and `<hyperlink r:id=... ref=...>`, so a regex anchoring the attribute it wants to the tag opener matches NOTHING and reports a clean empty parse. Both are parsed per-tag now. Column A is a FORMULA cell (`t="str"` with a cached `<v>`) referencing `'Best Time(Raw)'`, not a shared string — mutating a label for a test means editing the worksheet, not `sharedStrings.xml` |
| Where a target BEGINS | `library/sheet.py` — the **id lineage restart**, not the styling: a non-grey row that either opens a section or re-introduces `[1]` alone after 1 has been used. Bold was the authority until 2026-08-05, when the sheet unbolted all nine of Cool Cool Mountain's target rows between two revisions — same 702 rows, same ids, all 147 grey fonts untouched — and bold-as-authority silently lost nine targets with their approaches. The restart rule reproduces all 252 targets on BOTH revisions and agreed with bold EXACTLY on the day bold was still complete (252 = 252, zero disagreement either way). The grey veto is what keeps it honest: WF's `[1] Whomp text Xcam` reuses lineage 1 as a subsection, and without it the restart rule opens a target there. Bold survives on `SheetRow.bold` and every disagreement lands in the payload's `styling_drift`, so the next decay is visible instead of silent |
| The row grammar, and approach-vs-subsection | `library/sheet.py` — THE classification, and the one error that would poison everything downstream. Four rules, in order: (0) a row named **`… star Xcam`** is an APPROACH whatever its ids say — it times a star GRAB, so it is a whole-target time for the star its target maps to. It carries the union of several approaches' ids (`[1|2]`), so rule 1 called all 25 of them subsections and the human corrected every one by hand (2026-08-05). The pattern matches those 25 exactly and nothing else: every other Xcam row times a door, a text box or an entry (`Whomp text Xcam`, `Attic door Xcam`, `Cannon entry Xcam`), which genuinely are parts. `tests/test_library_seed.py` pins BOTH halves — no `star Xcam` left as a subsection, and at least 15 ordinary Xcam rows still subsections, so the first assertion cannot pass by the rule swallowing everything. (1) **already-seen ids ⇒ subsection**, one-way and definitive; a NEW id proves nothing, and today's sheet holding no counterexample is not the same as the rule being sound (user's correction, 2026-08-04). (2) the grey column-A font `FF434343`; styling, so one vote only. (3) a **veto** at `SUBSECTION_VETO_RATIO` = 0.70 of the row's basis — the best of its OWN ids, falling back to the target's best so far. The floor is measured, not chosen: "faster than its basis" alone flags 8 legitimate approaches, while 0.70 flags none of the 268 non-RTA approach rows (slowest 0.770) and still catches 77% of the 147 subsections (median 0.507, max 0.940). Stage RTA rows are EXEMPT because a 70-star route is legitimately a third of the full-stage route beside it (THI 96.40 against 314.74) — those six rows are the entire reason a naive ratio test fails. A disagreement RAISES naming the row. `PLACEHOLDER_CS` refuses `9:59.96`, verbatim the format example on the sheet's rules tab and carried by one live row against a 66.9s target. Column A's FILL colour is banding and does NOT track target boundaries — only 38 of 252 target rows carry a header fill; **bold** is the boundary. Headers nest two deep (`Castle Movements (Lobby)` then `★ BoB`), so `SheetRow` carries `group` as well as `section` — keeping only the innermost threw 113 movement rows into `unknown` |
| A sheet name → an entity key | `library/mapping.py` — misses are OUTPUT, not an error path. `route` (15), `castle_movement` (113) and `not_a_target` (1, with its reason in `KNOWN_NON_TARGETS`) are expected; anything else is `unknown`, which `tests/test_library_seed.py` asserts is empty. **A stage RTA is a ROUTE**, in this project's own glossary sense: its sibling rows pick a different SERIES of stars, not a different way of doing one star, so they span many targets and belong to no single one (user, 2026-08-05). The reason was called `stage_rta` until then. Every **`+ 100c` row is the course's 100-COIN star** ending on the star it names — the exit-star variant model `ranks/standards.py` already carries — so four CCM rows share `star:4:6` while the plain `Slip Slidin' Away` beside them stays `star:4:0`. That also explains the eight main-course red-coin stars mapping to nothing: the community only times them together with 100 coins, so they are absent from the sheet rather than missed by us. Bowser reds need their own clause (the sheet says "Red Coins", our registry says "8 Red Coins") — leaving exactly this unmapped dropped every Bowser reds ladder from the rank seed in 2026-07 |
| The payload shape, JP/US pairing, coverage counts | `library/build.py` — two version fields answering different questions: `schema_version` is ours and forces a rebuild; `sheet_revision` is the sheet's OWN newest Log entry, which is what makes "newest wins" answerable from the source rather than from fetch time. A row only merges into a (JP)/(US) sibling that does not already carry its version — two same-version rows under one name are different approaches whose labels collide, and merging them would drop one with its entries. **The version is a comma-separated TOKEN in the trailing parenthetical, not a bare suffix** (`sheet.py::version_of`/`base_name`, which owns the whole label grammar): 163 rows write `(JP)` alone but 58 write it beside other words — `(Toad, US)`, `(☆15 MIPS Clip, JP)`, `(120 star file, US)` — and anchoring on the bare form read all 58 as version-less until 2026-08-09. That cost three things at once, and only the third was ever reported: their halves never paired, their entries carried no version so the target page's mode toggle could not render, and **14 targets sat in the browse grid wearing their JP row's name** with nothing saying a US row existed ("I don't see the HMC Door Mips Clip US version in our library" — it was there, one click in, under a JP name). Pairing drops ONLY that token and keeps every other word, which is what stops `(MIPS, JP)` merging with `(☆50 MIPS, US)`: those name two different setups, not two versions of one. Several sheet targets share one entity on purpose; they stay separate targets, because collapsing four CCM routes into one list is unreadable |
| The audit view, and the human's corrections | `library/audit.py` + `tools/audit_library.py` + `tools/audit_library.html` — the human's verdict OUTRANKS ours and `build.py` applies it, so a correction is an input rather than a patch. Keys are by NAME, never by row number, and both need a discriminator that cost a real bug: a target key carries its ROM version (BBH opens two targets both called "Go on a Ghost Hunt") and a row key carries its bracket ids ("Warp fadeout" appears twice under Big Bob-omb, and 19 targets repeat a row name). `tests/test_library_seed.py` asserts both are unique on the real snapshot — a collision means one correction silently rules on two things, which is the very failure the audit exists to catch. A row-kind override MOVES the row between `approaches` and `subsections` rather than stamping a field, so a consumer reading only one list cannot miss it. The server binds an OS-chosen free port, so it can never collide with the one he plays on |
| Fitting a ladder to a row | `library/ladders.py` — THREE named steps, each replaceable on its own because the model has already changed once and the user's standing instruction is that changing it again stays cheap: `place_at_percentiles` → `avoid_valleys` → `make_attainable`. **Every cutoff must be a time Usamune can SHOW.** Its clock is a frame counter at 30 fps and centiseconds are only how it prints, so exactly 30 of every 100 values exist (0, 3, 6, 10, 13, 16, 20, 23, 26, 30 …) — and 2,435 of this project's 4,656 derived cutoffs asked for a time nobody could hit until 2026-08-05. Rounding is always UP (`core/timefmt.attainable_cs`): a cutoff is a threshold you must beat, so rounding down quietly makes a rank harder than the number it came from, and up biases the ladder gentle (user's ruling). **Tiers the data cannot tell apart MERGE, never separate by force** (2026-08-06): the old +1-frame separation FABRICATED short ladders — 43 door-sized rows became chains of eight one-frame tiers whose Bronze sat a median 10 cs past its intended percentile with 0.0% of the community slower than it. The faster rank keeps a contested frame; sparse ladders are already first-class (`defined_tiers`, vetted ladders ship with skipped tiers), and one ladder is honestly a single tier ("JRB door - Enter JRB": the whole community ties on one frame). Post-merge shape: 451 of 582 rows keep all 8 tiers, short rows median 7, zero chains. **The ladder's FEEL was measured against the vetted ladders' own** (2026-08-06): empty-middle-tier rate 17% on long rows against the vetted ladders' 37% on the same populations; tier-to-tier step median 1.77% against vetted 2.72% (composition — short rows compress relative steps). **The model's VALUES are a retunable default and no test may pin them** — the percentile pins that existed until 2026-08-06 were the shipped-default-contents trap; tests now pin coherence only (ranks real, strictly increasing, 0<v<100). The community's own vetted ladders land on this set — 2,508 of 2,509, the exception being WF's OG Master at 8.85, a typo upstream — so ours must too or the two sources are not describing the same clock. `tests/test_attainable_times.py` covers the round trips and `tests/test_library_seed.py` covers the shipped output; both mutation-proved |
| Fitting a ladder to a row (rest) | `library/ladders.py` — `LADDER_PERCENTILES` IS the definition, not an approximation of Daily Star; see the phase-2 section below for the measurement and the three model forms already ruled out. `MIN_ENTRIES` = 10 is a FEASIBILITY floor (below it neighbouring percentiles land on the same observation), never an accuracy one — the user declined that explicitly. Cutoffs come out strictly increasing in whole centiseconds because `ranks/classify.py` compares DISPLAYED centiseconds and two tiers sharing one is a tier no time can earn. A cutoff landing inside a real observation gap moves to the gap's slow edge: it changes nobody's rank today (everyone recorded is on one side or the other) but it decides a FUTURE time in that gap, and it stops two cutoffs sharing a gap and minting a band nobody can occupy. `fit_payload` runs AFTER the audit corrections, so a row the audit re-pointed is fitted as what it now is |
| Which approaches become practiceable strategies | `library/adopt.py` — matching compares WHOLE LADDERS, not one cutoff, and both ways a single number fails were measured: a vetted Mario cutoff against `best_cs` compares a US-effective time against a JP one (JRB's stone pillar reads 10.80 JP / 14.50 US against a vetted 14.63, so the true pair looks 3.8 s apart), and where a star carries four strategies inside a second (RR's Somewhere Over the Rainbow) a one-number test pairs them at random. Assignment is greedy over the globally closest pair. Nothing is adopted onto an entity carrying `exit_variants`: a 100-coin star's strategies are variant-qualified because CCM publishes two variants both defining "Standard" AND "Open", and the sheet row does not say which exit star it ran — caught by `tests/test_ui_hundred_coin_render.py` when an unqualified "100 coin star Xcam" reached the picker |
| Serving a fitted ladder to the ranker | `ranks/standards.py` over `data/sheet_ladders.seed.json`, its OWN file merged only on READ. That is what makes "a fitted ladder never overwrites a vetted one" STRUCTURAL rather than a rule a test must remember — nothing can write it into `self._data`, so `save()` cannot spill it. `ladders()` returns a merged COPY (vetted wins); the mutating paths take `_stored_ladders()`, because popping from a merge silently no-ops and the strategy returns next load. `is_fitted`/`fitted_strategies` are how a surface says which is which. 75 strategies across 47 entities today, and adopting them lowers MARELO by design: a new faster strategy makes an entity's best-possible ladder harder, which the user accepted knowingly (2026-08-05) |
| Which copy of the library a running app reads | `library/store.py` — THE newest-sheet-revision-wins rule, and it matters in BOTH directions: a refresh must survive an app update built earlier, and a newer release must still replace a stale local copy. Answerable only because a snapshot carries the sheet's OWN newest Log timestamp rather than our fetch time. `schema_version` is checked FIRST and independently — a copy written by an older build of this code is discarded whatever its revision claims. A refresh landing on an older sheet is `{applied: false}` rather than an error, and writes nothing |
| The library's REST surface | `server/library_api.py`, documented in `docs/api.md`. Mounted unconditionally in `server/app.py` — reference data needs no tracker service, so a broadcast-only second instance still serves it. The refresh runs in a worker thread: it downloads ~5.6 MB and re-fits 631 ladders, and the POLLER shares this process, so a blocked event loop is a dropped star grab. An entity nobody has timed is a 200 with an empty list, never a 404 |
| Assigning a library row to a segment the user BUILT | `library/adoptions.py` — a star approach adopts itself at scrape time (`adopt.py`), a movement cannot: the sheet is finer than our segments (113 rows against 63) and its subsections have no segment at all, so the user builds one and points a row at it rather than us inventing 113 (user's ruling, 2026-08-05). An assignment is the USER's fact — it lives in their data dir, keyed by the row's stable name so a refresh keeps it — and must not be confused with `library_overrides.json`, which is a correction to our READING of the sheet, is committed, and is the same for everybody. Adopted ladders merge into the SAME sheet-derived layer as the bundled ones, and `apply_sheet_ladders` re-merges from scratch on every change, which is what makes an unadopt actually remove a strategy. Every refusal names its reason (409, not 400 — the request is well formed and the refusal is about the world): a silent no-op is indistinguishable from success until a rank fails to appear |
| Where the sheet is fetched from | `library/source.py` — one place, because the tool and the server both need it now |
| Refreshing the bundled snapshot | `tools/scrape_sheet.py` — and **READ the `unknown:` list it prints**; that list is the deliverable, because a target we cannot name looks identical to a target the sheet does not have. `--from <file.xlsx>` re-derives offline. Output is gzipped with `mtime=0`: 4.51 MB of JSON becomes 0.42 MB for 7 ms of load (26 ms against 19 ms), the exe ships it as-is, and an unchanged sheet produces a byte-identical file |
| The Library tab's own rules (section order, bands + subdivisions, the scoring twin, the video-provider registry, the grid math, the trim→import mapping) | `ui/components/librarymodel.js` — import-free of Preact/the DOM on purpose (caps.js and format.js, both themselves import-free, are the only imports), so `tests/test_library_model_js.py` drives every one of these under node with no browser. `sectionOrder` is a SECOND door beside `ladderorder.js::slowestFirst` on purpose, not an unpinned duplicate of it: that one orders a standings TABLE's columns (a run getting faster on one already-chosen strategy), this one orders a PAGE's sections (which strategy a climbing reader meets first) — an unproven approach sorts LAST here and FIRST there, and neither reads the other's ladder key. **The floor tier is the registry key `Iron`** (round 1, 2026-08-07 — "it's the CAPLESS rank", rendered via `capName`), never a third name. **The scoring twin** (`SCORE_ANCHORS`/`scoreFor`/`divisionWithin`/`timeForScore`, mirroring `ranks/scoring.py`) is what lets `bandsOf` split every tier band into five subdivision shells with time brackets — a deliberate duplicate of a curve the browser cannot round-trip for (44k entries re-banded per JP/US flip), so it carries the standing remedy: `tests/test_cross_language_parity.py` drives BOTH real implementations over real ladders and every division edge, mutation-proved. Python's `round()` is half-even; the twin's `pyRound` matches it. **`videoSource(url, parentHost)`** names a player per format (census 2026-08-07: YouTube 6,579 · Twitch 397 · X 384 · bsky 22 · files/images · link-out fallback); Twitch players refuse to load without `parent=` matching the embedding host, and embed.bsky.app takes only a DID — a handle URL ships `embed: null` and the card resolves it on click. `trayToImport` is the tray→`POST /api/compare/import` translation (below) |
| Browsing — course grid → a group's target grid | `ui/components/librarynav.js` — two layers like the target picker's own depth=2 (`entitymodal.js`), but inline PAGE state (`openGroupKey`), never a second `PickerDialog` mounted over the tab: the Library's nav IS the page, and a dialog with nowhere to close to was the wrong reuse. Renders the picker's own `PracticeCell` grid so a course looks identical wherever you pick one from. A target with no entity to look up by (a Castle Movement, a stage RTA) hands back its numeric `index` into the fetched `/api/library` body instead of an entity key — `library.js` resolves either door to the one full-target shape `librarytarget.js` needs |
| The target page — one section per [[approach]], each a [[rank]]-banded, [[division]]-subdivided standings surface over real [[entry]] rows | `ui/components/librarytarget.js` — `Section` is a single-open accordion (the parent owns `open`, never each section), auto-expanding on whatever [[matched strategy]] you are currently graded on (`librarymodel.js::autoExpandName`) or the beginner-most section otherwise. **Each tier band holds five `DivisionGroup`s, collapsed by default** (round 1, 2026-08-07: "find the exact subdivision I want to look at") — header carries the winged numbered cap, the time bracket, counts, and the ◀ you marker at division precision; an EMPTY subdivision is a dimmed plain row, never a button that expands to nothing. Entries WITH videos draw big `ExampleCard`s (thumb/branded tile → embed on click, native `<video>` for files, honest "watch on <site>" for the unembeddable); entries WITHOUT draw compact `PlainEntry` chips — 37,268 of 44,701 entries carry no video, and rendering them as players was round 1's "the videos don't load". **Anchor ids are `lib-band-<targetIndex>-<slug>-<slug(tier)>`, never just the strategy name** — `matched_strategy || name` collides 10 times across 8 real entities, so every anchor is scoped to its OWN `_targetIndex` as well. **The JP/US control is a MODE that FILTERS** (round 1 item 4): entries tagged with the other version disappear, untagged entries show in both, bands/counts compute AFTER the filter, and the per-entry version pill is deleted with the mixed display; the toggle renders when the approach has `ladder_jp` OR mixed-version entries, and the `ladder_version` chip renders independently beside it. Nothing here decides which ladder an ATTEMPT grades on (the JP-standards section above — console-support branch). `entryTrayKey` (the tray item identity a `+` click sends downstream) lives here, not in `librarytray.js`, because only this file knows an entry's owning [[approach]]. Tests that need cards on screen expand the subdivisions first (`EXPAND_DIVISIONS` in each UI test file). **Round 5 — the link door lives here too**: `LinkControl` (button → segment menu → `POST /api/library/adopt` → linked chip + Unlink; refusals show the server's own 409 detail where the click landed) renders inside a movement approach's open section and on every `PiecesList` row (the sheet's subsections, compact rows at the page bottom — their FIRST rendering anywhere); eligibility is `librarymodel.js::linkable` (entity-less approaches + all subsections, `row_key` required), rows arrive decorated with `row_key`/`adopted`/`adoptable` from BOTH full-target doors (`library_api.py::decorated`), one `/api/segments` fetch serves menu options and chip names, and a success calls `library.js::reloadRows` so `adopted` is always re-read from the server, never guessed client-side. The fixture passes `adoptions_path` into `create_app` so render tests clicking adopt write scratch, never `data/library_adoptions.json`. **Round 6 — an association SHOWS, and some make themselves**: `adoptions.py::auto_match` pairs an entity-less target with a live segment by normalized NAME equality (ladder proximity was measured structurally unable: the corpus hand-seeded 3-tier Standard rows never reach `_distance`'s 4-shared-tier floor; 184 rows × 18 vetted strategies scored zero), served as `matched_segment` per request (`segment_names` callable, wired in app.py off the live db) so a segment built mid-session pairs on next load; an explicit adoption outranks it, and a matched row hides the offer strip (the chip says it). The vetted-name-collision refusal is GONE — the assignment lands, the vetted ladder keeps grading (read-merge). Standing: `librarymodel.js::standingOn` grades the segment's best `pb_cs` (new on `/api/target/strategies`) by the ROW's own displayed ladder — the same walk that files entries, so the ◀ you pin and the chip cannot disagree on this page; no PB → Capless verbatim ("if there are no times, it's capless"). One `useAssocStandings` fetch per distinct segment; pieces associate only by their own link, never by the target's match (a part must not wear the whole's PB). **Round 7 — the door is TARGET-level**: `TargetLinkControl` sits beside the page's own name (his ask: "moved near the top … as an 'overarching' thing") and one click adopts EVERY laddered approach (`adoptions.py::adopt_target` → `POST /api/library/adopt_target`, one save + one sync so a half-linked target is unreachable; `unadopt_target` reverses the batch); approaches carry NO row-level strip any more, pieces keep theirs (`LinkDoor` is the shared chrome). The menu OVERLAYS (absolute under the button, page height measured byte-identical open/closed) per his 2026-07-26 overlay ruling. **Round 8 — the REVERSE door**: the segment builder (`ui/components/segments.js`, `builder-liblink` row beside the origin row) drives the SAME adopt_target/unadopt_target endpoints — an existing segment links immediately, a NEW one holds the pick and applies it right after Save mints the id (the segment saves even when the link fails; the error reports beside Save); `GET /api/library/adoptions` grew the two reverse views it reads (`by_entity` from approach assignments only, `matched_by_name` so the editor never contradicts the Library chip with "Not linked") |
| The [[tray]] and the "vibes grid" it can open | `ui/components/librarytray.js` — TWO tiers on purpose, and this file only ever builds the first: several YouTube embeds playing roughly together, honestly labelled (`HONESTY_LINE`) as not frame-synced, because an iframe `start=` param is not a shared clock. `onStudy` is a prop — this file places the "Study in Compare" button and reports `studying`; `library.js`'s `studyInCompare` owns what the click actually does (the second tier). The grid overlay is a plain fixed backdrop, not the shared `Modal`, because every `Modal` call site snaps open/closed and this surface was named as one that must animate both ways (`.claude/rules/ui-core.md`'s state-change-animates law) — it reuses the practice log's own tuned `feedTuning()`/`curve()` rather than inventing a new tunable |
| Compare's fold-in — WHY `compare.js` itself is untouched | `ui/components/library.js::studyInCompare` — Compare's own nav entry is gone (`app.js`'s `NAV_GROUPS`), replaced by the Library, but the `Compare` component, its props (`{t, intent, clearIntent, active}`) and its own state are byte-for-byte what they were: the pane never moved, only the door into it did. "Study in Compare" imports every tray item under its own `entity_key` via `POST /api/compare/import` (one item failing never sinks the batch — each still gets its own import + optional trim PUT), then calls the SAME `enterCompare`/`openIds` door an attempt row's own Compare button already used, landing on the first imported item's (entity, strat) pane with every OTHER entity's import still reachable from Compare's own "load existing" dropdown (never silently dropped, just not shown at once — Compare's own one-pane-at-a-time limit, not a new one this added) |

## "Subsection" is the `subsection-tracking` branch's word, not ours

This zone calls a sheet row a **subsection** and will keep doing so, but the
NOUN belongs to `subsection-tracking`, whose definition is the better one and
is the one in `docs/glossary.md`:

> A piece of a [[star]] or of a [[segment]], practiced on its own — the climb
> rather than the whole star. A subsection IS a segment, and names the target
> it belongs to, so it carries its own personal best, its own ladder and its
> own rows in the practice log.

That branch also owns **moment** (`detectors/moment.py`) — something Mario
does that a subsection can start or end on, read off his own action byte
rather than off where he stands, which is why a subsection can begin and end
inside one course. This zone had briefly written a competing `### Subsection`
row about sheet rows; it was removed on 2026-08-05 rather than merged, at the
user's ruling, because two definitions of one noun is exactly what the
glossary exists to prevent.

The two fit together, and that is the point rather than a compromise: the
sheet's 122 subsection rows ARE subsections in their sense — pieces of a star
practiced on their own — and each already carries a fitted ladder off real
community times. Adoption is the join.

**Do not build moment detection, live moment feeds, or subsection triggers
here.** That work is theirs, is further along, and touches
`tracking/segments.py`, `tracking/eventlabel.py`, `tracking/synthesize.py`,
`server/api.py` and `main.py` — all of which this branch also touches, so
whichever lands second reconciles.

## JP standards: registered, resolved by one rule, mode-resolution NOT ours

User's rule, 2026-08-07: **a JP time gets its own standard only where a
difference is ANNOTATED; everywhere else the base ladder is COMBINED and
applies to both modes.** Two sources annotate differently and ONE rule resolves
both — `ranks/standards.py::ladder_cs(ek, strat, version="jp")` overlays the
annotated JP values rank-by-rank onto the base:

- **Vetted** — `rank_standards.seed.json` `jp_strategies` is SPARSE (only the
  ranks whose JP time differs; 79 strategies / 617 cells, emitted by
  `tools/scrape_ranks.py` since the beginning and unread until 2026-08-07).
- **Sheet** — `sheet_ladders.seed.json` v2 mirrors the vetted two-layer shape
  (`strategies` + `jp_strategies`), a FULL fitted JP ladder where a row's JP
  population clears the feasibility floor on its own (58 library rows qualify;
  6 of the adopted grading strategies carry one). `fit_payload` stamps it as
  `ladder_jp`, and it rides adoption (`adopt.py` and user `adoptions.py` both).

**WHICH version an attempt grades on is deliberately not decided here.** That
is the console-support branch's N64-mode spec (AUTO/JP/US); `ladder_cs`'s
`version=` parameter is the door their spec resolves through, and
`has_jp_ladder(ek, strat)` is how a surface knows a split exists. Do not build
mode detection or a version setting in this zone.

## Owed to phase 5: every standard links to its own examples

User's ask, 2026-08-05, recorded before it is built. Each row of the
[[standards ladder]] on the [[practice log]] should link into the Library and
land on **the examples for that threshold specifically** — not the target's
whole video list. "What are all the examples of achieving this rank?" for
every tier, resolved as the entries whose time sits closest to that cutoff.

Three things make it cheap, and one makes it a real design question:

- The data is already there. Every [[entry]] in the library carries a time and
  the video its [[runner]] linked, and a fitted [[ladder]] already sits on the
  same row — so "the entries nearest this cutoff" is a slice of a list we
  ship, not a new fetch.
- It is the plural of something that exists. `classify.resolve_cutoff_videos`
  already bands example clips into `{rank: url}`, ONE per tier, fastest first.
  This is the same question asked for all of them, so the two must agree about
  which entries belong to a cutoff or the card and the Library will disagree in
  front of the user.
- **Not every link resolves** — see below. A threshold whose single nearest
  entry has been privated must not read as having no examples, which is the
  argument for showing several rather than the closest one.
- The open question is what "belongs to a threshold" means: entries between
  this cutoff and the next faster one is the obvious reading, but a tier with
  few entries then shows an empty list while the tier below it is crowded.
  Decide it against the real distribution rather than in the abstract.

## Two facts about the data that are not bugs

**A row can belong to a different entity than its target.** Under
`Bowser in the Dark World Red Coins` the `Red coin star Xcam` rows are the
8-red-coin STAR, while the longer parent rows include the travel to the pipe
and belong to the pipe segment; the same shape holds for every `+ 100c` target,
where the `100 coin star Xcam` row is the 100-coin star itself and the parent
row is the combined reds-and-coins run (user, 2026-08-05). `library/audit.py`
lets a row carry its own `entity_key`, inheriting its target's when it does
not. **Phase 2 must fit a ladder against a row's OWN entity**, or star:16:0
gets a ladder built from pipe-inclusive times.

**Not every video link resolves.** Sheet entries go private, get deleted, or
are purged from their host, and nothing marks them. Coverage inside a row is
near total — most rows carry tens of entries with links — so a dead link is a
nuisance rather than a hole, and the user has ruled it acceptable
(2026-08-05). Consequences: never present a single link as THE example (the
audit page offers three), never treat a 404 as a data bug, and do not build
link-checking on the strength of it.

## Numbers this zone is pinned against

Measured 2026-08-04 and asserted as FLOORS in `tests/test_library_seed.py`, never
as equalities — the sheet gains rows daily and an exact count would go red for
the community doing exactly what we want.

252 targets · 123 mapped onto 112 entities · 495 approaches · 122 subsections ·
44,749 entries · 7,452 videos · 448 runners · 0 unknown.

(The sheet grows daily: entries and videos both moved overnight between
2026-08-04 and 2026-08-05. Approaches and subsections moved by exactly 25 when
the `star Xcam` rule landed. **Approaches FELL, 509 → 495, on 2026-08-10** when
the version token started being read inside compound parentheticals and 14
JP/US pairs finally merged — a floor is the right shape for a count the
community grows, and the wrong shape for one a parser fix corrects downward,
so re-derive it rather than assuming a drop is a loss.)

**Every row of the sheet is accounted for, and that is checkable rather than
believed** (2026-08-09): 790 worksheet rows → 702 bracketed data rows → 702
`SheetRow`s → 631 library rows, the other 88 being 48 section headers and 39
blanks. The audit that answers "did we miss anything" cannot be a diff of our
output against our output; it walks column A and asks what became of each cell.
Two header rows (`7. Lethal Lava Land`, `★ SL`) carry a stray time in one
runner column and are correctly ignored — they have no bracket id and no name.

## Proving the classification guard

Mutation-proved against the real workbook, three ways, and the first one is the
surprise:

1. **Repaint every grey font black** → still parses, all 147 subsections found.
   The id rule carries it, so the font is NOT load-bearing on its own — which
   means a font-only mutation cannot prove this guard has teeth.
2. **Give one subsection a NEW id AND a black font** → REFUSED by name. Only
   the temporal veto is left, so this is the mutation that proves it.
3. **Give it a new id and leave it grey** → parses as a subsection. That is the
   case with no counterexample on today's sheet, and it must keep working.

The general shape, which cost a wrong step in the plan: **a mutation that two
guards both catch proves neither of them.** To prove guard N has teeth, disable
every OTHER guard in the same mutant.

The same trap in a second form, found 2026-08-05: deleting the `star Xcam`
rule left `test_every_star_xcam_row_is_an_approach` GREEN, because that test
reads the shipped snapshot and the snapshot had the human's overrides applied
to it. A test over the ARTIFACT cannot guard the RULE that produced it. What
closes it is `test_no_correction_is_load_bearing_without_a_reason`:
`apply_overrides` stamps `overridden` only on rows it actually MOVED, so a
correction the parser now makes unaided leaves no stamp, and a correction that
still moves something must carry a written reason. Delete the rule and all 25
become load-bearing at once — mutation-proved in both directions.

## Phase 2 — the ladder fit, measured and settled 2026-08-05

The spec is a local file; this is the committed record.

**A LADDER BELONGS TO A LIBRARY ROW, NOT TO AN ENTITY.** This is the design and
it is the thing a session gets wrong: asked which *entities* would gain a
ladder, the answer is ZERO — all 112 entities the sheet maps to already carry a
vetted Daily Star ladder — and that reads as phase 2 having nothing to do. It
is the wrong denominator. Every one of the ~631 library rows (509 approaches +
122 subsections) has its own distribution and takes its own fitted ladder,
whether or not we currently map it to anything. A Castle Movement row keeps its
ladder in the library for however long it takes a segment to exist for it.
User's correction, 2026-08-05: *"if we have an approach in the library, it
should have rank standards derived and ready to go for adoption"*.

**The model is the percentile, and it is now the DEFINITION rather than an
approximation of Daily Star.** Fitted cutoffs sit at these percentiles of the
row's own distribution, median over 204 vetted ladders matched to sheet
approaches:

| Mario | Grandmaster | Master | Diamond | Platinum | Gold | Silver | Bronze |
|---|---|---|---|---|---|---|---|
| 6.7 | 21.7 | 45.0 | 65.2 | 80.4 | 89.3 | 94.0 | 98.2 |

**It cannot reproduce a vetted ladder tier-for-tier, and that is structural
rather than fixable.** Held out, a fitted ladder gives a real recorded time the
same tier 39–42% of the time and lands within ONE tier 75–78%. The reason: the
median gap between adjacent tiers in the vetted ladders is **2.72%** — only
1.32% between Mario and Grandmaster, 1.88% to Master — while the model's median
time error is 1.5–2%. The error is the size of a tier, so no model fitted from
20–150 community times can resolve them. Do not re-litigate this by trying
another model form: bucketing by duration (2.04% vs 2.07%), scaling by the
distribution's spread (2.22%) and a canonical shape off the sheet best (which
collapses to 23% same-tier) were all measured and none helped. The user's
ruling on that measurement was to **adopt the percentiles as the definition**
and label a fitted ladder as sheet-derived, never as vetted.

**Fitted ladders GRADE for real** (user, 2026-08-05) — a sheet-derived ladder
becomes the real ladder wherever the community publishes none. Vetted Daily
Star ladders are still never touched, and that invariant is best kept
STRUCTURAL (a separate file the vetted store falls back to) rather than by a
test over one merged file.

**Error scales with sample size**, which is what a feasibility floor should be
set from: 20–49 entries → 4.19% median error, 50–149 → 1.94%, 150+ → 1.46%.
The user explicitly DECLINED an accuracy floor; a floor may only exist because
a distribution is too small to define eight tiers at all.

**Adoption, and the duplicate it would otherwise create.** Star approaches
auto-adopt as strategies so they are in the picker with a ladder ready;
subsections do NOT (they would clutter the segment list) and wait for the user;
Castle Movements wait for a segment definition. Auto-adoption must dedupe,
because the sheet's "Mario Wings to the Sky" and our vetted "Skyjump" are one
strategy under two names — match a vetted ladder to a sheet approach by how
close its Mario cutoff is to the approach's sheet best, which paired 204 at a
median 0.33s and is the same matcher the fit measurement uses. The vetted
ladder wins on a match; only unmatched approaches are adopted (~35 today).

**A row's OWN entity is what a ladder fits against** — see the Bowser and
`+ 100c` note above, or star:16:0 gets a ladder built from pipe-inclusive
times.

**Clusters feed the fitter, not only the display**: a percentile-placed cutoff
landing inside a valley mints a rank band nobody can occupy, so it moves to the
valley's slow edge. Multi-modality COUNTS are bandwidth artefacts (3–15%) and
must not be quoted; which approaches surface is stable — Into the Igloo, Time
stopped, Log firsty, Navigating the Toxic Maze, the 100-coin red-coin runs.

**The user will audit every approach and subsection himself eventually**, since
validity often cannot be read off a name without playing the star. Build for
that: the audit page is the surface, and a fitted ladder should be visible
there beside the row it came from.
