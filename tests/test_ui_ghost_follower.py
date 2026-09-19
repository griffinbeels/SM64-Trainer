"""Drive the ghost follower over clock traces and score its error IN GAME FRAMES.

The follower is the whole sync design in one pure module: the ghost is not a
video that plays, it is a function of the live attempt's clock. Every question
the feature raises -- reset, pause, the Usamune menu, a save-state load, the
timing nudge -- is the same question here, "which template picture does this
counter value name", so each one is a trace rather than a detector.

WHY A NODE HARNESS AND NOT A BROWSER. The thing under test is a decision per
game frame, and the number that says whether it works is how many frames the
ghost is off by. A browser can show that the page moves; only a driven trace
can say it was never more than a frame out across three hundred of them. The
`<video>` is modelled (a seek sets the time, playing advances it by
rate x dt), which is exactly what Chromium does apart from decode latency --
so these numbers are the follower's own error and NOT an end-to-end latency
measurement. `tests/test_ui_ghost_page.py` drives the real element.

The scores this file prints are in the report; read `SCENARIOS` for what each
trace does to the clock.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FOLLOWER = (REPO / "src" / "sm64_events" / "ui" / "ghost" / "follower.js").as_uri()

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")


def _clip_roots() -> list[Path]:
    """Where this machine's own footage sits, READ-ONLY. A worktree has its
    own (usually empty) `replays/`, so the primary checkout -- the one the
    human actually plays on -- is the interesting one.

    `SM64_GHOST_CLIP_ROOTS` (os.pathsep-separated) aims this check at another
    checkout's recordings without copying them: the clips from his first try
    live in the FEATURE worktree, because that is where he was recording, and
    this check is the promise that the follower works on his real footage.
    Nothing here ever writes to a clip root.
    """
    override = os.environ.get("SM64_GHOST_CLIP_ROOTS", "").strip()
    if override:
        return [Path(part) for part in override.split(os.pathsep) if part]
    roots = [REPO / "replays"]
    try:
        common = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return roots
    if common:
        roots.append(Path(common).parent / "replays")
    return roots


# A real capture-layer clip advances the game 30.0009 frames/second with its
# pictures ~1/30 s apart (measured on attempt 35076, 2026-09-17), so the trace
# steps the world at the game's own rate and the clip's time axis IS wall time.
HARNESS = r"""
import { createFollower, ghostTemplate, NEAR_ZERO_IGT } from "__FOLLOWER__";

const DT = 1 / 30;                      // one game frame of wall time
const out = {};

// A picture-feed template: one picture per game frame, at its own timestamp,
// with the jitter a real capture has (measured: 28.1-38.2 ms between
// pictures against a 33.3 ms mean).
function feedTemplate(frames, startIgt = 0) {
  const times = [], igt = [];
  let t = 0;
  for (let k = 0; k < frames; k += 1) {
    times.push(Number(t.toFixed(6)));
    igt.push(startIgt + k);
    t += DT + (((k * 7919) % 11) - 5) * 0.001;   // deterministic jitter
  }
  return { frame_times: times, picture_igt: igt, duration_s: t,
           video_start_s: 0, fps: 60, game_fps: 30, encode: "picture_feed" };
}

// THE SHAPE OF EVERY REAL SAVED CLIP: it begins before the reset that started
// the attempt, and through that reload the counter still holds the PREVIOUS
// run's final value. So the clip opens with pictures stamped with a number
// from a finished run and then drops to near zero. (His first real template,
// star 6-0: 44 pictures frozen at 3029, then 3 climbing to 584.)
function prerollTemplate(preroll, frames, held = 3029, startIgt = 3) {
  const view = feedTemplate(preroll + frames);
  view.picture_igt = view.picture_igt.map(
    (_, k) => (k < preroll ? held : startIgt + (k - preroll)));
  return view;
}

// A constant-rate clip has no frame_times at all: every slot is
// video_start_s + k/fps, and the cut leaves its remainder on the first
// picture. Two encoded slots per game frame at 60 fps.
function cfrTemplate(frames, startS) {
  const igt = [];
  for (let k = 0; k < frames; k += 1) igt.push(Math.floor(k / 2));
  return { frame_times: null, picture_igt: igt, duration_s: startS + frames / 60,
           video_start_s: startS, fps: 60, game_fps: 30, encode: "cfr" };
}

function fakeVideo() {
  return { currentTime: 0, playbackRate: 1, paused: true, seeks: 0 };
}
function apply(video, decision) {
  if (decision.action === "seek" && decision.time != null) {
    video.currentTime = decision.time;
    video.seeks += 1;
  }
  if (decision.rate > 0) { video.playbackRate = decision.rate; video.paused = false; }
  else video.paused = true;
}

// Run one trace. `trace` is the clock as the server would push it.
function drive(view, trace, options = {}) {
  const follower = createFollower(view, options);
  // The SAME template the follower built, span and all -- scoring against a
  // differently-bounded one would measure the harness.
  const template = ghostTemplate(view, options.span || null);
  const video = fakeVideo();
  const rows = [];
  let now = 0;
  for (const sample of trace) {
    const decision = follower.sample(sample, video.currentTime, now);
    if (sample.nudge !== undefined) follower.setNudge(sample.nudge);
    apply(video, decision);
    // What is ON SCREEN for this game frame: the template picture the video
    // now sits on, against the picture the rule says it should show.
    const shown = template.igtAtTime(video.currentTime);
    const wanted = sample.igt + follower.nudge;
    // `outside` is the claim his ruling turns on and it is a SLOT question:
    // comparing clip times cannot answer it, because `timeOfSlot` returns the
    // middle of a picture's interval, so a video a fraction past the last
    // picture's midpoint is still ON that picture.
    rows.push({ igt: sample.igt, wanted, shown, time: video.currentTime,
                outside: template.outsideRun(video.currentTime),
                error: shown == null ? null : shown - wanted,
                paused: video.paused, rate: Number(video.playbackRate.toFixed(4)),
                action: decision.action, reason: decision.reason });
    if (!video.paused) video.currentTime += video.playbackRate * DT;
    now += DT * 1000;
  }
  return { rows, seeks: video.seeks };
}

const score = (rows, from = 0) => {
  const slice = rows.slice(from).filter((r) => r.error != null);
  const errs = slice.map((r) => Math.abs(r.error));
  return { worst: errs.length ? Math.max(...errs) : null,
           last: slice.length ? slice[slice.length - 1].error : null,
           over_one: errs.filter((e) => e > 1).length, samples: slice.length };
};
// How many samples after `at` before the ghost is back within one frame.
const recovery = (rows, at) => {
  for (let k = at; k < rows.length; k += 1)
    if (rows[k].error != null && Math.abs(rows[k].error) <= 1) return k - at;
  return null;
};

