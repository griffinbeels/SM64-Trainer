---
paths:
  - "src/sm64_events/memory/layout.py"
  - "src/sm64_events/core/snapshot.py"
  - "src/sm64_events/server/poller.py"
  - "src/sm64_events/detectors/igt_clock.py"
  - "src/sm64_events/detectors/counter_epoch.py"
  - "src/sm64_events/detectors/star_grab.py"
  - "src/sm64_events/core/events.py"
  - "src/sm64_events/storage/db.py"
  - "src/sm64_events/tracking/projection.py"
  - "src/sm64_events/tracking/service.py"
  - "src/sm64_events/server/api.py"
  - "src/sm64_events/server/broadcaster.py"
  - "src/sm64_events/ui/components/practicelog.js"
  - "src/sm64_events/ui/components/attemptlog.js"
  - "src/sm64_events/ui/uilog.js"
  - "src/sm64_events/core/uilog.py"
---
# Chain: the star grab's time, from Usamune's RAM to the practice-log row

Read this before editing any module named below. A symptom is seen at the
SINK; its cause sits at the FIRST hop where the value stops being true, and a
fix downstream of that hop cannot hold. Before any fix: run the counterfactual
(force the known-good value in at a hop, re-run to the sink, see whether the
symptom vanishes) — the earliest hop whose correction prevents the failure is
the one to fix. Walk the table only when the counterfactual is ambiguous.

- **Value:** the in-game time a star grab records — Usamune's own x-cam number.
- **Source truth:** Usamune's result store in the emulator's RAM, read by `uv run python tools/verify_star_stop.py`.
- **Sink:** the attempt row on that star's practice-log card (and the desktop window, same components).
- **One clock:** the game frame the grab is stamped with, back-computed to the x-cam. Hops 7-9 join on the broadcast `seq` instead, and that join is NOT unique — `seq` restarts at 0 every server start while `data/ui_log.jsonl` persists, which is why `tools/star_to_screen.py` is deprecated as noise rather than trusted.

