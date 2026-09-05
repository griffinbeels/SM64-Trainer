// src/sm64_events/ui/components/inputtimeline.js
// One attempt's inputs, drawn as lanes over frames -- and, when a template is
// active, that template drawn BEHIND them so the gap is something you look at.
//
// It reports what each track was doing on a frame and names nothing as the
// reason. His ruling, 2026-08-20: "I think the user can deduce the corrections
// they need to make based on the data. If we try to prescribe solutions,
// that's a totally different problem." So there are no generated corrections,
// and -- one level deeper -- no attempt to MATCH your presses to the
// template's either: deciding "this A corresponds to that A" is an inference,
// and a wrong match is a confidently wrong number wearing a measurement's
// clothes.
//
// Lanes are HTML boxes at percentage widths rather than SVG, so they stretch
// with the drawer at any width with no aspect arithmetic. The stick is the one
// SVG, and it sets preserveAspectRatio explicitly -- the default is `slice`,
// which CROPS whatever the container's aspect does not cover.
import { h } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { clampToFrames, slotAtTime, timeOfSlot } from "../frame.js";
import htm from "htm";
import { Icon } from "./icons.js";
import { fmtIgtShort } from "../format.js";
import { ControllerPanel, FacingDial, stickPhrase } from "./controllerpanel.js";
import { SetupModal } from "./setupmodal.js";

const html = htm.bind(h);

const STICK_HEIGHT = 46;
const SPEED_HEIGHT = 30;

// A run is `{start, length, buttons, stick_x, stick_y, yaw, speed}` on the
// capture axis (zero-based, sorted), with holes between runs where capture
// stopped. The field names are the payload's own, so a field added on the
// server is readable here the moment it arrives.
export function frameAt(runs, frame) {
  // A frame inside a hole has no reading and answers null -- never the
  // neighbouring run's, which would interpolate across exactly the gap the
  // format exists to preserve.
  let low = 0;
  let high = runs.length - 1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    const run = runs[mid];
    if (frame < run.start) high = mid - 1;
    else if (frame >= run.start + run.length) low = mid + 1;
    else return run;
  }
  return null;
}

export function actionAt(spans, frame) {
  for (const span of spans || []) {
    if (frame >= span.start && frame < span.start + span.length) return span;
  }
  return null;
}

// The last moment at or before `frame`: what he had most recently done to
// the world when the playhead sits here. Markers are sorted by frame.
export function momentAt(markers, frame) {
  let found = null;
  for (const marker of markers || []) {
    if (marker.frame > frame) break;
    found = marker;
  }
  return found;
}

// The buttons SM64 play is made of. These lanes ALWAYS draw, pressed or
// not, so the rows sit in the same place on every attempt and an unused C
// button is visibly empty rather than absent (his report, 2026-08-22: "our
// list of inputs is missing C-up for some reason. C-Left, C-Right, C-Down,
// C-Up should be captured clearly"). Start, the D-pad, L and R appear only
// when pressed -- fourteen lanes would bury the seven that carry a run.
export const CORE_BUTTONS = ["A", "B", "Z", "Cup", "Cdown", "Cleft", "Cright"];

export function lanesOf(runs, table) {
  return (table || []).map(([bit, name]) => {
    const bars = [];
    for (const run of runs) {
      if (!(run.buttons & bit)) continue;
      const last = bars[bars.length - 1];
      if (last && last.start + last.length === run.start) last.length += run.length;
      else bars.push({ start: run.start, length: run.length });
    }
    return { bit, name, bars };
  }).filter((lane) => lane.bars.length > 0 || CORE_BUTTONS.includes(lane.name));
}

// A step line: one value held across each run, drawn as a horizontal segment
// from the run's start to its end. `valueOf` picks the field; `scale` maps it
// into the lane's height.
function stepPath(runs, valueOf, scale) {
  const points = [];
  for (const run of runs) {
    const y = scale(valueOf(run)).toFixed(2);
    points.push(`${run.start},${y}`, `${run.start + run.length},${y}`);
  }
  return points.join(" ");
}

// Speed is drawn against the fastest value in THIS track, not a fixed cap:
// what he asked for is "where there are opportunities to go faster", which is
// a comparison within one run. A fixed ceiling would flatten a whole slow
// segment into a line at the bottom and hide exactly that.
//
// With a template behind it, BOTH curves share the faster track's peak: two
// curves on two scales would put the slower run's top at the same height as
// the faster run's, which is the opposite of the comparison he asked for.
function speedPeak(...tracks) {
  let peak = 0;
  for (const runs of tracks) {
    for (const run of runs || []) peak = Math.max(peak, Math.abs(run.speed));
  }
  return peak > 0 ? peak : 1;
}