const steady = (n, start = 0) =>
  Array.from({ length: n }, (_, k) => ({ igt: start + k, global_timer: 1000 + k,
                                         level: 8, area: 1 }));

// -- 1. steady play --------------------------------------------------------
{
  const view = feedTemplate(400);
  const { rows, seeks } = drive(view, steady(360));
  out.steady = { ...score(rows, 2), seeks };
}

// -- 2. the timer stalls (pause screen, Usamune menu) ----------------------
{
  const view = feedTemplate(400);
  const trace = steady(100);
  for (let k = 0; k < 60; k += 1)
    trace.push({ igt: 99, global_timer: 1100 + k, level: 8, area: 1 });
  for (let k = 0; k < 100; k += 1)
    trace.push({ igt: 100 + k, global_timer: 1160 + k, level: 8, area: 1 });
  const { rows } = drive(view, trace);
  const stalled = rows.slice(101, 160);
  out.stall = { ...score(rows, 2),
                held_paused: stalled.every((r) => r.paused),
                held_error: Math.max(...stalled.map((r) => Math.abs(r.error))),
                after_resume: score(rows, 162).worst,
                resume_recovery: recovery(rows, 160) };
}

// -- 3. reset: the counter returns to zero ---------------------------------
{
  const view = feedTemplate(400);
  const trace = steady(200);
  for (let k = 0; k < 30; k += 1)
    trace.push({ igt: 0, global_timer: 1200 + k, level: 8, area: 1 });
  for (let k = 0; k < 120; k += 1)
    trace.push({ igt: k, global_timer: 1230 + k, level: 8, area: 1 });
  const { rows } = drive(view, trace);
  const waiting = rows.slice(201, 230);
  out.reset = { recovery: recovery(rows, 200),
                at_zero: rows[200].error,
                held_paused: waiting.every((r) => r.paused),
                spawn_frame: rows[230].shown,
                after: score(rows, 232) };
}

// -- 4. a save-state load BACKWARD ----------------------------------------
{
  const view = feedTemplate(400);
  const trace = steady(300);
  for (let k = 0; k < 100; k += 1)
    trace.push({ igt: 150 + k, global_timer: 1300 + k, level: 8, area: 1 });
  const { rows } = drive(view, trace);
  out.back_jump = { recovery: recovery(rows, 300), at_jump: rows[300].error,
                    after: score(rows, 303) };
}

// -- 4b. a load SHORTER than the drift the rate correction absorbs ---------
// The one case only the discontinuity rule can see. A few frames of drift is
// something steady play walks off over half a second; a few frames of LOAD is
// the game somewhere else, and the ghost has to be there this frame.
{
  const view = feedTemplate(400);
  const trace = steady(200);
  for (let k = 0; k < 60; k += 1)
    trace.push({ igt: 197 + k, global_timer: 1200 + k, level: 8, area: 1 });
  const { rows } = drive(view, trace);
  out.short_load = { recovery: recovery(rows, 200), at_jump: rows[200].error,
                     reason: rows[200].reason, after: score(rows, 203) };
}

// -- 5. a save-state load FORWARD -----------------------------------------
{
  const view = feedTemplate(500);
  const trace = steady(100);
  for (let k = 0; k < 100; k += 1)
    trace.push({ igt: 250 + k, global_timer: 1100 + k, level: 8, area: 1 });
  const { rows } = drive(view, trace);
  out.forward_jump = { recovery: recovery(rows, 100), at_jump: rows[100].error,
                       after: score(rows, 103) };
}

// -- 6. the player nudges the ghost ---------------------------------------
{
  const view = feedTemplate(500);
  const trace = steady(300);
  trace[100].nudge = 30;
  trace[200].nudge = -15;
  const { rows } = drive(view, trace);
  out.nudge = { after_plus: recovery(rows, 101), after_minus: recovery(rows, 201),
                plus_wanted: rows[110].wanted - rows[110].igt,
                minus_wanted: rows[210].wanted - rows[210].igt,
                after: score(rows, 205) };
}

// -- 7. a constant-rate clip whose first picture is not at 0:00 ------------
{
  const view = cfrTemplate(800, 0.011003);
  const { rows } = drive(view, steady(360));
  out.cfr_offset_clip = { ...score(rows, 2),
                          first_time: ghostTemplate(view).firstTime };
}

// -- 8. the player is somewhere else --------------------------------------
{
  const view = feedTemplate(400);
  const trace = steady(60);
  for (let k = 0; k < 60; k += 1)
    trace.push({ igt: 60 + k, global_timer: 1060 + k, level: 16, area: 1 });
  const { rows } = drive(view, trace, { expect: { level: 8, area: 1 } });
  out.elsewhere = { paused_away: rows.slice(61).every((r) => r.paused),
                    reason: rows[80].reason,
                    played_at_home: rows.slice(2, 59).every((r) => !r.paused) };
}

// -- 9. a template whose counter restarts AFTER its own start is refused ---
{
  // A state load inside the recording: the counter drops, but nowhere near
  // zero, so it is not a run starting.
  const view = feedTemplate(200, 400);
  view.picture_igt = view.picture_igt.map((v, k) => (k < 100 ? v : v - 50));
  const t = ghostTemplate(view);
  out.loaded_template = { ok: t.ok, reason: t.reason };
}
{
  // An AREA-CROSSING star: Usamune restarts the counter per leg, so the clip
  // holds the pre-roll restart AND a second one mid-run. One counter value
  // then names two pictures and the ghost refuses rather than picking one.
  const view = prerollTemplate(40, 300);
  view.picture_igt = view.picture_igt.map(
    (v, k) => (k >= 200 ? k - 200 + 1 : v));
  const t = ghostTemplate(view);
  out.two_restart_template = { ok: t.ok, reason: t.reason };
}
{
  const view = feedTemplate(100);
  view.picture_igt = null;
  const t = ghostTemplate(view);
  out.mapless_template = { ok: t.ok, reason: t.reason };
}

// -- 10. the ordinary clip: pre-roll, then the attempt ---------------------
{
  const view = prerollTemplate(44, 400);
  const t = ghostTemplate(view);
  const { rows, seeks } = drive(view, steady(360, 3));
  out.preroll = { ok: t.ok, reason: t.reason, pictures: t.pictures,
                  preroll_pictures: t.prerollPictures, first_igt: t.firstIgt,
                  ...score(rows, 2), seeks,
                  // Nothing the ghost showed came from the run before this one,
                  // and it never sat earlier in the clip than the attempt's
                  // own first picture.
                  ever_preroll: rows.some((r) => r.shown === 3029),
                  earliest: Math.min(...rows.map((r) => r.time)),
                  first_time: t.firstTime };
}