| # | hop | value is true here as | module | probe (reads it) | inject (forces it) | when the hop is broken, the probe shows | when the probe itself is broken, it shows |
|---|-----|-----------------------|--------|------------------|--------------------|------------------------------------------|--------------------------------------------|
| 1 | Usamune's result store in emulator RAM | the u16 at `usamune_star_result`, with `usamune_overall` running beside it | `src/sm64_events/memory/layout.py` | `uv run python tools/verify_star_stop.py` — watches 8 s past each grab and prints the store beside his screen | none — domain rule 6 forbids writing emulator memory, so the known-good pair goes in at hop 2 | our number lower than his screen by a non-constant 1-28 frames (2026-08-01), or a store that never leaves 0 | nothing printed for a grab it did see, because no write landed inside `RESULT_FRESH_FRAMES` — silence reads as "he grabbed no stars" |
| 2 | the poll's snapshot | `GameSnapshot.igt_result` and `.igt_overall`, one coherent read per poll | `src/sm64_events/core/snapshot.py`, `src/sm64_events/server/poller.py` | `uv run python tools/sync_version.py --version us --only address.usamune_star_result` | build the pair directly — `snap(igt_result=..., igt_overall=...)` in `tests/test_star_grab.py` | a gate verdict naming a value that never moves or an implausible read, and every grab falling back to `igt_source` `counter` | a SKIPPED verdict with no ROM attached — it cannot tell a wrong address from an absent emulator |
| 3 | the x-cam moment and its number | the dance-entry frame's overall counter + `IgtClock.DISPLAY_TICK`, each subarea leg banked | `src/sm64_events/detectors/igt_clock.py`, `src/sm64_events/detectors/counter_epoch.py` | `uv run python tools/derive_xcam.py` — scores what we journal against Usamune's settled result, GATE PASSED / GATE FAILED | drive `IgtClock` over a hand-built snapshot sequence — `tests/test_igt_clock.py` | GATE FAILED with a per-grab error, or a subarea star hundreds of frames low (356 and 502, 2026-08-01) | GATE PASSED over zero MIDAIR grabs — it scores only what he played, so a ground-only session passes vacuously |
| 4 | the deferred-emit gate | `_ready_to_publish` agreement, else `PUBLISH_WAIT_FRAMES` as the backstop | `src/sm64_events/detectors/star_grab.py` | `uv run python tools/what_happened.py` — every `star_collected` payload carries `published_after` and `result_writes` | feed a hand-built write burst through `run_pairs` in `tests/test_star_grab.py` | `published_after` climbing out of 0-1 into the tens (45 on the first grab after a course entry, 2026-08-02), or a `star_time_corrected` following the publish | the freshest journal named and read — which may be a sibling worktree's, giving a plausible timeline for a session he never played |
| 5 | the journal row | `igt_frames` in the `star_collected` payload, appended once | `src/sm64_events/core/events.py`, `src/sm64_events/storage/db.py` | `uv run python tools/dedupe_journal.py data/tracker.db`, plus a read-only SELECT over the journal `tools/what_happened.py --list` names (a displayed 0'26"13 is 784 frames) | append an edited payload with `db.append_event`, the shape `tests/test_projection.py` uses | no row at the grab frame, or two rows for the one grab | a scan of the repo's journal while he plays on the installed exe's — clean, and about another database |
| 6 | the projector | `Attempt.igt_frames`, with `time_corrections` folded in so every consumer sees one number | `src/sm64_events/tracking/projection.py`, `src/sm64_events/tracking/service.py` | `uv run python tools/measure_target_queue.py` — replays each journal under both revisions and diffs every recorded row's times | re-project a `Connection.backup` copy whose payload you edited | a recorded row whose time moved, or an attempt missing from the after-side | zero differences, because both sides ran the same code (`--before` left at the working tree) |
| 7 | the wire | `/api/session`'s attempt rows, and the WS `star_collected` frame with its `seq` | `src/sm64_events/server/api.py`, `src/sm64_events/server/broadcaster.py` | `GET /api/session` on the port he actually plays on — find it, never assume 8064/8065 | `serve_ui_live(db_path=...)` in `tools/ui_fixture.py` — the real app offline on a seeded snapshot | a row arriving with `igt_frames` null while the journal holds the number | an answer from a different server about a database he is not playing on |
| 8 | the practice-log card | `.attempt-result` text, `igt_frames` formatted at 30 fps | `src/sm64_events/ui/components/practicelog.js`, `src/sm64_events/ui/components/attemptlog.js` | `uv run python tools/contact_sheet.py .log-card` — the card at 1500/1200/900/850, and you LOOK at the time | seed the attempt into the db `tools/ui_fixture.py` snapshots, then render | the RTA number, a `--`, or a time one display tick off | a clean sheet of a fixture card holding no attempts — nobody is looking at the card he reported |
| 9 | what the screen drew | `logs[].rows[]` in `data/ui_log.jsonl`, read back off the DOM | `src/sm64_events/ui/uilog.js`, `src/sm64_events/core/uilog.py` | `uv run python tools/what_happened.py` — journal events and painted rows on one clock | POST a crafted record to `/api/uilog`, the shape `tests/test_uilog.py` uses | the old time still painted after the event, or that card absent from the record | an EMPTY log — the open tab runs JS from before the change, which reads exactly like nothing painted |

## Counterfactual recipe

Take one grab he reported. Force Usamune's known-good number in at hop 2 with
`snap(igt_result=N, igt_overall=M)` and run `run_pairs` to hop 5; if the
journaled `igt_frames` is right, hops 1-4 are cleared in one command. Then put
that same row into a snapshot db, serve it with `serve_ui_live` and shoot
`uv run python tools/contact_sheet.py .log-card`: the symptom vanishing there
puts the boundary between 5 and 8. For a LATENCY symptom the value is right at
every hop — compare `published_after` (hop 4) against the paint stamp (hop 9)
and fix the bigger half.

## Failure catalogue

- 2026-08-01 hop 3 — the x-cam moment was the grab frame, so midair grabs recorded -4, -11, -23 and -39 frames and were leaderboard-INVALID — "the arithmetic was never wrong; the FRAME was" — fix at the dance entry, not in the arithmetic.
- 2026-08-01 hop 3 — a subarea star read 0'40"63 against Usamune's 0'52"46, because `USAMUNE_OVERALL` restarts at an area warp — fix by banking each leg in `igt_clock.py`, never by an offset.
- 2026-08-01 hop 4 — "now the tool feels like it's broken and laggy… we HAVE THE ANSWER RIGHT WHEN THE STAR DANCE HAPPENS" — the emit waited `RESULT_SETTLE_FRAMES` past the x-cam — fix at the gate, as an optimistic publish on agreement.
- 2026-08-01 hop 4 — "it writes the entry into the system… and then the xcam happens, which overrides the original entry… it should be hidden to the user" — publishing on the FIRST write of Usamune's burst, which is the grab-time value — fix: agreement, not existence.
- 2026-08-02 hop 4 — "Everything is *correct* but slow"; every grab published at 1 frame except the first after a course entry, which took 45 — a course entry read as a subarea warp — fix at the epoch classification (`aefcf6d`).
- 2026-08-04 hops 7-9 — "It's still slow, and you did not succeed… Do we have instrumentation for comparing the timegap between detecting the xcam / final time, and when we actually display it in the frontend?" — two rounds aimed by reading code hit a real 1.5 s detector tail that was not what he felt, because the sink-side hops had no probe at all.
- 2026-08-05 hop 4, the sibling gate one detector over (`warp_entered`) — "we should never be injecting that much artificial waiting into the system… That type of lag is never acceptable" — 89% of a measured 2.9 s gap was the hold, not the render.
