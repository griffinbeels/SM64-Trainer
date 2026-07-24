# Failure Compilation — Design Spec

**Date:** 2026-07-23
**Status:** Approved (design), pending implementation plan
**Author:** Claude (brainstorming session)

## 1. Summary

A per-entity **Generate failure compilation** action on the practice card. For a
selected star (or segment) it finds every non-success, non-purged attempt that
still has footage available, cuts a short window around each failure moment,
orders them by **how far into the run the failure happened**, and appends the
**fastest available successful run in full** as the finale — producing a **single
MP4 with audio** the user can drop straight into Premiere Pro for a manual
refinement pass.

The intent: click one button and get "here is every way I fail this star, in the
order they'd occur during a run, ending with the clean fast run." The button
generates *all* the raw material; the taste pass (removing duplicate/uninteresting
failures) is done by hand in an editor afterward — deliberately not automated.

The feature is a **batch orchestration over the existing single-clip
extractor**: `ClipExtractor.extract(ring, start, end, out)` already cuts one
frame-accurate, A/V-synced window from the replay ring, honoring coverage holes
and frame-size changes. A compilation is N failure windows + one finale window,
concatenated and normalized into one file.

## 2. Goals & non-goals

**Goals**
- One button per practiced entity: **Generate failure compilation**.
- User-defined **X seconds before** / **Y seconds after** the failure moment.
- Include **every** non-success attempt for the entity that (a) is not
  cleared/auto-purged and (b) still has footage (in the live ring or a saved clip).
- Order failures by **elapsed time from the run's start** (a death 5 s in comes
  before a death 25 s in) — reconstructing the temporal shape of a run.
- Finale = the **fastest available successful run**, played **in full**, last.
- Output is **one MP4 with audio**, ready for Premiere.
- Show a **link immediately on completion**, with **Reveal in File Explorer**.
- Ships for **both stars and segments** (star↔segment parity rule).
- Honest reporting: failures that aged out of the buffer are **counted and shown**,
  never silently dropped.

**Non-goals (v1)**
- Automated de-duplication or "interesting-ness" filtering of failures — that is
  the manual Premiere pass, by design.
- Per-strategy filtering (all strats for the entity are included; a strat filter
  is a natural later knob).
- Title/label cards or transitions between clips (hard cuts only; the editor
  cuts them apart anyway).
- Re-using the compilation as an in-app scrubbable player artifact (it's an export
  for an external editor; it lives on disk, not in the replay index).

## 3. Key decisions

1. **Failure moment = the attempt's `ended_utc`.** Every attempt closes on its
   failure event (death / F1 reset / hard reset / level-change abandon), so the
   "look around a failure" window is `[ended_utc − X, ended_utc + Y]`. This is a
   *tight window around the death*, distinct from the existing per-attempt replay
   (the whole run).

2. **Ordering key = `ended_utc − started_utc` (elapsed real time from run start),
   ascending.** Always defined for every failure type (unlike IGT, which many
   resets/deaths record as `None`). Ties broken by attempt id for determinism.
   *(User-chosen over IGT-at-failure and chronological.)*

3. **Finale = fastest available success, played in full.** Among the entity's
   successes that are not cleared and have footage, pick the lowest displayed time
   (`igt_frames`, else `rta_frames`). Its window is the **whole run**
   (`[started_utc − pre_pad, ended_utc + post_pad]`), so the compilation ends with
   the complete clean run as the payoff. *(User-chosen over "ending only".)*
   Best-effort: if no success is available, the compilation still generates
   (failures only) and the summary says so.

4. **"In the cache" = footage obtainable right now**, gated differently per kind:
   - **Failures**: the `[end−X, end+Y]` window must be within the **live ring**.
     (A saved clip is the *whole run*; cutting a tight failure sub-window out of a
     saved `.mp4` needs UTC→offset math the builder doesn't have, and users rarely
     *save* failure clips — so saved-file failure windows are explicitly deferred.)
   - **Finale**: ring full-run coverage **OR a saved clip** — a saved clip already
     *is* the whole-run finale span, so it's fed to the concat directly with no
     ring cut. This is the important case: PBs get saved and often age out of the
     ring, so a saved PB stays usable as the finale.
   Attempts failing their gate are excluded, **counted**, and surfaced in the
   result summary. Mirrors `available_attempt_ids` (recomputed live — the ring
   shifts). The ring bound is the outer envelope; interior coverage holes are
   handled at extract time (see §7 runtime skip).

