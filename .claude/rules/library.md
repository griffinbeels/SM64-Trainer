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
| The row grammar, and approach-vs-subsection | `library/sheet.py` — THE classification, and the one error that would poison everything downstream. Three signals: (1) **already-seen ids ⇒ subsection**, one-way and definitive; a NEW id proves nothing, and today's sheet holding no counterexample is not the same as the rule being sound (user's correction, 2026-08-04). (2) the grey column-A font `FF434343`; styling, so one vote only. (3) a **veto** at `SUBSECTION_VETO_RATIO` = 0.70 of the row's basis — the best of its OWN ids, falling back to the target's best so far. The floor is measured, not chosen: "faster than its basis" alone flags 8 legitimate approaches, while 0.70 flags none of the 268 non-RTA approach rows (slowest 0.770) and still catches 77% of the 147 subsections (median 0.507, max 0.940). Stage RTA rows are EXEMPT because a 70-star route is legitimately a third of the full-stage route beside it (THI 96.40 against 314.74) — those six rows are the entire reason a naive ratio test fails. A disagreement RAISES naming the row. `PLACEHOLDER_CS` refuses `9:59.96`, verbatim the format example on the sheet's rules tab and carried by one live row against a 66.9s target. Column A's FILL colour is banding and does NOT track target boundaries — only 38 of 252 target rows carry a header fill; **bold** is the boundary. Headers nest two deep (`Castle Movements (Lobby)` then `★ BoB`), so `SheetRow` carries `group` as well as `section` — keeping only the innermost threw 113 movement rows into `unknown` |
| A sheet name → an entity key | `library/mapping.py` — misses are OUTPUT, not an error path. `stage_rta` (15), `castle_movement` (113) and `not_a_target` (1, with its reason in `KNOWN_NON_TARGETS`) are expected; anything else is `unknown`, which `tests/test_library_seed.py` asserts is empty. Every **`+ 100c` row is the course's 100-COIN star** ending on the star it names — the exit-star variant model `ranks/standards.py` already carries — so four CCM rows share `star:4:6` while the plain `Slip Slidin' Away` beside them stays `star:4:0`. That also explains the eight main-course red-coin stars mapping to nothing: the community only times them together with 100 coins, so they are absent from the sheet rather than missed by us. Bowser reds need their own clause (the sheet says "Red Coins", our registry says "8 Red Coins") — leaving exactly this unmapped dropped every Bowser reds ladder from the rank seed in 2026-07 |
| The payload shape, JP/US pairing, coverage counts | `library/build.py` — two version fields answering different questions: `schema_version` is ours and forces a rebuild; `sheet_revision` is the sheet's OWN newest Log entry, which is what makes "newest wins" answerable from the source rather than from fetch time. A row only merges into a (JP)/(US) sibling that does not already carry its version — two same-version rows under one name are different approaches whose labels collide, and merging them would drop one with its entries. Several sheet targets share one entity on purpose; they stay separate targets, because collapsing four CCM routes into one list is unreadable |
| Refreshing the bundled snapshot | `tools/scrape_sheet.py` — and **READ the `unknown:` list it prints**; that list is the deliverable, because a target we cannot name looks identical to a target the sheet does not have. `--from <file.xlsx>` re-derives offline. Output is gzipped with `mtime=0`: 4.51 MB of JSON becomes 0.42 MB for 7 ms of load (26 ms against 19 ms), the exe ships it as-is, and an unchanged sheet produces a byte-identical file |

## Numbers this zone is pinned against

Measured 2026-08-04 and asserted as FLOORS in `tests/test_library_seed.py`, never
as equalities — the sheet gains rows daily and an exact count would go red for
the community doing exactly what we want.

252 targets · 123 mapped onto 112 entities · 484 approaches · 147 subsections ·
44,549 entries · 7,419 videos · 446 runners · 0 unknown.

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