// -- 11. a reset mid-run goes back to the POST-restart picture -------------
{
  const view = prerollTemplate(44, 400);
  const t = ghostTemplate(view);
  const trace = steady(200, 3);
  // The reload: the counter sits in the near-zero band for a while, below the
  // template's own first value.
  for (let k = 0; k < 30; k += 1)
    trace.push({ igt: 0, global_timer: 1200 + k, level: 8, area: 1 });
  for (let k = 0; k < 120; k += 1)
    trace.push({ igt: 3 + k, global_timer: 1230 + k, level: 8, area: 1 });
  const { rows } = drive(view, trace);
  const waiting = rows.slice(201, 230);
  out.preroll_reset = { at_reset: rows[200].shown, reason: rows[200].reason,
                        held_paused: waiting.every((r) => r.paused),
                        held_on_first: waiting.every((r) => r.shown === t.firstIgt),
                        ever_preroll: rows.some((r) => r.shown === 3029),
                        recovery: recovery(rows, 230), after: score(rows, 232) };
}

// A clip with lead-in AND a tail: the previous run's last pictures, the
// attempt, then the star cutscene holding the final counter value. What a
// recording keeps around the attempt is the player's setting; the span is the
// attempt itself.
// `after` says what the tail carries: "hold" is the star cutscene keeping the
// final value (his own clip's shape), "next_run" is a recording long enough to
// have caught the START of the next attempt -- a second run's counter climbing
// from near zero inside the same clip.
function spannedTemplate(preroll, frames, tail, after = "hold") {
  const [held, startIgt] = [3029, 3];
  const view = feedTemplate(preroll + frames + tail);
  const last = startIgt + frames - 1;
  view.picture_igt = view.picture_igt.map((_, k) => {
    if (k < preroll) return held;
    if (k < preroll + frames) return startIgt + (k - preroll);
    return after === "hold" ? last : 1 + (k - preroll - frames);
  });
  return { view, span: { first_picture: preroll,
                         last_picture: preroll + frames - 1,
                         basis: "attempt_frames" } };
}

// -- 12. what the clip is showing before anything has placed it ------------
// A page that has just loaded sits at clip time 0, and in a real clip that is
// the PRE-ROLL. Asked what is on screen, the template answers with the
// earliest picture it may show, never with a number from the previous run --
// which would report the ghost as half an hour out and send a live diagnosis
// looking for a broken clock.
{
  const view = prerollTemplate(44, 400);
  const t = ghostTemplate(view);
  const follower = createFollower(view, {});
  const first = follower.sample({ igt: 200, global_timer: 1000, level: 8, area: 1 },
                                0, 0);
  out.unplaced = { shown_at_zero: t.igtAtTime(0), first_igt: t.firstIgt,
                   first_drift: first.drift };
}

// -- 13. the source's own span clamps BOTH ends ----------------------------
{
  const { view, span } = spannedTemplate(44, 400, 60);
  const t = ghostTemplate(view, span);
  // Played from the attempt's start to well past its end: the last 40 samples
  // are all past the template's final picture.
  const { rows } = drive(view, steady(440, 3), { span });
  const past = rows.slice(405);
  out.span = { ok: t.ok, reason: t.reason, pictures: t.pictures, basis: t.basis,
               first_igt: t.firstIgt, last_igt: t.lastIgt,
               first_time: t.firstTime, last_time: t.lastTime,
               // Nothing outside the attempt was ever on screen: not a picture
               // from the run before it, not one from the star cutscene.
               ever_outside: rows.some((r) => r.outside),
               ever_preroll: rows.some((r) => r.shown === 3029),
               ...score(rows.slice(2, 400), 0),
               // Past the end it parks on the last picture and stays there.
               past_reason: past[10].reason,
               past_paused: past.every((r) => r.paused),
               past_on_last: past.every((r) => r.shown === t.lastIgt) };
}
{
  // The same clip WITHOUT a span: the counter rule finds the start, and the
  // end is the clip's own last picture -- the cutscene tail is playable.
  const { view } = spannedTemplate(44, 400, 60);
  const t = ghostTemplate(view, null);
  out.span_absent = { ok: t.ok, basis: t.basis, pictures: t.pictures,
                      preroll_pictures: t.prerollPictures,
                      first_igt: t.firstIgt, last_igt: t.lastIgt };
}
{
  // The recording was long enough to catch the START of the next attempt.
  // Asked what is on screen at a clip time past the run, the template answers
  // with its own last picture -- never with the counter of a different
  // attempt that happens to be encoded there. Without the span there is no
  // way to tell the two runs apart, and it is refused.
  const { view, span } = spannedTemplate(44, 400, 60, "next_run");
  const t = ghostTemplate(view, span);
  out.span_trims_next_run = {
    ok: t.ok, pictures: t.pictures, last_igt: t.lastIgt,
    shown_past_end: t.igtAtTime(t.lastTime + 1),
    tail_igt: view.picture_igt[view.picture_igt.length - 1],
    without_span: ghostTemplate(view, null).reason };
}
{
  // A span naming pictures the capture never stamped says so by name rather
  // than falling back to the whole clip, which would show the lead-in.
  const { view } = spannedTemplate(44, 400, 60);
  view.picture_igt = view.picture_igt.map((v, k) => (k >= 44 && k <= 60 ? null : v));
  const t = ghostTemplate(view, { first_picture: 44, last_picture: 60 });
  out.span_empty = { ok: t.ok, reason: t.reason };
}