function speedPath(runs, peak) {
  return stepPath(runs, (run) => Math.abs(run.speed),
    (speed) => SPEED_HEIGHT - (speed / peak) * (SPEED_HEIGHT - 2));
}

// The stick's reach is the pad's own, not the game's cap: his pad reaches 84
// where the game clamps at 64, so scaling to the cap would pin every full
// deflection to the lane's edge (the same call controllerpanel.js makes).
function stickPath(runs, axis, stickMax) {
  const valueOf = (run) => (axis === "x" ? run.stick_x : run.stick_y);
  let reach = stickMax;
  for (const run of runs) reach = Math.max(reach, Math.abs(valueOf(run)));
  return stepPath(runs, valueOf,
    (value) => STICK_HEIGHT / 2 - (value / reach) * (STICK_HEIGHT / 2 - 2));
}

// Times read as SECONDS, not as a frame count. His round-32 ask, quoting
// k8ehops: "instead of using 30f or 35f, use xx.xx format instead, since thats
// what most people read times as". `fmtIgtShort` is the project's own display
// form, so this surface cannot spell a time differently from the rest.
const timeLabel = fmtIgtShort;

function spanLabel(start, length) {
  return length === 1
    ? timeLabel(start)
    : `${timeLabel(start)}–${timeLabel(start + length - 1)}`;
}

// One row of Mario's actions. The template's is the same row, dimmed and
// beneath yours rather than behind it: two labelled spans stacked in one
// lane would read as one unreadable label, where two lanes read as "he was
// diving here and you were still running".
// `total` is the track's own length: a span that REACHES the end is placed
// from the RIGHT edge, so it grows inward. `.action-span` carries padding
// that no width can compress below (~6.4px), so a short final span rendered
// that wide whatever its share -- and placing it by `left` hung it 3px past
// the lane, since an over-constrained box drops its `right` rather than its
// `left` (66 layout defects the moment the lead-in made the timeline denser,
// measured 2026-08-31).
function ActionRow({ name, spans, percent, seek, ghost = false, total = 0 }) {
  return html`<div class=${`input-lane is-actions ${ghost ? "is-template" : ""}`}>
    <span class="input-lane-name">${name}</span>
    <div class="input-lane-track">
      ${spans.map((span) => html`
        <button class=${`action-span group-${span.group} ${ghost ? "is-template" : ""}`}
                key=${span.start}
                style=${total && span.start + span.length >= total
                  ? `right:0;width:${percent(span.length)}`
                  : `left:${percent(span.start)};width:${percent(span.length)};`
                    + `max-width:calc(100% - ${percent(span.start)})`}
                onclick=${(event) => { event.stopPropagation(); seek(span.start); }}
                title=${`${ghost ? "Template — " : ""}${span.label} — ${spanLabel(span.start, span.length)} (${span.length}f)`}
                aria-label=${`${ghost ? "Template " : ""}${span.label} from ${spanLabel(span.start, span.length)}`}>
          <span class="action-span-name">${span.label}</span>
        </button>`)}
    </div>
  </div>`;
}

// The journal's MOMENTS on the same axis -- a pole grabbed, a bob-omb picked
// up, a switch pressed, the star itself -- each a tick at the frame it
// happened, with the recorder's own sentence beside it. Nothing here is
// captured; it is the journal joined onto the track by frame (server side,
// inputs/markers.py), so this row and the segment recorder can never name
// one thing two ways. A label gets the room up to the next tick and no more,
// so two moments a few frames apart read as two ticks rather than one
// smeared word; the tooltip carries the whole sentence.
// `lead` is the lead-in's length: a marker's own frame is on the AXIS, and
// the time it states must be on the ATTEMPT's clock, so a moment inside the
// lead reads negative rather than pretending the attempt started earlier.
function MomentRow({ markers, total, percent, seek, lead = 0 }) {
  return html`<div class="input-lane is-moments">
    <span class="input-lane-name">Moments</span>
    <div class="input-lane-track">
      ${markers.map((marker, index) => {
        const next = index + 1 < markers.length ? markers[index + 1].frame : total;
        const room = Math.max(next - marker.frame, 1);
        return html`
          <button class=${`moment-mark type-${marker.type}`} key=${`${marker.frame}-${index}`}
                  data-frame=${marker.frame}
                  style=${`left:${percent(marker.frame)};width:${percent(room)}`}
                  onclick=${(event) => { event.stopPropagation(); seek(marker.frame); }}
                  title=${`${marker.label} — ${timeLabel(marker.frame - lead)}`}
                  aria-label=${`${marker.label} at ${timeLabel(marker.frame - lead)}`}>
            <span class="moment-mark-tick"></span>
            <span class="moment-mark-name">${marker.label}</span>
          </button>`;
      })}
    </div>
  </div>`;
}

