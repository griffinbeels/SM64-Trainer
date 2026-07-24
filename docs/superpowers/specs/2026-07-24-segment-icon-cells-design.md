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
text-first).

## Addendum — same-day follow-ups (user requests 2026-07-24)

**Armed visibility.** A RUNNING segment must never be invisible ("never
silently running" rule): every banner mode appends a `StandardSegmentCell`
for any armed segment its own filter didn't already include
(`armedExtraCells` in stagebanner.js — the castle/arena rows render their
own lists through the same cell), and a mode with no row of its own (unknown
stage, hub placeholder) renders a "Running" row instead of the placeholder
while anything is armed. To make every armed segment reachable, views.py's
`segment_targets` now includes EVERY definition (a fully location-less start
gets empty `start_areas`/`start_levels` — existing `.some`/`.includes`
filters treat that as no match, so nothing else changes). Disabling the
segment (Hide) remains the way to stop it arming AND showing.

**Custom icon files.** The picker's first tile is a dashed "+" that uploads
any image file: raw-body `POST /api/icons/upload?name=` (compare-upload
precedent, no python-multipart; 2 MB cap, extension whitelist, slugged
filename, overwrite-on-same-name = replace) saves into
`core/paths.user_icons_dir()` (`data/icons/` — the DATA dir, so user icons
survive app updates and stay out of the read-only install), served back via
`GET /api/icons/file/{name}`. Overrides store `user:<filename>`;
`GET /api/icons` gains a `user_icons` list so uploads are reusable across
entities; `/api/icon` validates against bundled stems ∪ user icons.
`iconSrcFromStem` (iconpicker.js) is THE stem→URL rule (banner + editor
preview). No server-side resize (stdlib-only): the modal states the
preferred shape — square, ~100×100, like the bundled set — and CSS
`object-fit: cover` handles the rest.