// -- 12. HE grabs the star first -------------------------------------------
//
// His ruling: *"The ghost should close when I grab the star (sometimes I might
// be faster than the ghost!)."* The clock says `star_grab` while Mario is in
// an action a grab puts him in; it holds through the dance and then GOES AWAY
// while he is still standing there with the counter running, which is why the
// follower latches it rather than reading it per sample.
{
  const view = feedTemplate(400);
  const trace = steady(120);
  // The dance: 90 samples of the action, then it clears and he walks around
  // for another 90 with the counter still climbing.
  for (let k = 0; k < 90; k += 1)
    trace.push({ igt: 120 + k, global_timer: 1120 + k, level: 8, area: 1,
                 star_grab: true });
  for (let k = 0; k < 90; k += 1)
    trace.push({ igt: 210 + k, global_timer: 1210 + k, level: 8, area: 1,
                 star_grab: false });
  // Then he resets, and the ghost is owed back.
  for (let k = 0; k < 20; k += 1)
    trace.push({ igt: 0, global_timer: 1300 + k, level: 8, area: 1 });
  for (let k = 0; k < 80; k += 1)
    trace.push({ igt: k, global_timer: 1320 + k, level: 8, area: 1 });
  const { rows } = drive(view, trace);
  const after = rows.slice(120, 300);
  out.his_grab = {
    before: rows[119].reason,
    at_touch: rows[120].reason,
    // Never plays again between the touch and the reset -- including the
    // ninety samples after the action itself has cleared.
    held_paused: after.every((r) => r.paused),
    reasons_after: [...new Set(after.map((r) => r.reason))],
    // ...and it is back after the reset, following him again.
    after_reset: score(rows, 325),
    played_again: rows.slice(325).some((r) => !r.paused),
  };
}
{
  // A SEGMENT does not end on a star grab: some run through one on the way
  // somewhere, and taking the ghost down there deletes the half he was
  // practising. Same trace, `grabEndsRun: false`.
  const view = feedTemplate(400);
  const trace = steady(120);
  for (let k = 0; k < 90; k += 1)
    trace.push({ igt: 120 + k, global_timer: 1120 + k, level: 8, area: 1,
                 star_grab: true });
  const { rows } = drive(view, trace, { grabEndsRun: false });
  out.segment_grab = { ...score(rows, 122),
                       reasons: [...new Set(rows.slice(122).map((r) => r.reason))],
                       kept_playing: rows.slice(122).every((r) => !r.paused) };
}
{
  // A state load FORWARD past the end of the template does not raise the
  // ghost again: there is nothing left for it to show, so a placement that
  // lands outside the run leaves the latch set.
  const view = feedTemplate(200);
  const trace = steady(120);
  for (let k = 0; k < 10; k += 1)
    trace.push({ igt: 120 + k, global_timer: 1120 + k, level: 8, area: 1,
                 star_grab: true });
  for (let k = 0; k < 40; k += 1)
    trace.push({ igt: 900 + k, global_timer: 1130 + k, level: 8, area: 1 });
  const { rows } = drive(view, trace);
  out.grab_then_load_past_the_end = {
    reasons: [...new Set(rows.slice(131).map((r) => r.reason))] };
}

// -- 13. the course's own subareas are the course --------------------------
// His report was LLL: the ghost kept playing after he died out of the course.
// The gate names the LEVEL and leaves the area open, so walking into the
// volcano (same level, another area) never takes the ghost down, and dying to
// the castle does -- and returning brings it straight back.
{
  const view = feedTemplate(400);
  const at = (igt, level, area) =>
    ({ igt, global_timer: 1000 + igt, level, area });
  const trace = [];
  for (let k = 0; k < 60; k += 1) trace.push(at(k, 22, 1));        // LLL
  for (let k = 0; k < 60; k += 1) trace.push(at(60 + k, 22, 2));   // volcano
  for (let k = 0; k < 60; k += 1) trace.push(at(120 + k, 16, 1));  // died out
  for (let k = 0; k < 60; k += 1) trace.push(at(180 + k, 22, 1));  // walked in
  const { rows } = drive(view, trace, { expect: { level: 22, area: null } });
  out.subarea = {
    in_course: score(rows.slice(2, 59)).worst,
    subarea_played: rows.slice(62, 119).every((r) => !r.paused),
    subarea_reasons: [...new Set(rows.slice(62, 119).map((r) => r.reason))],
    away_paused: rows.slice(120, 180).every((r) => r.paused),
    away_reason: rows[150].reason,
    // Back in the course: the very next sample PLACES it rather than holding,
    // and the reason it carries is not "elsewhere" -- a page reading that
    // keeps the window down for a ghost that is already right.
    back_action: rows[180].action,
    back_reason: rows[180].reason,
    back_recovery: recovery(rows, 181),
  };
}

