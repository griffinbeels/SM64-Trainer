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
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FOLLOWER = (REPO / "src" / "sm64_events" / "ui" / "ghost" / "follower.js").as_uri()

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")


def _clip_roots() -> list[Path]:
    """Where this machine's own footage sits, read-only. A worktree has its
    own (empty) `replays/`, so the primary checkout -- the one the human
    actually plays on -- is the interesting one."""
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
import { createFollower, ghostTemplate } from "__FOLLOWER__";

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
  const template = ghostTemplate(view);
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
    rows.push({ igt: sample.igt, wanted, shown,
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

// -- 9. a template whose counter restarts is refused, not guessed ----------
{
  const view = feedTemplate(100);
  view.picture_igt = view.picture_igt.map((v, k) => (k < 50 ? v : v - 50));
  const t = ghostTemplate(view);
  out.restarting_template = { ok: t.ok, reason: t.reason };
}
{
  const view = feedTemplate(100);
  view.picture_igt = null;
  const t = ghostTemplate(view);
  out.mapless_template = { ok: t.ok, reason: t.reason };
}

console.log(JSON.stringify(out));
"""


def _drive(script: str) -> dict:
    proc = subprocess.run(["node", "--input-type=module", "-e", script],
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


def test_the_ghost_holds_while_the_player_is_in_another_level(scores):
    away = scores["elsewhere"]
    assert away["played_at_home"] is True, away
    assert away["paused_away"] is True, away
    assert away["reason"] == "elsewhere", away


def test_a_template_the_rule_cannot_read_says_so(scores):
    """A counter that restarts inside the template (an area warp, a state
    load during the recording) makes one counter value name two pictures.
    The prototype refuses it by name rather than picking one."""
    assert scores["restarting_template"] == {"ok": False,
                                             "reason": "counter_restarts"}
    assert scores["mapless_template"] == {"ok": False, "reason": "no_picture_timer"}


# -- the real clip ----------------------------------------------------------


def _real_clip() -> dict | None:
    """A saved sidecar on this machine, read-only. The payload the server
    would hand the page is these same keys."""
    for root in _clip_roots():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("attempt_*.json")):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if meta.get("picture_igt") and meta.get("frame_times"):
                return {"name": path.name,
                        "view": {k: meta.get(k) for k in
                                 ("frame_times", "picture_igt", "duration_s",
                                  "video_start_s", "fps", "encode")}}
    return None


REAL = r"""
import { createFollower, ghostTemplate } from "__FOLLOWER__";
const view = __VIEW__;
const DT = 1 / 30;
const template = ghostTemplate(view);
const igts = view.picture_igt.filter((v) => v != null);
const first = igts[0], last = igts[igts.length - 1];

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

const worst = (xs) => Math.max(...xs.map(Math.abs));
console.log(JSON.stringify({
  ok: template.ok, pictures: igts.length, first, last,
  steady: { worst: worst(steady), over_one: steady.filter((e) => Math.abs(e) > 1).length,
            samples: steady.length },
  interrupted: { worst: worst(interrupted),
                 over_one: interrupted.filter((e) => Math.abs(e) > 1).length,
                 samples: interrupted.length },
}));
"""


def test_the_real_saved_clip_follows_its_own_timer():
    """The one end-to-end check against footage this machine actually holds:
    the template's own per-picture timer, played back against a clock that
    walks it, stalls in it and loads backwards inside it."""
    clip = _real_clip()
    if clip is None:
        pytest.skip("no saved clip with a picture timer on this machine")
    script = (REAL.replace("__FOLLOWER__", FOLLOWER)
              .replace("__VIEW__", json.dumps(clip["view"])))
    result = _drive(script)
    assert result["ok"] is True, result
    assert result["steady"]["worst"] <= 1, result
    assert result["steady"]["over_one"] == 0, result
    assert result["interrupted"]["worst"] <= 1, result
    assert result["interrupted"]["over_one"] == 0, result