5. **Reuse `ClipExtractor.extract` verbatim; normalize+concat in a second ffmpeg
   pass.** Clips span multiple sessions and window resizes, so their resolutions
   differ. A `-c copy` concat would corrupt on a size mismatch (the app already
   treats a frame-size change as run-breaking). The builder therefore cuts each
   window with the existing extractor, then runs **one concat-filter pass** that
   scales+pads every clip to a common canvas (letterboxed, never squashed) and
   re-encodes to one MP4 at `config.py`'s constant-quality target. The second
   encode is an accepted cost: this is throwaway raw material for an editor, and
   reusing the shared extractor unchanged avoids forking its subtle
   dims/coverage/A-V-sync logic. *(Single-encode via a `scale_to` param on the
   extractor is a later optimization, explicitly deferred.)*

6. **Async job, mirroring `CompareService`.** Dozens of ffmpeg cuts plus a concat
   pass take real time. `POST` returns a `job_id` immediately; a daemon thread
   runs plan+build updating `progress`/`message`/`state`; the client polls by id.
   The result link appears when the job reaches `done`.

7. **Kind-dispatched request body** (`{star:{course_id,star_id}}` **or**
   `{segment_id}`), matching the app's other star↔segment endpoints, so one code
   path serves both and can't drift.

8. **Canvas = the finale clip's resolution** (fallback: first clip's dims, then
   `config.py` default). The hero run defines the frame; failures are letterboxed
   to fit.

## 4. Architecture & data flow

```
practice card (star OR segment)
  └─ FailureCompilation component (X/Y inputs, Generate button)
       └─ POST /api/compilation  {identity, x_before, y_after}
            └─ CompilationService.start(identity, x, y) -> job_id     (returns immediately)
                 └─ daemon thread:
                      plan_compilation(attempts, ring.coverage, saved_ids, identity, x, y)
                         -> ordered [ClipSpec...] + SkipReport            (pure, tracking/compilation.py)
                      for each spec:  ClipExtractor.extract(ring, span) -> tmp_i.mp4   (reuse)
                      concat_normalize([tmp...], canvas) -> compilation_<slug>_<ts>.mp4 (new ffmpeg pass)
                      job.state = "done"; job.result = {path, clip_count, skipped, finale}
       └─ GET /api/compilation/{job_id}  (poll -> progress -> done)
       └─ result panel: summary + [Reveal in Explorer] -> POST /api/replay/reveal {path}
```

## 5. Module layout

| File | Responsibility |
|---|---|
| `tracking/compilation.py` | **new, pure.** `plan_compilation(...)` → ordered `ClipSpec` list + `SkipReport`; dataclasses `ClipSpec` (attempt_id, kind `failure`/`finale`, `source` `ring`/`saved`, span_start, span_end, saved_path, sort_key, label) and `CompilationPlan` (specs + skipped counts + finale identity). No ffmpeg, no I/O — the bulk of the logic, fully unit-tested. |
| `replay/compilation.py` | **new.** `CompilationBuilder` (per-spec `extract` → `concat_normalize` ffmpeg pass; canvas resolution; partial-file safety) and `CompilationService` (job registry + daemon thread + `start`/`status`, mirroring `CompareService`). |
| `server/compilation_api.py` | **new.** `POST /api/compilation`, `GET /api/compilation/{job_id}`. Same LookupError→404 / ValueError→409 / RuntimeError→503 taxonomy. Reveal reuses `POST /api/replay/reveal`. |
| `core/paths.py` | **+** `compilations_dir()` → `save_root/compilations` (inside `save_root`, so the existing `reveal` path-check already permits it). |
| `ui/components/failcomp.js` | **new.** Shared `FailureCompilation({identity,...})`: X/Y inputs (localStorage defaults), Generate button, progress line, result panel + Reveal. |
| `ui/components/practice.js` | Mount `FailureCompilation` in **both** the Star and Segment cards. |
| `main.py` | Construct `CompilationService` (shares `recorder`/ring, `extractor`, `tracker`), mount router. |