out.near_zero = NEAR_ZERO_IGT;
console.log(JSON.stringify(out));
"""


def _drive(script: str) -> dict:
    """Run one harness script in node and read its answer.

    Over STDIN, not `-e`: a script carrying a real clip's arrays is an
    argument, and Windows caps a command line at ~32k. His Wet-Dry World
    100-coin clip (3,232 pictures) is past that, so the check died with
    `[WinError 206] The filename or extension is too long` -- a failure about
    argument length, on the one clip big enough to be interesting.
    `tests/test_ui_ghost_session.py` already feeds node this way.
    """
    proc = subprocess.run(["node", "--input-type=module", "-"], input=script,
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def scores() -> dict:
    return _drive(HARNESS.replace("__FOLLOWER__", FOLLOWER))


def test_steady_play_never_falls_a_frame_behind(scores):
    """The clip's time axis is the game's own wall clock, so playing at rate
    1 is playing at game speed -- the correction only has to absorb the
    emulator running slightly off 30 fps."""
    steady = scores["steady"]
    assert steady["worst"] <= 1, steady
    assert steady["over_one"] == 0, steady
    # Steady play is not a seek loop: the whole trace is one initial seek.
    assert steady["seeks"] <= 2, steady


def test_a_stalled_timer_holds_the_picture(scores):
    """Pause screen, Usamune menu, a dialogue box: the counter stops and the
    ghost stops with it, with no pause detector anywhere."""
    stall = scores["stall"]
    assert stall["held_paused"] is True, stall
    assert stall["held_error"] <= 1, stall
    assert stall["resume_recovery"] is not None and stall["resume_recovery"] <= 2, stall
    assert stall["after_resume"] <= 1, stall


def test_a_reset_pre_seeks_to_the_templates_spawn_and_waits(scores):
    """The counter returning to zero IS the reset signal. The ghost is parked
    on the template's own first picture during the reload and starts on the
    spawn frame, so the two are in step from the first frame of play."""
    reset = scores["reset"]
    assert reset["recovery"] is not None and reset["recovery"] <= 2, reset
    assert reset["held_paused"] is True, reset
    assert reset["spawn_frame"] == 0, reset
    assert reset["after"]["worst"] <= 1, reset


def test_a_backward_state_load_seeks(scores):
    load = scores["back_jump"]
    assert load["recovery"] is not None and load["recovery"] <= 2, load
    assert load["after"]["worst"] <= 1, load


def test_a_short_load_snaps_instead_of_being_walked_off(scores):
    """A load of a few frames is the case the drift correction is blind to:
    three frames of DRIFT is something steady play absorbs over half a second,
    three frames of LOAD is the game somewhere else right now. Measured: with
    the discontinuity rule removed the ghost took 15 samples to walk back and
    this goes red, while every other trace stays green."""
    load = scores["short_load"]
    assert load["reason"] == "load_back", load
    assert load["recovery"] == 0, load
    assert load["after"]["worst"] <= 1, load


def test_a_forward_state_load_seeks(scores):
    load = scores["forward_jump"]
    assert load["recovery"] is not None and load["recovery"] <= 2, load
    assert load["after"]["worst"] <= 1, load


def test_a_nudge_retargets_the_ghost_immediately(scores):
    """His ask: practise a segment as though everything before it went right.
    The nudge shifts which template picture answers for the live clock, so it
    must land in a frame or two, not drift in."""
    nudge = scores["nudge"]
    assert nudge["plus_wanted"] == 30, nudge
    assert nudge["minus_wanted"] == -15, nudge
    assert nudge["after_plus"] is not None and nudge["after_plus"] <= 2, nudge
    assert nudge["after_minus"] is not None and nudge["after_minus"] <= 2, nudge
    assert nudge["after"]["worst"] <= 1, nudge


def test_a_clip_whose_first_picture_is_not_at_zero_still_lines_up(scores):
    """An accurate cut leaves its sub-frame remainder on the first picture
    (clip 5782: frames at k/60 + 0.011 s). Every slot counts from
    `video_start_s`, which is why the arithmetic stays in `ui/frame.js`."""
    cfr = scores["cfr_offset_clip"]
    assert cfr["first_time"] > 0.011, cfr
    assert cfr["worst"] <= 1, cfr
    assert cfr["over_one"] == 0, cfr


def test_his_own_grab_takes_the_ghost_down_and_a_reset_brings_it_back(scores):
    """ROUND 3, ITEM 1. His words: *"The ghost should close when I grab the
    star (sometimes I might be faster than the ghost!). Upon star grab for the
    player's actual live session."*

    The interesting half is the NINETY SAMPLES AFTER THE ACTION CLEARS. A star
    dance holds the grab action for about three seconds and then Mario is an
    ordinary walking Mario again with the counter still climbing, so a follower
    that read the flag per sample would put the ghost back up over a star he
    had already taken -- the exact overstaying this round is about. The latch
    is what makes those ninety samples silent too.
    """
    grab = scores["his_grab"]
    assert grab["before"] == "playing", grab
    assert grab["at_touch"] == "grabbed", grab
    assert grab["held_paused"] is True, grab
    assert grab["reasons_after"] == ["grabbed"], grab
    # ...and his reset brings it straight back, by the same route the ghost
    # finishing first already used: the counter returns to near zero.
    assert grab["played_again"] is True, grab
    assert grab["after_reset"]["worst"] <= 1, grab


def test_a_segment_ghost_is_not_ended_by_a_star_grab(scores):
    """A segment's run does not end at a star: some run through one on the way
    somewhere, and taking the ghost down there deletes the half he was
    practising. A segment's own end is the segment engine's judgement and it is
    not on this socket, so a segment keeps the end it already had."""
    segment = scores["segment_grab"]
    assert segment["kept_playing"] is True, segment
    assert "grabbed" not in segment["reasons"], segment
    assert segment["worst"] <= 1, segment


def test_a_load_past_the_end_does_not_raise_a_ghost_with_nothing_left(scores):
    """The release is "back INSIDE the run", not "the counter jumped". A load
    landing past the template's last picture has nothing to show, so the ghost
    stays down rather than parking a star-grab frame over his game."""
    assert scores["grab_then_load_past_the_end"]["reasons"] == ["grabbed"], (
        scores["grab_then_load_past_the_end"])


def test_a_courses_own_subarea_is_still_the_course(scores):
    """ROUND 3, ITEM 5. His report: *"I exited LLL (by dying) -- and it looks
    like the ghost is still playing... We should only play a ghost when it's
    the active star / we're in that course/area."*

    The gate names the LEVEL and leaves the area open, which is the whole
    reading: the volcano is Lethal Lava Land and the pyramid is Shifting Sand
    Land, so a gate on the area would take the ghost down inside the very star
    it is playing. Dying out of the course is a LEVEL change and does take it
    down, and walking back in brings it back on the next sample.
    """
    sub = scores["subarea"]
    assert sub["in_course"] <= 1, sub
    assert sub["subarea_played"] is True, sub
    assert "elsewhere" not in sub["subarea_reasons"], sub
    assert sub["away_paused"] is True, sub
    assert sub["away_reason"] == "elsewhere", sub
    assert sub["back_action"] == "seek", sub
    assert sub["back_reason"] != "elsewhere", sub
    assert sub["back_recovery"] is not None and sub["back_recovery"] <= 2, sub


def test_the_ghost_holds_while_the_player_is_in_another_level(scores):
    away = scores["elsewhere"]
    assert away["played_at_home"] is True, away
    assert away["paused_away"] is True, away
    assert away["reason"] == "elsewhere", away


def test_a_template_the_rule_cannot_read_says_so(scores):
    """A counter that restarts AFTER the attempt's own start makes one counter
    value name two pictures -- an area warp (Usamune restarts the counter per
    leg) or a state load during the recording. The prototype refuses those by
    name rather than picking one. A drop that does not land near zero is not a
    run starting either, so it is refused too."""
    assert scores["loaded_template"] == {"ok": False,
                                         "reason": "counter_restarts"}
    assert scores["two_restart_template"] == {"ok": False,
                                              "reason": "counter_restarts"}
    assert scores["mapless_template"] == {"ok": False, "reason": "no_picture_timer"}


def test_the_js_near_zero_band_is_the_pythons(scores):
    """The band that means "a run just started" has ONE definition in this
    project. The follower's copy is the same number `anchors.py` uses, so a
    template's zero and a journalled `practice_reset` can never disagree about
    which frame that was."""
    from sm64_events.detectors.anchors import NEAR_ZERO_IGT
    assert scores["near_zero"] == NEAR_ZERO_IGT


def test_a_clip_that_starts_before_its_own_reset_plays_the_attempt(scores):
    """THE DEFECT THIS CLOSES. Every saved clip of a star opens with pre-roll
    from before the reset that started it, and through that reload the counter
    still holds the previous run's final value -- so the counter restarts once,
    at the attempt's own start, in essentially every real clip. Refusing that
    refused all of them: his first real try showed a black screen with no
    footage (2026-09-19).

    The pre-roll is not played, shown or matched: the template's zero is the
    last drop into the near-zero band, and the ghost's clip time never goes
    earlier than that picture."""
    run = scores["preroll"]
    assert run["ok"] is True, run
    assert run["preroll_pictures"] == 44, run
    assert run["first_igt"] == 3, run
    assert run["pictures"] == 400, run
    assert run["ever_preroll"] is False, run
    assert run["earliest"] >= run["first_time"] - 1e-9, run
    # And it follows: steady play from the attempt's own start stays inside a
    # game frame, with one seek to place it.
    assert run["worst"] <= 1, run
    assert run["over_one"] == 0, run
    assert run["seeks"] <= 2, run


def test_a_clip_sitting_in_its_pre_roll_never_reports_the_previous_run(scores):
    """A page that has just loaded is at clip time 0, which in a real clip is
    a picture from the run before this one. What is on screen is answered with
    the earliest picture the template may show, so the first decision's drift
    is a number about THIS attempt."""
    unplaced = scores["unplaced"]
    assert unplaced["shown_at_zero"] == unplaced["first_igt"] == 3, unplaced
    assert unplaced["first_drift"] == 3 - 200, unplaced


def test_the_sources_span_clamps_the_ghost_to_the_attempt(scores):
    """HIS RULING: *"Ghost footage should be clamped to the EXACT START FRAME
    of the attempt, and the exact ending frame of the star grab... It should
    always be clamped to exactly the span of the attempt, regardless of the
    user's recording settings."*

    So when the source hands over the span it decides both ends, and what the
    recording kept around the attempt -- lead-in before the reset, cutscene
    after the star -- is never shown. Past the end the ghost parks on the last
    picture of the attempt rather than playing into the cutscene."""
    span = scores["span"]
    assert span["ok"] is True, span
    assert span["basis"] == "attempt_frames", span
    assert span["pictures"] == 400, span
    assert span["first_igt"] == 3, span
    assert span["last_igt"] == 402, span
    # Neither end of the clip outside the span was ever on screen.
    assert span["ever_preroll"] is False, span
    assert span["ever_outside"] is False, span
    # And inside it, it follows.
    assert span["worst"] <= 1, span
    assert span["over_one"] == 0, span
    # Past the last picture: parked on it, paused, and it stays there.
    assert span["past_reason"] == "past_template", span
    assert span["past_paused"] is True, span
    assert span["past_on_last"] is True, span


def test_without_a_span_the_counter_decides_the_start_and_the_clip_the_end(scores):
    """The fallback, for an older server or a clip whose frames cannot be
    resolved: the last restart into the near-zero band is the start, and the
    clip's own last picture is the end -- so the cutscene tail IS part of the
    template, which is exactly what the span exists to trim."""
    absent = scores["span_absent"]
    assert absent["ok"] is True, absent
    assert absent["basis"] == "counter_restart", absent
    assert absent["preroll_pictures"] == 44, absent
    assert absent["first_igt"] == 3, absent
    assert absent["last_igt"] == 402, absent
    assert absent["pictures"] == 460, absent   # the attempt plus its 60 tail


def test_a_clip_holding_the_next_attempt_shows_only_this_one(scores):
    """A long enough recording catches the beginning of the NEXT attempt in the
    same clip. Asked what is on screen past the run's end, the template answers
    with its own last picture rather than with the other run's counter -- a
    number that would read as the ghost being wildly out of step. Without a
    span the two runs cannot be told apart, and the clip is refused."""
    trimmed = scores["span_trims_next_run"]
    assert trimmed["ok"] is True, trimmed
    assert trimmed["pictures"] == 400, trimmed
    assert trimmed["tail_igt"] == 60, trimmed   # the next run, 60 frames in
    assert trimmed["shown_past_end"] == trimmed["last_igt"] == 402, trimmed
    assert trimmed["without_span"] == "counter_restarts", trimmed


def test_a_span_naming_no_stamped_picture_says_so(scores):
    """It does not quietly widen back to the whole clip: that would put the
    lead-in on screen, which is the defect the span exists to make impossible."""
    assert scores["span_empty"] == {"ok": False, "reason": "span_has_no_pictures"}


def test_a_reset_returns_to_the_attempts_first_picture_not_the_pre_roll(scores):
    """The counter returning to zero is the reset signal, and the picture it
    names is the template's own first one -- never a picture from the run the
    clip happens to open with. The ghost holds there while the live timer is
    still below the template's start, then runs."""
    reset = scores["preroll_reset"]
    assert reset["reason"] == "reset", reset
    assert reset["at_reset"] == 3, reset
    assert reset["held_paused"] is True, reset
    assert reset["held_on_first"] is True, reset
    assert reset["ever_preroll"] is False, reset
    assert reset["recovery"] is not None and reset["recovery"] <= 2, reset
    assert reset["after"]["worst"] <= 1, reset


