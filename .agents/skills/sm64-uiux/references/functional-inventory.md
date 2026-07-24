# UI functional inventory

Use this as a preservation checklist, then verify against current source. It records
capabilities, not required placement or visual form.

## App shell

- Connection state, manual/AFK pause state, resume.
- Server restart and update check/status.
- Replay recording state, duration, and capacity.
- Lifetime/session selection, new session, session management, scoped wipe.
- Current star or segment target and strategy; target editor and strategy creation.
- Armed/running segment visibility on every tab.
- Clock choice and rank-grading mode.
- Navigation to Practice, Segments, Routes, Run, Compare, and Live feed.
- Update modal.

## Practice

- Stat selection, attempt sort, reset visibility, route-focus selection.
- Contextual stage targeting:
  - main-course star rail;
  - Bowser-course reds/no-reds choice;
  - arena fight auto-target;
  - castle subarea segment choices.
- Active/recent pinned star or segment; multiple armed segment handling.
- Star/segment identity, strategy, PB, rank, next rank, and time filters.
- RTA guide and example links where available.
- Timeline and marker editing.
- Progress history with attempt-row/replay navigation.
- Attempt outcomes, times, deltas, dust-trick counts, strategy/rank, replay,
  compare, Save as PB, clear, and restore.
- Show-more and cleared/abandoned visibility.
- Stat chips and editable rank standards.
- Route step/candidate targeting and K-of-N progress.
- Unassigned-attempt history.

Star and segment detail sections intentionally maintain feature parity where their
domains overlap. Segments remain RTA-only and may be history-only after deletion.

## Segments workshop

- Definition picker and CRUD.
- Enabled state.
- Vocabulary-driven start/end clauses and guards.
- World-topology filtering while preserving stored out-of-topology values.
- Timing bounds and strategy/rank-related behavior.

## Routes workshop

- Route picker and CRUD.
- Ordered steps, reorder/add/remove, labels, K-of-N requirements, candidates.
- Per-step and cumulative success.
- Start-condition trigger.
- Import/export and broken-segment visibility.
- Route Practice focus.

## Run

- Selecting a route arms it; no separate Start button.
- Always-on timer with start offset.
- Current/upcoming/done steps, PB comparisons, and gold splits.
- Pause, resume, reset, Focus mode, and click-to-hide timers.
- Finished/aborted history, progression graph, expandable splits.

## Compare

- Replayable-attempt feed and intent from Practice.
- My-run and comparison videos.
- Import by supported source, upload, progress, save, rename, and delete.
- Synchronized play/pause, stepping, seeking, work-area in/out, offset, mute,
  and volume.
- Keep Compare mounted across tab switches so loaded media and sync survive.

## Live feed and supporting UI

- Raw live event stream for diagnostics.
- Replay player, range serving, and open-in-Compare path.
- Shared modal behavior.
- Strategy creation and rank-standard/video editing.
- Loading, empty, offline, broken-definition, and update states.

## Behavioral invariants

- One authoritative practice target exists at a time.
- Segment arming may retire a star target; running state must remain visible.
- At a fixed viewport, live target/stage/armed-state changes update content inside
  stable Practice layout slots; they do not insert, remove, or reorder structural
  regions. Objective, analysis, and attempt-history crops remain dependable for
  OBS.
- View refresh and WebSocket notices must reconcile without stale UI.
- Existing API contracts and localStorage keys remain stable unless migration is
  deliberately designed and tested.
- The same UI tree serves the browser and desktop shell.
- The packaged app remains zero-build and offline-safe.