## 6. Selection & ordering — `plan_compilation` (pure)

Signature (conceptual):
`plan_compilation(attempts, buffer_coverage, saved_ids, identity, x_before, y_after, pre_pad, post_pad) -> CompilationPlan`

1. **Filter to the entity.** Star: `course_id == c and star_id == s and segment_id is None`. Segment: `segment_id == id`.
2. **Failures** = entity attempts with `outcome ∈ {reset, hard_reset, abandoned, death}` and `not cleared`.
3. **Availability gate per failure** (window `[end−X, end+Y]`): `buffer_coverage`
   fully contains the window (`cov_start ≤ end−X` and `end+Y ≤ cov_end`). Failures
   failing the gate go to `SkipReport.aged_out` (with a count). All included
   failures are `source="ring"`.
4. **Order** included failures by `sort_key = (ended_utc − started_utc, attempt_id)` ascending. `ClipSpec.span = [end−X, end+Y]`, `kind="failure"`.
5. **Finale**: among entity successes with `not cleared` AND a resolvable displayed
   time (`igt_frames` else `rta_frames`; both `None` → ineligible), take them
   fastest-first and pick the first that is **available**:
   - full run `[start−pre_pad, end+post_pad]` within `buffer_coverage` →
     `source="ring"`, span = whole run; else
   - `attempt_id in saved_ids` → `source="saved"`, `saved_path` set (fed to concat
     directly, no ring cut).
   Emit that one `ClipSpec` `kind="finale"`. If no success is available,
   `finale=None` and `SkipReport.no_finale=True`.
6. Return `CompilationPlan(specs=[…failures ordered…, finale?], skipped=SkipReport, finale_identity)`.

Empty result (no available failures **and** no finale) → the service raises
`ValueError("nothing to compile")` → 409.

## 7. Building — `CompilationBuilder` (`replay/compilation.py`)

`build(plan, out_path, progress_cb) -> CompilationResult`:
1. For each `ClipSpec` in order, resolve it to a temp input file; `progress_cb(i/N, "cutting i/N…")`:
   - `source="ring"` → `ClipExtractor.extract(ring, span_start, span_end, tmp_i)`.
     A spec whose window fails to extract (ValueError: no overlap) is dropped and
     added to a runtime skip list (belt-and-suspenders vs. the plan-time gate,
     since the ring can shift mid-build).
   - `source="saved"` → use `saved_path` directly as the concat input (no cut).
2. **Canvas** = finale clip's `dims` (ring: from its covering `SegmentInfo.dims`; saved: probed from the file), else first clip's, else `config.py` default.
3. **`concat_normalize(tmp_clips, canvas, out_path)`** — one ffmpeg call:
   `filter_complex` per input `scale=W:H:force_original_aspect_ratio=decrease, pad=W:H:(ow-iw)/2:(oh-ih)/2, setsar=1`, audio `aresample=async=1`, then `concat=n=N:v=1:a=1`; video re-encoded via `video_quality_args`, `+faststart`. Partial-file safety: unlink `out_path` on ffmpeg failure (same discipline as `extract.py`).
4. Return `CompilationResult(path, clip_count, skipped, finale_time)`.

Output name: `compilation_<entity-slug>_<YYYYMMDD-HHMMSS>.mp4` in `compilations_dir()`,
where `<entity-slug>` reuses `service.slug` semantics (course+star or segment name).

## 8. Async job + REST (`CompilationService`, `server/compilation_api.py`)

Mirrors `CompareService` exactly:
- `self._jobs: dict[str, dict]`; `start(...)` → `uuid4().hex`, seeds
  `{state:"running", progress:0.0, message:"planning…"}`, spawns a daemon thread,
  returns the id.