# -- the real clip ----------------------------------------------------------


def _real_clips() -> list[dict]:
    """EVERY saved sidecar on this machine that carries a picture timer,
    read-only. The payload the server would hand the page is these same keys.

    All of them rather than the first: the first one sorted happened to be the
    clip with no pre-roll while the rule was being written, which is exactly
    how the refusal that rejected essentially every real clip survived its own
    check (his first try, 2026-09-19).
    """
    clips = []
    for root in _clip_roots():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("attempt_*.json")):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if meta.get("picture_igt") and meta.get("frame_times"):
                clips.append({"name": path.name,
                              "view": {k: meta.get(k) for k in
                                       ("frame_times", "picture_igt", "duration_s",
                                        "video_start_s", "fps", "encode")}})
    return clips


REAL = r"""
import { createFollower, ghostTemplate } from "__FOLLOWER__";
const view = __VIEW__;
const DT = 1 / 30;
const template = ghostTemplate(view);
const igts = view.picture_igt.filter((v) => v != null);
// THE TRACE WALKS THE TEMPLATE'S OWN RUN, not the clip's raw stamps.
//
// This used to be `igts[0] .. igts[igts.length - 1]`, which on a real saved
// clip is "the previous run's final timer .. this attempt's last" -- 3029
// down to 584 on his Swimming Beast clip. The loop then never ran, the trace
// was EMPTY, and `Math.max()` of nothing is -Infinity, which JSON writes as
// `null`: the test died comparing None to 1 rather than reporting anything
// about the follower (measured on his own recording, 2026-09-19). The
// template already knows where the attempt starts and ends; ask it.
const first = template.firstIgt, last = template.lastIgt;

// A REFUSED template has no run to walk and no `igtAtTime` to ask, so the
// answer is the refusal itself. Calling on regardless threw
// "igtAtTime is not a function" on the sixth of his clips -- an error about
// the harness, in a check whose whole job is to report on the follower.
if (!template.ok) {
  console.log(JSON.stringify({ok: false, reason: template.reason}));
} else {

function drive(trace) {
  const follower = createFollower(view, {});
  const video = { currentTime: 0, playbackRate: 1, paused: true };
  const errors = [];
  let now = 0;
  for (const sample of trace) {
    const d = follower.sample(sample, video.currentTime, now);
    if (d.action === "seek" && d.time != null) video.currentTime = d.time;
    if (d.rate > 0) { video.playbackRate = d.rate; video.paused = false; }
    else video.paused = true;
    const shown = template.igtAtTime(video.currentTime);
    errors.push(shown == null ? null : shown - sample.igt);
    if (!video.paused) video.currentTime += video.playbackRate * DT;
    now += DT * 1000;
  }
  return errors;
}

// The whole moving span of the real clip, at the game's own rate.
const play = [];
for (let igt = first; igt <= last; igt += 1)
  play.push({ igt, global_timer: 5000 + igt, level: 8, area: 1 });
const steady = drive(play).slice(2).filter((e) => e != null);

// The same clip, interrupted: a stall at a third of the way, then a load
// back to the quarter mark.
const mixed = [];
const third = first + Math.floor((last - first) / 3);
for (let igt = first; igt <= third; igt += 1)
  mixed.push({ igt, global_timer: 5000 + igt, level: 8, area: 1 });
for (let k = 0; k < 40; k += 1)
  mixed.push({ igt: third, global_timer: 6000 + k, level: 8, area: 1 });
const quarter = first + Math.floor((last - first) / 4);
for (let igt = quarter; igt <= last; igt += 1)
  mixed.push({ igt, global_timer: 7000 + igt, level: 8, area: 1 });
const interrupted = drive(mixed).slice(2).filter((e) => e != null);

// `Math.max()` of nothing is -Infinity and JSON writes that as null, which
// reads downstream as "no error" rather than "no samples". An empty trace is
// a broken harness, so it says so in the answer.
const worst = (xs) => (xs.length ? Math.max(...xs.map(Math.abs)) : "no samples");
console.log(JSON.stringify({
  ok: template.ok, reason: template.reason, pictures: template.pictures,
  preroll_pictures: template.prerollPictures, stamped: igts.length,
  first, last,
  steady: { worst: worst(steady), over_one: steady.filter((e) => Math.abs(e) > 1).length,
            samples: steady.length },
  interrupted: { worst: worst(interrupted),
                 over_one: interrupted.filter((e) => Math.abs(e) > 1).length,
                 samples: interrupted.length },
}));
}
"""