// THE clock mapping, in both directions. `anchorOffsetS` is how far into
// the clip the attempt's anchor sits: the clip is cut a few seconds BEFORE
// the anchor (the replay pre-pad) while the track starts AT it. Without it
// every input landed three seconds early (his first live run, 2026-08-22:
// "the input reader shows a totally different angle and shows me pressing
// A/B"). Pure and exported so tests/test_ui_input_clock.py can drive them.
//
// This arithmetic is the FALLBACK. A clip whose sidecar carries a
// `frame_map` uses the mapped pair below instead: the capture duplicates
// and skips single game frames (round 32 items 16/24 -- his counter read
// 26, 27, 27, 29, ...), so no offset can be right on every frame, and his
// ruling was "we need 100% accuracy". The map says, per video frame, which
// game frame its picture shows; the arithmetic remains for clips cut
// before the frame clock existed.
export const frameAtTime = (seconds, anchorOffsetS, fps, frames) => {
  const raw = Math.floor((seconds - anchorOffsetS) * fps + 1e-4);
  return Math.max(0, Math.min(Math.max(frames - 1, 0), raw));
};
export const timeAtFrame = (frame, anchorOffsetS, fps) =>
  anchorOffsetS + (frame + 0.5) / fps;

// A raw game frame <-> the track's zero-based axis, through the payload's
// `stretches` ([axis_start, raw_start, length] per ascending stretch of the
// counter -- the server's own restart rule, shipped rather than re-derived).
export function trackFrameOf(raw, stretches) {
  for (const [axisStart, rawStart, length] of stretches || []) {
    if (raw >= rawStart && raw < rawStart + length) {
      return axisStart + (raw - rawStart);
    }
  }
  return null;
}
export function gameFrameOf(axis, stretches) {
  for (const [axisStart, rawStart, length] of stretches || []) {
    if (axis >= axisStart && axis < axisStart + length) {
      return rawStart + (axis - axisStart);
    }
  }
  return null;
}

// The mapped clock. `frameMap[k]` is the raw game frame video frame k shows
// (null before the clock's coverage); `clock` is the clip clock
// (frame.js::clipClock -- the encode rate and first timestamp of a CFR
// clip, or every frame's own time for a picture-feed clip). Answers null
// when the map cannot say, and the caller falls back to the offset
// arithmetic -- never a silent guess.
export function mappedFrameAtTime(seconds, frameMap, clock, stretches, frames) {
  if (!frameMap || !frameMap.length) return null;
  const slot = Math.max(0, Math.min(frameMap.length - 1,
    slotAtTime(seconds, clock)));
  const raw = frameMap[slot];
  if (raw == null) return null;
  const axis = trackFrameOf(raw, stretches);
  if (axis === null) {
    // The clip's lead-in (before the anchor) or its tail (after the grab).
    const first = (stretches || [])[0];
    return first && raw < first[1] ? 0 : Math.max(frames - 1, 0);
  }
  return Math.max(0, Math.min(Math.max(frames - 1, 0), axis));
}
export function mappedTimeAtFrame(frame, frameMap, clock, stretches) {
  if (!frameMap || !frameMap.length) return null;
  const raw = gameFrameOf(frame, stretches);
  if (raw === null) return null;
  // The FIRST video frame showing this game frame -- or, when the capture
  // skipped it entirely, the first one past it (his 26, 27, 27, 29 shape:
  // frame 28's picture never existed, so its inputs show over 29's slot).
  for (let slot = 0; slot < frameMap.length; slot += 1) {
    const shown = frameMap[slot];
    if (shown != null && shown >= raw) return timeOfSlot(slot, clock);
  }
  return null;
}