- Worker: plan → build with a `progress(frac,msg)` closure writing into the job;
  on success `job["result"] = {...}`, `state="done"`; on exception
  `state="error"`, `message=str(e)`.
- `status(job_id)` → shallow copy; `LookupError` for unknown id.

Endpoints:
- `POST /api/compilation` body `{star:{course_id,star_id}} | {segment_id}`,
  `x_before: float`, `y_after: float` → `{job_id}`. Validates X/Y ≥ 0 and the
  identity resolves (ValueError → 409).
- `GET /api/compilation/{job_id}` → the job dict. On `done`: `result` has `path`,
  `clip_count`, `skipped:{aged_out:int, no_finale:bool}`, `finale:{time_str}`.
- Reveal: existing `POST /api/replay/reveal {path}` (compilation lives under
  `save_root`, so the path check already passes).

## 9. UI (`ui/components/failcomp.js`, shared)

`FailureCompilation({identity, entityLabel})`:
- **X before / Y after** number inputs, defaults 5 / 3, persisted in
  `localStorage` (`sm64.failcomp.xBefore` / `.yAfter`).
- **Generate failure compilation** button → `POST /api/compilation` → poll
  `GET /api/compilation/{job_id}` (~1 s) showing `message` ("cutting 7/24…").
- On `done`: a result panel —
  *"24 failures + fastest run (0'19″43) → compilation_… .mp4 · 5 failures skipped
  (aged out of buffer)"* — with a **Reveal in Explorer** button
  (`POST /api/replay/reveal`).
- On `error`: shows the message; button re-enabled.

Mounted in **both** the Star card and the Segment card in `practice.js` (like
`stratpicker.js`), so `tests/test_ui_section_parity.py` fails if it ever renders
in only one. Browser ↔ GUI parity is automatic (same `ui/`, same server).

## 10. Edge cases

- **No failures with footage and no finale** → 409 "nothing to compile" (button
  shows the message).
- **Failures but no available success** → failures-only compilation; summary notes
  no finale was found in the buffer.
- **X/Y wider than the recorder's idle window** → post-failure footage may be
  idle-discarded; the extractor clamps and marks that clip truncated (already
  handled) — the clip is still included, just shorter.
- **Ring shifts mid-build** → a per-spec extract that now fails is dropped and
  reported (runtime skip), the compilation completes with the rest.
- **A single clip lacking an audio stream** (should not happen — segments always
  carry audio) would break `concat …:a=1`; the builder maps `0:a:0` per input and
  we accept the theoretical edge (documented) rather than injecting silence in v1.
- **Compilations counted in `settings().saved_bytes`** (they live under
  `save_root`) — acceptable; they *are* saved output.

## 11. Testing

- `tests/test_compilation.py` — pure `plan_compilation`: entity filtering, the
  failure outcome set, cleared exclusion, elapsed-time ordering + tie-break,
  per-failure coverage gating, aged-out skip counts, finale = fastest available
  (igt vs rta; ring source vs saved-clip fallback; cleared/absent finale),
  "nothing to compile".
- `tests/test_compilation_builder.py` — `CompilationBuilder` with a **fake
  extractor** and a captured ffmpeg runner: correct per-spec order, canvas =
  finale dims, concat-filter arg shape, partial-file unlink on failure, runtime
  skip on a mid-build extract failure.
- `tests/test_compilation_service.py` — job lifecycle (running→done, error path,
  unknown id), mirroring the compare job tests.
- `tests/test_compilation_api.py` — kind dispatch (star vs segment), X/Y
  validation, status codes.
- `tests/test_ui_section_parity.py` — extended to require `FailureCompilation` in
  both practice cards.

## 12. Definition of done

- `uv run pytest -q` passes; new behavior covered per §11.
- Live check with the human: generate a real star compilation, confirm ordering
  matches run-progress intuition, audio present, finale is the fastest run, Reveal
  opens Explorer on the file.
- CLAUDE.md module map updated (new files + the `compilations_dir` path row);
  README updated for the new REST surface; this spec linked from the plan.