# -- the payload his page actually received ---------------------------------

#: The numbers out of the source payload his overlay was handed on its first
#: real try (star 6-0, "WK Over Wall", 2026-09-19) -- `picture_igt` and
#: `frame_times` and nothing else, no footage and no paths. 642 pictures: 44
#: frozen at 3029 (the previous run's final value, held through the reset's
#: reload), three the capture layer refused, then 3 climbing to 584 with the
#: tail repeating while the star cutscene plays. Every one of those three
#: shapes broke the original rule.
HIS_VIEW = Path(__file__).resolve().parent / "fixtures" / "ghost_preroll_view.json"

def _his_span(view: dict) -> dict:
    """The span the source track will compute, DERIVED from his clip rather
    than typed in: the attempt starts on the frame its counter read zero and
    ends on the frame the star was grabbed.

    The counter's first playable picture is frame 308680 reading 3, so the
    attempt's frame zero is 308677 -- three frames the reload ate, with no
    picture between them. The grab is the first picture carrying the final
    value 584. Both ends are then resolved through the frame map exactly as
    the source does: the first picture at or after the start frame, the last
    at or before the end frame.

    The derivation checks itself: 309261 - 308677 = 584, which is the counter
    value at the grab. A span and a counter that disagreed would mean one of
    them is not measuring this attempt.
    """
    frames, igt = view["frame_map"], view["picture_igt"]
    opening = next(k for k, value in enumerate(igt) if value is not None
                   and value <= 30)
    start_frame = frames[opening] - igt[opening]
    run = igt[opening:]
    grab = opening + run.index(max(v for v in run if v is not None))
    end_frame = frames[grab]
    assert end_frame - start_frame == igt[grab] == 584, (start_frame, end_frame)
    return {
        "first_picture": next(k for k, f in enumerate(frames)
                              if f is not None and f >= start_frame),
        "last_picture": max(k for k, f in enumerate(frames)
                            if f is not None and f <= end_frame),
        "start_frame": start_frame, "end_frame": end_frame,
        "basis": "attempt_frames"}


def test_his_own_payload_clamped_to_the_attempts_exact_span():
    """The span path over the real clip. Derived from his own frame map: the
    attempt is pictures 45 through 626 -- 42 pictures of lead-in before it and
    15 of star cutscene after it, both of which the ghost must never show."""
    view = json.loads(HIS_VIEW.read_text(encoding="utf-8"))
    span = _his_span(view)
    assert span["first_picture"] == 45, span
    assert span["last_picture"] == 626, span
    assert span["start_frame"] == 308677 and span["end_frame"] == 309261, span

    result = _drive(HIS_CLIP.replace("__FOLLOWER__", FOLLOWER)
                    .replace("__VIEW__", json.dumps(view))
                    .replace("__SPAN__", json.dumps(span)))
    assert result["ok"] is True, result
    assert result["basis"] == "attempt_frames", result
    # 582 pictures between those indices, none of them refused by the capture.
    assert result["pictures"] == 582, result
    assert result["first_igt"] == 3, result
    assert result["last_igt"] == 584, result
    # It follows inside the span, and never leaves it in either direction.
    assert result["steady"]["worst"] <= 1, result
    assert result["steady"]["over_one"] == 0, result
    assert result["ever_preroll"] is False, result
    assert result["ever_outside"] is False, result
    assert result["earliest"] >= result["first_time"] - 1e-9, result
    # Slower than the ghost: it waits on the grab frame rather than playing
    # the cutscene he has not earned yet.
    assert result["past"]["reason"] == "past_template", result
    assert result["past"]["paused"] is True, result
    assert result["past"]["on_last"] is True, result


