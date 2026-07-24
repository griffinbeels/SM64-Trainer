# Segment banner parity + per-entity icon overrides

Date: 2026-07-24 · Status: implemented (user request, live-audit style; follows
2026-07-24-star-icon-mode-design.md)

## Problem

1. The star-icon mode shipped default-off; the user wants **course icons as
   the default**.
2. The castle/bowser/arena banner rows still render bare `.stagebtn` text
   buttons — "wrong and ugly" next to the star row. They must reuse the SAME
   cell layout (icon, rank medal, name, sub-line, active glow/bob, armed
   state, scale-to-fit), adapted to segment data.
3. Segments need icon art: a default, unless the icon set has a real match
   (the Bowser segments ↔ bitdw/bitfs/bits), and the user must be able to
   attach ANY icon to ANY star or segment (with a preview picker), from the
   segment editor and from the banner itself.

## Design

**One cell component.** `PracticeCell` in `stagebanner.js` renders the
approved star-cell anatomy (holder+img, `.starrank` Medal-or-"–", name,
sub-line) and ALL four banner rows (StarRow, SegmentRow, ArenaRow,
BowserCourseRow) render through it — pinned by tests/test_star_icons.py.
Segment cells show the segment name, its active strat as the sub-line
(swapped for the "⏱ running" chip while armed → `.starcell.armed` green
border), and the segment's rank medal. Segment rows keep their existing
click/auto-select/mutual-exclusion logic untouched — only rendering changes.
Cells in segment rows are fixed to the 7-column star-row width formula
(`calc((100cqw - 80px)/7)`) so a 1-segment lobby row and the 7-star row have
identical cell geometry at every pane width.

**Icon resolution** (client, one helper): user override → mode art → default.

- Override: view's `icon_overrides` (`{entity_key: stem}`, e.g.
  `"segment:3": "bitdw"`) — wins in both icon modes (explicit user intent).
- Stars: course mode → `COURSE_ICON_PREFIXES` art; classic → generic star.
- Segments: course mode → `LEVEL_ICONS` by start level (17/30→bitdw,
  19/33→bitfs, 21/34→bits) else generic star; classic → generic star.
- The Bowser "Reds" cell is a star cell (course 16/17/18): generic star
  unless overridden — visually distinct from the level-icon pipe segment.

**Default mode** — `sm64.starIcons` defaults to `"course"` (store.js).

**Storage & API.** Overrides live server-side (browser↔GUI parity, survives
localStorage wipes): ui_state KV `icon_overrides`, written by
`TrackerService.set_icon(ek, stem|None)` (mirrors `set_rank_mode`: KV +
broadcast-only `icons_changed`; segment existence → LookupError).
`POST /api/icon` is kind-dispatched exactly like `/api/strat`
(`{course_id,star_id}` | `{kind:"segment",segment_id}`, `icon` stem or null
to reset); the stem is validated against the bundled set (unknown → 400).
`GET /api/icons` lists the set's stems (server reads `ui/assets/star_icons/`
so new files appear with zero code changes). The session view carries
`icon_overrides`, and `segment_targets` entries gain `strat` + `rank`
(graded via the SAME `_strat_rank`/`_grading_basis` path as `rank_by_star` —
one grading path, medals can never disagree).

**Icon picker.** `ui/components/iconpicker.js` — a Modal grid of every icon
(fetched from `/api/icons` on open) + a "Default" reset tile; picking POSTs
`/api/icon` and refreshes. Opened from (a) a hover ✎ button on every banner
cell (stars AND segments, in place), and (b) an "Icon" row in the segment
editor (`segments.js`) with a live preview of the segment's resolved icon.

## Testing

test_star_icons.py grows: default-mode pin, all-four-rows-render-PracticeCell
pin, picker wiring pin. test_api.py: /api/icon set/clear/validate (star +
segment + 404 + 400 + degraded 503), /api/icons content, view carries
overrides + segment_targets rank/strat. Visual: harness page screenshots of
all four banner modes, then human playtest.

## Out of scope

Icons anywhere but the banner (practice cards/routes/run views stay
text-first); uploading custom image FILES (the picker chooses from the
bundled set — revisit if the set proves insufficient).
