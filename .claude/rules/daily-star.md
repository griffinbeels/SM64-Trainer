---
paths:
  - "src/sm64_events/ranks/dailystar.py"
  - "src/sm64_events/server/dailystar_api.py"
  - "src/sm64_events/ui/dailystar.js"
  - "src/sm64_events/ui/components/dailystarmodal.js"
  - "tests/test_daily_star.py"
  - "tests/test_ui_daily_star.py"
---

# The Daily Star — where to change what

One Scorecard entry per local date, the same on every install (task 0111).
Design history: `.iteration/daily-star/lineage.md` (local) and the glossary's
**Daily Star** row.

| To change... | Edit |
|---|---|
| Which entry a date deals, the order, Day 1 | `ranks/dailystar.py` — `DECK` (frozen Overall-card cells; a fight is its seed key), `EPOCH`/`FIRST_KEY`, `cycle_order`. **Any change reshuffles every user's future days**: `tests/test_daily_star.py` pins dealt dates on purpose, so re-pin deliberately. A cycle's order may depend only on its own seed and the previous cycle's LAST card, never a chain back to Day 1 (the prototype recursed and would overflow the stack) |
| Your rank / Next Goal | `server/dailystar_api.py::overall_standing` — the star's OVERALL curve (`views.entity_rank`, what MARELO aggregates), never a strategy ladder (his ruling, 2026-09-24). The tile strategy is sent only to name the Library page |
| Today's best | `server/dailystar_api.py::todays_best` — attempts that ENDED inside the browser's local date; imports excluded. Shown only in the selector hover now |
| The announcement's content, TIME LEFT, midnight page turn | `ui/components/dailystarmodal.js` + the `.daily-*` block in `ui/index.html`. The open dialog owns its page (`pages`), so the store clearing at midnight never closes it; `TURN_MS` must equal the CSS turn length |
| When it appears, the local date, midnight | `ui/dailystar.js` (`useDailyStar`, on `t` via `app.js`, not the store: `useTracker` is at its length allowance). Seen-date is server state (`ui_state` `daily_star_seen`) so the window and a tab announce once |
| The foil on the selector cell | `.daily-star-foil` in `ui/index.html`; the cell gets it from `PracticeCell`'s `daily` prop (`dailyFor(t, key)` at each call site in `stagebanner.js`) |

Known gap: a Bowser reds star has no cell of its own on the Bowser row (it
nests under "Reds"), so on those days nothing on that row shimmers.
Browser pins: `tests/test_ui_daily_star.py` (clock pinned to 2026-09-23 so the
fixture's WF stage holds the day's star).
