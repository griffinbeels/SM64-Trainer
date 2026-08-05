---
paths:
  - "src/sm64_events/library/**"
  - "tools/scrape_sheet.py"
  - "tests/test_library_*.py"
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
| The payload shape, JP/US pairing, coverage counts | `library/build.py` — two version fields answering different questions: `schema_version` is ours and forces a rebuild; `sheet_revision` is the sheet's OWN newest Log entry, which is what makes "newest wins" answerable from the source rather than from fetch time. A row only merges into a (JP)/(US) sibling that does not already carry its version — two same-version rows under one name are different approaches whose labels collide, and merging them would drop one with its entries. Several sheet targets share one entity on purpose; they stay separate targets, because collapsing four CCM routes into one list is unreadable |
| The audit view, and the human's corrections | `library/audit.py` + `tools/audit_library.py` + `tools/audit_library.html` — the human's verdict OUTRANKS ours and `build.py` applies it, so a correction is an input rather than a patch. Keys are by NAME, never by row number, and both need a discriminator that cost a real bug: a target key carries its ROM version (BBH opens two targets both called "Go on a Ghost Hunt") and a row key carries its bracket ids ("Warp fadeout" appears twice under Big Bob-omb, and 19 targets repeat a row name). `tests/test_library_seed.py` asserts both are unique on the real snapshot — a collision means one correction silently rules on two things, which is the very failure the audit exists to catch. A row-kind override MOVES the row between `approaches` and `subsections` rather than stamping a field, so a consumer reading only one list cannot miss it. The server binds an OS-chosen free port, so it can never collide with the one he plays on |
| Refreshing the bundled snapshot | `tools/scrape_sheet.py` — and **READ the `unknown:` list it prints**; that list is the deliverable, because a target we cannot name looks identical to a target the sheet does not have. `--from <file.xlsx>` re-derives offline. Output is gzipped with `mtime=0`: 4.51 MB of JSON becomes 0.42 MB for 7 ms of load (26 ms against 19 ms), the exe ships it as-is, and an unchanged sheet produces a byte-identical file |

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

252 targets · 123 mapped onto 112 entities · 509 approaches · 122 subsections ·
44,701 entries · 7,433 videos · 448 runners · 0 unknown.

(The sheet grows daily: entries and videos both moved overnight between
2026-08-04 and 2026-08-05. Approaches and subsections moved by exactly 25 when
the `star Xcam` rule landed.)

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

## What phase 2 must know, since the spec is a local file

Phase 2 fits rank ladders from the sheet's distribution for targets the
community publishes no standards for. Three things settled before it starts:

- **The fit is meant to work.** One global percentile per rank is the STARTING
  model, not the answer; if it misses, bucket by target duration (the vetted
  ladders demonstrably widen with length — Bronze is 1.219× Mario under 15s and
  1.389× at 40–120s), fit per section, and weight by sample size. A residue
  that survives all that comes back as a measurement to discuss, never as
  loosely-fitted ladders and never as shipping nothing. User's ruling,
  2026-08-04.
- **Vetted standards are never touched**, only gaps filled. A test must assert
  the intersection of fitted and vetted keys is empty.
- **The hypothesis, measured on 8 hand-mapped rows and not yet on 320:** each
  Daily Star cutoff sits at a stable PERCENTILE of that approach's community
  distribution — Mario ≈ 4–8%, Grandmaster ≈ 18%, Master ≈ 33%, Diamond ≈ 54%,
  Platinum ≈ 71%, Gold ≈ 86%, Silver ≈ 90%, Bronze ≈ 99%. A fixed ratio off the
  Mario cutoff does NOT reproduce them (coefficient of variation stays above
  0.69 at every exponent tried over 302 complete ladders).
- **Clusters are real, rare and cycle-shaped**, and they feed the fitter rather
  than only the display: a percentile-placed cutoff landing inside a valley
  mints a rank band nobody can occupy, so it moves to the valley's slow edge.
  Multi-modality counts are bandwidth artefacts (3–15%) and must not be quoted;
  WHICH approaches surface is stable — Into the Igloo, Time stopped, Log
  firsty, Navigating the Toxic Maze, the 100-coin red-coin runs.