// The pad reader's verdict on the clip (replay/padread.py): how many video
// frames the game's OWN input display could be read on, and on how many of
// those the timeline's pad is exactly what the screen shows. His acceptance
// test ("100% or it can't be relied on") as a number he can see, on the
// surface he judges it from; the tool that lists each disagreement is named
// in the hover.
function screenCheck(reading, attemptId, open, toggle, degraded = false) {
  if (!reading || !reading.sure) return null;
  const off = reading.sure - reading.agree;
  // Three honest numbers in the timeline's own unit (his 2026-09-01
  // ruling that "checked 1207/1207" was "literally and objectively
  // wrong"): how many of the clip's FRAMES the display could be checked
  // on, how many of those disagree, and the longest run the display
  // cannot tell apart (inside a hold every neighbour reads the same, so
  // nothing on screen pins which one a picture is). Never "N/N".
  const total = reading.frames_total || 0;
  const checked = reading.frames_checked || 0;
  const gap = reading.unpinned_longest || 0;
  const gapText = gap ? ` · longest unpinned ${gap}f` : "";
  // A DEGRADED capture is never allowed to look like a clean one (item 89):
  // the recorder's own bookkeeping did not cover this clip, so the display
  // had to repair the map frame by frame -- which is exactly where a drifting
  // frame comes from. He should be able to see that without asking.
  const health = degraded
    ? `${" "}· capture was degraded, map repaired from the screen` : "";
  const label = total
    ? (off === 0
        ? `screen-checked ${checked} of ${total} frames${gapText}${health}`
        : `screen-checked ${checked} of ${total} frames · ${off} disagree${gapText}${health}`)
    : (off === 0
        ? `screen-checked ${reading.agree}/${reading.sure}`
        : `screen-checked ${reading.agree}/${reading.sure} · ${off} disagree`);
  const title = `Of the clip's ${total || "?"} game frames the game's own input display `
    + `could be checked on ${checked}; the timeline's pad matches on all but ${off}. `
    + (gap ? `The longest stretch nothing on screen can pin is ${gap} frames. ` : "")
    + (off ? "Click to list each disagreeing frame."
           : "Every checkable frame agrees.");
  // A datum on a summary surface is a DOOR to its evidence (his standing
  // rule): the chip opens the list when there is one to open.
  return html`<button type="button"
      class=${`input-screen-check ${off ? "is-off" : "is-clean"} `
        + `${degraded ? "is-degraded " : ""}${open ? "is-open" : ""}`}
      title=${title} disabled=${off === 0} aria-expanded=${open}
      onclick=${toggle}>${label}</button>`;
}

// Whether this clip's frame map came off the frame-exact capture layer (the
// wrapper plugin, core/capturelayer.py) rather than a reconstruction after
// the fact -- a nudge, not a verdict: every other source is a real, working
// map, just one built from less direct evidence than the plugin's own.
function frameMapNote(frameMapSource, openSetup) {
  if (frameMapSource === "plugin") return null;
  return html`<div class="input-frame-map-note">
    <span>Frame-exact capture is off.</span>
    <button type="button" onclick=${openSetup}>Set up</button>
  </div>`;
}

// The frames the screen contradicts, each as the PANEL frame it sits on --
// "which 2 frames disagree?" answered on the surface, and a click goes
// there. `disagreements` rows are [slot, row, screen reads, map says].
function DisagreementList({ reading, frameMap, stretches, seek, lead }) {
  const rows = (reading && reading.disagreements) || [];
  if (!rows.length) return null;
  const seen = new Set();
  const items = [];
  for (const [slot, row, screen, says] of rows) {
    const raw = frameMap ? frameMap[slot] : null;
    const axis = raw == null ? null : trackFrameOf(raw, stretches);
    const key = `${axis}:${row}`;
    if (seen.has(key)) continue;                  // both slots of one picture
    seen.add(key);
    items.push({ axis, row, screen, says, slot });
  }
  return html`<ul class="input-screen-check-list">
    ${items.map((item) => html`<li key=${item.slot}>
      <button type="button" class="input-screen-check-row"
          disabled=${item.axis == null}
          onclick=${() => item.axis != null && seek(item.axis + lead)}>
        <span class="frame">${item.axis == null ? "outside the track" : `frame ${item.axis}`}</span>
        <span class="axis">${item.row === "y" ? "up/down" : "left/right"}</span>
        <span>screen <strong>${item.screen}</strong> · timeline <strong>${item.says}</strong></span>
      </button>
    </li>`)}
  </ul>`;
}