HIS_CLIP = r"""
import { createFollower, ghostTemplate } from "__FOLLOWER__";
const view = __VIEW__;
const span = __SPAN__;
const DT = 1 / 30;
const template = ghostTemplate(view, span);

function drive(trace) {
  const follower = createFollower(view, { span });
  const video = { currentTime: 0, playbackRate: 1, paused: true };
  const rows = [];
  let now = 0;
  for (const sample of trace) {
    const d = follower.sample(sample, video.currentTime, now);
    if (d.action === "seek" && d.time != null) video.currentTime = d.time;
    if (d.rate > 0) { video.playbackRate = d.rate; video.paused = false; }
    else video.paused = true;
    const shown = template.igtAtTime(video.currentTime);
    rows.push({ igt: sample.igt, shown, time: video.currentTime,
                outside: template.outsideRun(video.currentTime),
                error: shown == null ? null : shown - sample.igt,
                paused: video.paused, reason: d.reason });
    if (!video.paused) video.currentTime += video.playbackRate * DT;
    now += DT * 1000;
  }
  return rows;
}
const clock = (igt, k) => ({ igt, global_timer: 5000 + k, level: 8, area: 1 });
const worst = (rows) => Math.max(...rows.map((r) => Math.abs(r.error)));
const over = (rows) => rows.filter((r) => Math.abs(r.error) > 1).length;

// 1. The attempt played through, at the game's own rate, from the counter
//    value his clip's first playable picture carries.
const play = [];
for (let igt = template.firstIgt; igt <= template.lastIgt; igt += 1)
  play.push(clock(igt, play.length));
const steady = drive(play);
const moving = steady.slice(2).filter((r) => r.error != null);

// 2. The same clip, reset a third of the way in: the counter sits in the
//    near-zero band through the reload, then climbs again.
const third = template.firstIgt + Math.floor((template.lastIgt - template.firstIgt) / 3);
const mixed = [];
for (let igt = template.firstIgt; igt <= third; igt += 1) mixed.push(clock(igt, mixed.length));
const reloadAt = mixed.length;
for (let k = 0; k < 40; k += 1) mixed.push(clock(k < 20 ? 0 : k - 19, mixed.length));
for (let igt = template.firstIgt; igt <= template.lastIgt; igt += 1)
  mixed.push(clock(igt, mixed.length));
const reset = drive(mixed);
const afterReload = reset.slice(reloadAt + 42).filter((r) => r.error != null);

// 3. He is SLOWER than the ghost was: the live counter keeps climbing past
//    the template's last picture.
const over_run = [];
for (let igt = template.lastIgt - 30; igt <= template.lastIgt + 60; igt += 1)
  over_run.push(clock(igt, over_run.length));
const overrun = drive(over_run);
const past = overrun.slice(35);

const every = [].concat(steady, reset, overrun);
console.log(JSON.stringify({
  ok: template.ok, reason: template.reason, pictures: template.pictures,
  preroll_pictures: template.prerollPictures, basis: template.basis,
  first_igt: template.firstIgt, last_igt: template.lastIgt,
  first_time: template.firstTime, last_time: template.lastTime,
  steady: { worst: worst(moving), over_one: over(moving), samples: moving.length },
  // Nothing outside the attempt was on screen in ANY of the three traces.
  ever_preroll: every.some((r) => r.shown === 3029),
  ever_outside: every.some((r) => r.outside),
  earliest: Math.min(...every.map((r) => r.time)),
  past: { reason: past[10].reason, paused: past.every((r) => r.paused),
          on_last: past.every((r) => r.shown === template.lastIgt) },
  reset: { reason: reset[reloadAt].reason, shown: reset[reloadAt].shown,
           held_paused: reset.slice(reloadAt + 1, reloadAt + 20).every((r) => r.paused),
           held_on_first: reset.slice(reloadAt + 1, reloadAt + 20)
                               .every((r) => r.shown === template.firstIgt),
           after: { worst: worst(afterReload), over_one: over(afterReload),
                    samples: afterReload.length } },
}));
"""


def test_his_own_first_try_payload_plays_its_attempt():
    """THE CLIP THAT SHOWED HIM A BLACK SCREEN, driven through the follower.

    A reduced copy of the real `/api/ghost/source` payload his overlay was
    handed: the pre-roll, the capture layer's three refused pictures, the
    repeated values where a 60 fps capture covers a 30 fps game, and the tail
    repeating through the star cutscene. The old rule refused this template
    outright (`counter_restarts`) and the page had nothing to play."""
    view = json.loads(HIS_VIEW.read_text(encoding="utf-8"))
    result = _drive(HIS_CLIP.replace("__FOLLOWER__", FOLLOWER)
                    .replace("__VIEW__", json.dumps(view))
                    .replace("__SPAN__", "null"))
    assert result["ok"] is True, result
    assert result["reason"] is None, result
    assert result["basis"] == "counter_restart", result
    # 642 pictures, three of which the capture layer refused: 639 stamped, of
    # which 42 belong to the run that was already over when the clip opened.
    assert result["preroll_pictures"] == 42, result
    assert result["pictures"] == 597, result
    assert result["first_igt"] == 3, result
    assert result["last_igt"] == 584, result

    # Steady play from timer 3 upward, inside one game frame the whole way.
    assert result["steady"]["samples"] >= 500, result
    assert result["steady"]["worst"] <= 1, result
    assert result["steady"]["over_one"] == 0, result

    # The pre-roll is never shown, in either trace.
    assert result["ever_preroll"] is False, result
    assert result["ever_outside"] is False, result
    assert result["earliest"] >= result["first_time"] - 1e-9, result

    # A reset mid-run goes back to the attempt's first picture and holds there
    # while the live counter is still below it, then follows again.
    assert result["reset"]["reason"] == "reset", result
    assert result["reset"]["shown"] == 3, result
    assert result["reset"]["held_paused"] is True, result
    assert result["reset"]["held_on_first"] is True, result
    assert result["reset"]["after"]["worst"] <= 1, result
    assert result["reset"]["after"]["over_one"] == 0, result


def test_the_real_saved_clips_follow_their_own_timer():
    """The end-to-end check against footage this machine actually holds:
    every saved clip's own per-picture timer, played back against a clock that
    walks it, stalls in it and loads backwards inside it.

    A clip the rule REFUSES is a pass with a name -- an area-crossing star
    makes one counter value name two pictures and the prototype says so rather
    than guessing. What is not allowed is a clip it accepts and then follows
    badly, or a trace with no samples in it (which is what an empty play loop
    looked like before: `worst` came back null and the comparison, not the
    follower, is what failed).
    """
    clips = _real_clips()
    if not clips:
        pytest.skip("no saved clip with a picture timer on this machine")
    followed, refused = [], []
    for clip in clips:
        result = _drive(REAL.replace("__FOLLOWER__", FOLLOWER)
                        .replace("__VIEW__", json.dumps(clip["view"])))
        note = f"{clip['name']}: {result}"
        if not result["ok"]:
            assert result["reason"] in {"counter_restarts", "no_picture_timer",
                                        "span_has_no_pictures"}, note
            refused.append(clip["name"])
            continue
        # It played the ATTEMPT, not the clip: a template whose run starts at
        # the previous attempt's final timer would walk an empty trace.
        assert result["first"] < result["last"], note
        assert result["steady"]["samples"] > 0, note
        assert result["interrupted"]["samples"] > 0, note
        assert result["steady"]["worst"] <= 1, note
        assert result["steady"]["over_one"] == 0, note
        assert result["interrupted"]["worst"] <= 1, note
        assert result["interrupted"]["over_one"] == 0, note
        followed.append(clip["name"])
    # A run in which every clip was refused would satisfy every assertion
    # above and prove nothing at all.
    assert followed, f"no clip on this machine was followed: refused {refused}"