// THE INSPECTOR'S CLOCK. A capture-layer clip stamps every picture with the
// IGT the game held when it drew that picture -- the number Usamune printed
// on screen -- so the inspector shows that for the slot on screen, and only
// counts from the track's first frame (`frame - lead`) when no stamp is
// there. The two clocks start a frame or two apart (the track is cut to the
// attempt's final time), which is what he saw as "the time is always one
// frame later" on his first plugin clip (2026-09-05). Returns {frames,
// stamped}, or null in the lead-in with nothing stamped.
export function inspectorClock(frame, lead, pictureIgt, slot) {
  if (pictureIgt && slot != null && slot >= 0 && slot < pictureIgt.length) {
    const igt = pictureIgt[slot];
    if (igt != null) return { frames: igt, stamped: true };
  }
  if (frame < lead) return null;
  return { frames: frame - lead, stamped: false };
}

export function InputTimeline({ attemptId, video, anchorOffsetS = 0,
                                frameMap = null, clock = null,
                                pictureIgt = null,
                                padReading = null, degraded = false,
                                frameMapSource = null,
                                compact = false,
                                tools = null }) {
  const [state, setState] = useState({ phase: "loading" });
  const [frame, setFrame] = useState(0);
  const [checkOpen, setCheckOpen] = useState(false);   // the screen-check list
  const [setupOpen, setSetupOpen] = useState(false);
  // The pointer and the playhead both work in the TRACK column's own box,
  // never the lane row's: the row starts with the label column, and a
  // playhead measured against it could be dragged over the words "Stick"
  // and "Mario" (his report, 2026-08-22).
  const trackColumn = useRef(null);

  // THE CLIP'S OWN RANGE (round 32 item 53): the map says which game frame
  // each video frame shows, so its own extremes ARE what the video shows --
  // ask for exactly that and the timeline matches the footage, buffers
  // included, with no part of it pointing at video that does not exist.
  // Null until the clip's view lands (or forever, with no clip), and the
  // track is then the attempt alone.
  const clipSpan = useMemo(() => {
    if (!frameMap || !frameMap.length) return null;
    let low = null;
    let high = null;
    for (const raw of frameMap) {
      if (raw == null) continue;
      if (low === null || raw < low) low = raw;
      if (high === null || raw > high) high = raw;
    }
    return low === null ? null : `${low},${high}`;
  }, [frameMap]);

  useEffect(() => {
    let alive = true;
    setState({ phase: "loading" });
    const range = clipSpan
      ? `?from_frame=${clipSpan.split(",")[0]}&to_frame=${clipSpan.split(",")[1]}`
      : "";
    fetch(`/api/attempts/${attemptId}/inputs${range}`)
      .then((response) => (response.ok
        ? response.json()
        : response.text().then((text) => Promise.reject(new Error(text)))))
      .then((data) => { if (alive) setState({ phase: "ready", data }); })
      .catch((error) => { if (alive) setState({ phase: "error", error: String(error) }); });
    return () => { alive = false; };
  }, [attemptId, clipSpan]);

  // ONE CLOCK, ALWAYS. The video is the clock whenever there is one: the
  // timeline reads it every frame and never keeps a position of its own, so
  // dragging the video's scrubber moves the playhead and dragging the
  // playhead seeks the video. There is no "stop following" state -- that
  // was how the two drifted apart (his report, 2026-08-22: "if I drag the
  // video playhead itself, it should automatically move the input playback
  // system's playhead as well. We need both of these to always stay in
  // sync").
  useEffect(() => {
    if (!video || state.phase !== "ready") return undefined;
    const { fps, frames, stretches } = state.data;
    const lead = state.data.lead_frames || 0;
    const readAt = (seconds) => {
      const mapped = mappedFrameAtTime(seconds, frameMap, clock,
        stretches, frames);
      // The fallback arithmetic counts from the ANCHOR (the attempt's own
      // frame 0), which sits `lead` slots into the axis when a buffer is
      // drawn; the mapped path lands on the axis directly.
      const at = mapped !== null
        ? mapped
        : Math.min(frames - 1,
                   frameAtTime(seconds, anchorOffsetS, fps, frames) + lead);
      setFrame((current) => (current === at ? current : at));
    };
    // THE FRAME THE BROWSER IS ACTUALLY SHOWING, not the time we asked for.
    // `requestVideoFrameCallback` hands back that frame's own `mediaTime`,
    // so the panel reads the picture on screen rather than a slot computed
    // from `currentTime` -- and `currentTime` is the time of the SEEK, which
    // does not have to be inside the interval of the frame the decoder then
    // presents. That off-by-one is what he stepped into on this clip's
    // frames 87-89: the map named slot 363 (which really does draw L4, read
    // off the pixels) while the element was still showing 362's L3, so the
    // panel and the video disagreed by exactly one frame in places.
    if (typeof video.requestVideoFrameCallback === "function") {
      let handle = 0;
      let live = true;
      const onFrame = (_now, meta) => {
        if (!live) return;
        readAt(meta.mediaTime);
        handle = video.requestVideoFrameCallback(onFrame);
      };
      handle = video.requestVideoFrameCallback(onFrame);
      // A seek that lands on the frame already displayed presents nothing,
      // so read once up front rather than waiting for a callback that has
      // no reason to come.
      readAt(video.currentTime || 0);
      return () => {
        live = false;
        if (video.cancelVideoFrameCallback) {
          video.cancelVideoFrameCallback(handle);
        }
      };
    }
    let raf = 0;
    const tick = () => {
      readAt(video.currentTime || 0);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [video, state, anchorOffsetS, frameMap, clock]);

  const data = state.phase === "ready" ? state.data : null;
  const lanes = useMemo(
    () => (data ? lanesOf(data.runs, data.buttons) : []), [data]);
  const templateLanes = useMemo(
    () => (data && data.template ? lanesOf(data.template.runs, data.buttons) : []),
    [data]);

  if (state.phase === "loading") {
    return html`<div class="input-timeline is-loading">Reading inputs…</div>`;
  }
  if (state.phase === "error") {
    return html`<div class="input-timeline is-error">
      Could not read this attempt's inputs: ${state.error}</div>`;
  }
  if (!data.runs.length) {
    return html`<div class="input-timeline is-empty">
      <${Icon} name="feed" size=${18} />
      <div>
        <strong>No inputs recorded for this attempt.</strong>
        <p>It was played before input capture existed, or the trainer was not
           attached to the emulator at the time.</p>
      </div>
    </div>`;
  }

  const total = data.frames || 1;
  // The lead-in: frames before the attempt's own first (the level entry to
  // the reset). FRAME 0 stays the attempt's start -- the lead draws as
  // negative numbers and a shaded band, so the attempt's own length (the
  // number his PB is graded on) reads unchanged.
  const lead = data.lead_frames || 0;
  const attemptFrames = data.attempt_frames || (total - lead);
  const seek = (next) => {
    const clamped = Math.max(0, Math.min(total - 1, next));
    setFrame(clamped);
    if (video) {
      // Seeking the video is how the timeline moves: the clock loop above
      // reads the new time back on the next frame, so the two cannot
      // disagree even for a frame.
      if (!video.paused) video.pause();
      const mapped = mappedTimeAtFrame(clamped, frameMap, clock,
        data.stretches);
      // Inside the clip, always: a seek to the very edge leaves the element
      // reporting itself ended, and the panel then reads whatever it
      // presents (his 2026-08-31 jump from frame 770 to 591).
      video.currentTime = clampToFrames(
        mapped !== null ? mapped
                        : timeAtFrame(clamped - lead, anchorOffsetS, data.fps),
        video.duration || 0, data.fps);
    }
  };
  const seekFromPointer = (event) => {
    const box = trackColumn.current;
    if (!box) return;
    const rect = box.getBoundingClientRect();
    if (rect.width <= 0) return;
    seek(Math.round(((event.clientX - rect.left) / rect.width) * total));
  };

  const here = frameAt(data.runs, frame);
  const nowDoing = actionAt(data.actions, frame);
  const markers = data.markers || [];
  const lastMoment = momentAt(markers, frame);
  const template = data.template && !data.template.error ? data.template : null;
  const there = template ? frameAt(template.runs, frame) : null;
  const thereDoing = template ? actionAt(template.actions, frame) : null;
  const peak = speedPeak(data.runs, template ? template.runs : []);
  const percent = (value) => `${(value / total) * 100}%`;

  // ONE lane per button, with the template's bars drawn BEHIND yours inside
  // it — which is what "drawn behind your own" means, and what two stacked
  // rows of identically-named lanes did not mean.
  const byBit = new Map(lanes.map((lane) => [lane.bit, lane]));
  const ghostByBit = new Map(templateLanes.map((lane) => [lane.bit, lane]));
  const bits = [...new Set([...byBit.keys(), ...ghostByBit.keys()])];
  const laneRow = (bit) => {
    const mine = byBit.get(bit);
    const ghost = ghostByBit.get(bit);
    const name = (mine || ghost).name;
    return html`<div class="input-lane" key=${bit}>
      <span class="input-lane-name">${name}</span>
      <div class="input-lane-track">
        ${(ghost ? ghost.bars : []).map((bar) => html`
          <span class="input-bar is-template" key=${`t${bar.start}`}
                style=${`left:${percent(bar.start)};width:${percent(bar.length)}`}
                title=${`Template — ${name} ${spanLabel(bar.start, bar.length)} (${bar.length}f)`} />`)}
        ${(mine ? mine.bars : []).map((bar) => html`
          <button class="input-bar" key=${bar.start}
                  style=${`left:${percent(bar.start)};width:${percent(bar.length)}`}
                  onclick=${(event) => { event.stopPropagation(); seek(bar.start); }}
                  title=${`${name} ${spanLabel(bar.start, bar.length)} (${bar.length}f)`}
                  aria-label=${`${name} held from ${spanLabel(bar.start, bar.length)}, ${bar.length} frames`} />`)}
      </div>
    </div>`;
  };

  return html`<div class=${`input-timeline ${compact ? "is-compact" : ""}`}>
    <header class="input-timeline-head">
      <div>
        <span class="eyebrow">Inputs</span>
        ${/* The attempt's OWN time -- the number on the row above -- never
              the track's length, which carries the clip's buffers (his
              2026-09-01 report: 19"16 before the clip, 21"30 after, against
              a 0'19"20 row). data-total/data-lead keep the drawn span
              readable by the sweeps. */""}
        <h4 data-total=${total} data-lead=${lead}>${timeLabel(attemptFrames)}${" "}·${" "}${attemptFrames} frames${" "}·${" "}${data.fps} fps</h4>
        ${screenCheck(padReading, attemptId, checkOpen,
                      () => setCheckOpen((open) => !open), degraded)}
        ${frameMapNote(frameMapSource, () => setSetupOpen(true))}
      </div>
    </header>
    ${checkOpen && html`<${DisagreementList} reading=${padReading}
        frameMap=${frameMap} stretches=${data.stretches} seek=${seek}
        lead=${lead} />`}

    ${data.template && html`<div class="input-template-note">
      <${Icon} name="bookmark" size=${13} />
      <span>Compared against${" "}<strong>${data.template.name}</strong>${
        data.template.error
          ? html` — <span class="is-error">that template no longer loads:${" "}
              ${data.template.error}</span>`
          : ", drawn behind your own. Both start at frame 0."}</span>
    </div>`}

    <div class="input-lanes"
         onpointerdown=${seekFromPointer}
         onpointermove=${(event) => { if (event.buttons & 1) seekFromPointer(event); }}
         role="group" aria-label="Input lanes">
      <div class="input-track-column" ref=${trackColumn}>
        ${lead > 0 && html`<div class="input-lead-shade"
            style=${`width:${percent(lead)}`}></div>`}
        <div class="input-playhead" style=${`left:${percent(frame)}`}></div>
      </div>
      <div class="input-lane is-stick">
        <span class="input-lane-name">Stick</span>
        <div class="input-lane-track">
          <svg viewBox=${`0 0 ${total} ${STICK_HEIGHT}`} height=${STICK_HEIGHT}
               preserveAspectRatio="none" aria-hidden="true">
            <line x1="0" y1=${STICK_HEIGHT / 2} x2=${total} y2=${STICK_HEIGHT / 2}
                  class="stick-axis" vector-effect="non-scaling-stroke" />
            ${data.template && html`
              <polyline class="stick-line is-x is-template" vector-effect="non-scaling-stroke"
                        points=${stickPath(data.template.runs, "x", data.stick_max)} />
              <polyline class="stick-line is-y is-template" vector-effect="non-scaling-stroke"
                        points=${stickPath(data.template.runs, "y", data.stick_max)} />`}
            <polyline class="stick-line is-x" vector-effect="non-scaling-stroke"
                      points=${stickPath(data.runs, "x", data.stick_max)} />
            <polyline class="stick-line is-y" vector-effect="non-scaling-stroke"
                      points=${stickPath(data.runs, "y", data.stick_max)} />
          </svg>
        </div>
      </div>
      ${(data.actions || []).length > 0 && html`
        <${ActionRow} name="Mario" spans=${data.actions} percent=${percent}
            seek=${seek} total=${total} />`}
      ${template && (template.actions || []).length > 0 && html`
        <${ActionRow} name="Template" spans=${template.actions} percent=${percent}
            seek=${seek} ghost=${true} total=${total} />`}
      ${markers.length > 0 && html`
        <${MomentRow} markers=${markers} total=${total} percent=${percent}
            seek=${seek} lead=${lead} />`}
      <div class="input-lane is-speed">
        <span class="input-lane-name">Speed</span>
        <div class="input-lane-track">
          <svg viewBox=${`0 0 ${total} ${SPEED_HEIGHT}`} height=${SPEED_HEIGHT}
               preserveAspectRatio="none" aria-hidden="true">
            ${template && html`
              <polyline class="speed-line is-template" vector-effect="non-scaling-stroke"
                        points=${speedPath(template.runs, peak)} />`}
            <polyline class="speed-line" vector-effect="non-scaling-stroke"
                      points=${speedPath(data.runs, peak)} />
          </svg>
        </div>
      </div>
      ${bits.map((bit) => laneRow(bit))}
    </div>

    <footer class="input-inspector">
      <div class="input-inspector-frame">
        <span class="eyebrow">Frame</span>
        ${/* BOTH HALVES ARE FRAME NUMBERS -- never a number over a count.
              The axis is zero-based (frame 0 is the attempt's own start, the
              lead-in counts backwards from it), so a 499-frame track's last
              frame IS 498, and printing the COUNT beside it meant the
              readout could never reach its own denominator. His report,
              2026-09-05: "we always stop before the last frame of the
              video... from a user perspective this looks like an error, not
              intentional." The last frame now names itself: 498 / 498. */""}
        <strong>${frame - lead} / ${Math.max(0, total - lead - 1)}</strong>
        ${(() => {
          // The slot the axis frame is shown on -- through the map, not the
          // video element, so the stamped clock reads the same with or
          // without a player (the fixture has none).
          const seconds = mappedTimeAtFrame(frame, frameMap, clock, data.stretches);
          const slot = seconds == null ? null : slotAtTime(seconds, clock);
          const shown = inspectorClock(frame, lead, pictureIgt, slot);
          if (!shown) return html`<span class="meta">lead-in</span>`;
          return html`<span class="meta ${shown.stamped ? "is-stamped" : ""}"
              title=${shown.stamped ? "the game's own timer in this picture"
                                    : "counted from the attempt's first frame"}>
            ${timeLabel(shown.frames)}</span>`;
        })()}
      </div>
      <${ControllerPanel} frame=${here} buttons=${data.buttons}
          stickMax=${data.stick_max} deadZone=${data.dead_zone}
          label=${data.template ? "You pressed" : "Pressing"} />
      <${FacingDial} yaw=${here ? here.yaw : null}
          angleUnits=${data.angle_units} speed=${here ? here.speed : 0}
          label="Mario faces" />
      ${template && html`
        <${ControllerPanel} frame=${there} buttons=${data.buttons}
            stickMax=${data.stick_max} deadZone=${data.dead_zone}
            label=${template.name} />
        <${FacingDial} yaw=${there ? there.yaw : null}
            angleUnits=${data.angle_units} speed=${there ? there.speed : 0}
            label="Template faces" />`}
      <div class="input-inspector-read">
        ${here
          ? html`<span>Stick ${stickPhrase(here.stick_x, here.stick_y,
              data.dead_zone, data.stick_max)}</span>`
          : html`<span class="is-error">No capture on this frame</span>`}
        ${nowDoing && html`<span class="input-inspector-action">
          ${nowDoing.label}</span>`}
        ${thereDoing && html`<span class="input-inspector-action is-template"
            title="What the template was doing on this frame">
          template: ${thereDoing.label}</span>`}
        ${lastMoment && html`<span class="input-inspector-moment"
            title="The last moment before this frame">
          ${lastMoment.label}${" "}<span class="meta">at ${timeLabel(lastMoment.frame)}</span></span>`}
      </div>
    </footer>
    ${tools}
    ${setupOpen && html`<${SetupModal} onClose=${() => setSetupOpen(false)}
        initialPane="emu" />`}
  </div>`;
}
