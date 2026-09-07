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
import { useOverlayRows, useTemplateRevision } from "../inputpreferences.js";
import { slotAtTime, timeOfSlot } from "../frame.js";
import { watchVideoPicture } from "../videopicture.js";
import htm from "htm";
import { Icon } from "./icons.js";
import { fmtIgtShort } from "../format.js";
import { ControllerPanel, FacingDial, heldNames, stickPhrase, stickWords } from "./controllerpanel.js";
import { SetupModal } from "./setupmodal.js";
import { EMU } from "../platform.js";

const html = htm.bind(h);

const STICK_HEIGHT = 46;
const SPEED_HEIGHT = 30;

// A run is `{start, length, buttons, stick_x, stick_y, yaw, speed}` on the
// capture axis (zero-based, sorted), with holes between runs where capture
// stopped. The field names are the payload's own, so a field added on the
// server is readable here the moment it arrives.
export function frameAt(runs, frame) {
  if (frame == null) return null;
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
  if (frame == null) return null;
  for (const span of spans || []) {
    if (frame >= span.start && frame < span.start + span.length) return span;
  }
  return null;
}

// The last moment at or before `frame`: what he had most recently done to
// the world when the playhead sits here. Markers are sorted by frame.
export function momentAt(markers, frame) {
  if (frame == null) return null;
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

// Each capture hole breaks the drawing. A polyline joining either side of a
// hole invents a stick movement or speed where neither was recorded.
export function contiguousRuns(runs) {
  const groups = [];
  for (const run of runs) {
    const group = groups[groups.length - 1];
    const previous = group && group[group.length - 1];
    if (previous && previous.start + previous.length === run.start) group.push(run);
    else groups.push([run]);
  }
  return groups;
}

function curvePath(runs, pointsOf) {
  return contiguousRuns(runs).map((group) =>
    `M ${pointsOf(group).split(" ").join(" L ")}`).join(" ");
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
function stickReach(stickMax, ...tracks) {
  let reach = stickMax;
  for (const runs of tracks) {
    for (const run of runs) reach = Math.max(reach, Math.abs(run.stick_x), Math.abs(run.stick_y));
  }
  return reach;
}

function stickPath(runs, axis, reach) {
  const valueOf = (run) => (axis === "x" ? run.stick_x : run.stick_y);
  return stepPath(runs, valueOf,
    (value) => STICK_HEIGHT / 2 - (value / reach) * (STICK_HEIGHT / 2 - 2));
}

// Times read as SECONDS, not as a frame count. His round-32 ask, quoting
// k8ehops: "instead of using 30f or 35f, use xx.xx format instead, since thats
// what most people read times as". `fmtIgtShort` is the project's own display
// form, so this surface cannot spell a time differently from the rest.
const timeLabel = fmtIgtShort;

function spanLabel(start, length, lead = 0) {
  start -= lead;
  return length === 1
    ? timeLabel(start)
    : `${timeLabel(start)}–${timeLabel(start + length - 1)}`;
}

// Both action tracks occupy the SAME lane. The template's outlined band is
// taller, so identical timing still leaves an amber edge around your action.
function ActionRow({ name, spans, templateSpans = [], percent, seek, total = 0, lead = 0 }) {
  const draw = (span, ghost) => html`
    <button class=${`action-span group-${span.group} ${ghost ? "is-template" : ""}`}
        key=${`${ghost ? "t" : "a"}${span.start}`}
        style=${total && span.start + span.length >= total
          ? `right:0;width:${percent(span.length)}`
          : `left:${percent(span.start)};width:${percent(span.length)};`
            + `max-width:calc(100% - ${percent(span.start)})`}
        onclick=${(event) => { event.stopPropagation(); seek(span.start); }}
        title=${`${ghost ? "Template — " : ""}${span.label} — ${spanLabel(span.start, span.length, lead)} (${span.length}f)`}
        aria-label=${`${ghost ? "Template " : ""}${span.label} from ${spanLabel(span.start, span.length, lead)}`}>
      <span class="action-span-name">${span.label}</span>
    </button>`;
  return html`<div class=${`input-lane is-actions ${templateSpans.length ? "has-template" : ""}`}>
    <span class="input-lane-name">${name}</span>
    <div class="input-lane-track">
      ${templateSpans.map((span) => draw(span, true))}
      ${spans.map((span) => draw(span, false))}
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
  let found = null;
  for (const [axisStart, rawStart, length] of stretches || []) {
    if (raw >= rawStart && raw < rawStart + length) {
      // A raw counter is not an identity across save-state resets. Until
      // a capture occurrence resolves the choice, neither visit is known.
      if (found !== null) return null;
      found = axisStart + (raw - rawStart);
    }
  }
  return found;
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
// when the map cannot identify an input. The panel must preserve that
// absence rather than borrow a nearby input from a time estimate.
export function mappedFrameAtTime(seconds, frameMap, clock, stretches, frames) {
  if (!frameMap || !frameMap.length) return null;
  const slot = slotAtTime(seconds, clock);
  if (slot < 0 || slot >= frameMap.length) return null;
  const raw = frameMap[slot];
  if (raw == null) return null;
  const axis = trackFrameOf(raw, stretches);
  return axis !== null && axis >= 0 && axis < frames ? axis : null;
}
export function mappedTimeAtFrame(frame, frameMap, clock, stretches) {
  if (frame == null || !frameMap || !frameMap.length) return null;
  const raw = gameFrameOf(frame, stretches);
  if (raw === null || trackFrameOf(raw, stretches) !== frame) return null;
  // Prefer the exact counter within one visit. A preceding epoch can have
  // larger counters, so >= alone would seek there before finding this frame.
  let first = null, matchedEpoch = null, epoch = 0, previous = null;
  for (let slot = 0; slot < frameMap.length; slot += 1) {
    const shown = frameMap[slot];
    if (shown == null) continue;
    if (previous != null && shown < previous) epoch += 1;
    if (shown === raw) {
      if (matchedEpoch !== null && matchedEpoch !== epoch) return null;
      if (first === null) first = slot;
      matchedEpoch = epoch;
    }
    previous = shown;
  }
  if (first !== null) return timeOfSlot(first, clock);
  // A skipped frame can choose the next capture only on an ascending map.
  // Without an occurrence match, a reset makes that neighbor ambiguous.
  if (epoch) return null;
  for (let slot = 0; slot < frameMap.length; slot += 1) {
    const shown = frameMap[slot];
    if (shown != null && shown >= raw) return timeOfSlot(slot, clock);
  }
  return null;
}

// THE CLIP'S OWN CHECK (`pad_stamp_agreement`), and it is SILENT WHEN IT
// PASSES. The capture layer copies the pad out of RDRAM beside every picture
// it stamps, so extraction can compare that against what the timeline holds
// for the same frame -- exactly, with no pixels involved. It is right on
// every picture of every clip, and a chip saying so is a fact about our
// plumbing rather than about his run: "displaying this to the user is really
// weird lol, worthless information for them" (2026-09-05). Same shape as the
// segment step indicator he retired: the display existed to prove the
// mechanism worked, and it has.
//
// A DISAGREEMENT still draws, because that is the opposite kind of news --
// the panel beside this video is showing a pad the game did not hold, and
// nothing else on the page would say so. Clicking it lists every contradicted
// picture and seeks there.
function screenCheck(agreement, open, toggle) {
  if (!agreement || !agreement.pictures) return null;
  const off = agreement.pictures - agreement.agree;
  if (off === 0) return null;
  const title = "The capture layer copied the pad the game held beside every "
    + `picture it stamped, and the timeline disagrees with it on ${off} of `
    + `${agreement.pictures}. Click to list them.`;
  // A datum on a summary surface is a DOOR to its evidence (his standing
  // rule): the chip opens the list.
  return html`<button type="button"
      class=${`input-screen-check ${open ? "is-open" : ""}`}
      title=${title} aria-expanded=${open}
      onclick=${toggle}>${off} ${off === 1 ? "picture" : "pictures"}${" "}
      disagree with the game</button>`;
}

// Whether this clip's frame map came off the frame-exact capture layer (the
// wrapper plugin, core/capturelayer.py) rather than a reconstruction after
// the fact. A stored association that cannot be verified is a replay-specific
// problem; enabling capture now cannot repair its missing source evidence.
function frameMapNote(frameMapSource, inputAlignment, openSetup) {
  if (inputAlignment?.status === "unverified") {
    return html`<div class="input-frame-map-note">
      <span>Input alignment could not be verified for this replay.</span>
    </div>`;
  }
  if (frameMapSource === "plugin") return null;
  return html`<div class="input-frame-map-note">
    <span>Frame-exact capture is off.</span>
    <button type="button" onclick=${openSetup}>Set up</button>
  </div>`;
}

// The pictures whose stamped pad the timeline does not hold, each as the
// PANEL frame it sits on -- "which 2 disagree?" answered on the surface, and
// a click goes there. `disagreements` rows are
// [slot, frame, track pad, stamped pad], each pad [stick_x, stick_y, buttons].
function padOf(pad, buttons) {
  if (!Array.isArray(pad)) return "--";
  const { vertical, horizontal } = stickWords(pad[0], pad[1]);
  const stick = [vertical, horizontal].filter(Boolean).join(" ") || "neutral";
  const held = heldNames(pad[2], buttons).join(" + ") || "no buttons";
  return `${stick} · ${held}`;
}

function DisagreementList({ agreement, stretches, seek, seekSlot, slotCount, lead, buttons }) {
  const rows = (agreement && agreement.disagreements) || [];
  if (!rows.length) return null;
  return html`<ul class="input-screen-check-list">
    ${rows.map(([slot, raw, tracked, stamped]) => {
      const axis = raw == null ? null : trackFrameOf(raw, stretches);
      const hasSlot = Number.isInteger(slot) && slot >= 0 && slot < slotCount;
      return html`<li key=${slot}>
        <button type="button" class="input-screen-check-row"
            disabled=${seekSlot ? !hasSlot : axis == null}
            onclick=${() => seekSlot ? seekSlot(slot) : axis != null && seek(axis)}>
          <span class="frame">${axis == null ? "outside the track" : `frame ${axis - lead}`}</span>
          <span>game <strong>${padOf(stamped, buttons)}</strong>${" "}·${" "}timeline${" "}<strong>${padOf(tracked, buttons)}</strong></span>
        </button>
      </li>`;
    })}
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
  if (frame == null || frame < lead) return null;
  return { frames: frame - lead, stamped: false };
}

export function InputTimeline({ attemptId, video, anchorOffsetS = 0,
                                frameMap = null, clock = null, inputSpan = undefined,
                                pictureIgt = null,
                                padAgreement = null,
                                frameMapSource = null,
                                inputAlignment = null,
                                compact = false,
                                tools = null }) {
  const [state, setState] = useState({ phase: "loading" });
  const [frame, setFrame] = useState(video ? null : 0);
  const [presentedSlot, setPresentedSlot] = useState(null);
  const [overlayVisible, toggleOverlay] = useOverlayRows();
  const templateRevision = useTemplateRevision();
  const [retry, setRetry] = useState(0);
  const [checkOpen, setCheckOpen] = useState(false);   // the screen-check list
  const [setupOpen, setSetupOpen] = useState(false);
  // The pointer and the playhead both work in the TRACK column's own box,
  // never the lane row's: the row starts with the label column, and a
  // playhead measured against it could be dragged over the words "Stick"
  // and "Mario" (his report, 2026-08-22).
  const trackColumn = useRef(null);

  // The server excludes heartbeat copies from these bounds: an old held
  // picture remains in the video without stretching the input axis through
  // minutes of unrecorded pre-attempt time. Keep the raw-map fallback for
  // older servers that do not publish the captured span.
  const clipSpan = useMemo(() => {
    if (inputSpan !== undefined) return inputSpan ? inputSpan.join(",") : null;
    if (!frameMap || !frameMap.length) return null;
    let low = null;
    let high = null;
    for (const raw of frameMap) {
      if (raw == null) continue;
      if (low === null || raw < low) low = raw;
      if (high === null || raw > high) high = raw;
    }
    return low === null ? null : `${low},${high}`;
  }, [frameMap, inputSpan]);

  useEffect(() => {
    let alive = true;
    setState((old) => old.phase === "ready" && old.data.attempt_id === attemptId
      ? old : { phase: "loading" });
    const range = clipSpan
      ? `?from_frame=${clipSpan.split(",")[0]}&to_frame=${clipSpan.split(",")[1]}`
      : "";
    fetch(`/api/attempts/${attemptId}/inputs${range}`)
      .then((response) => (response.ok
        ? response.json()
        : response.text().then((text) => Promise.reject(new Error(text)))))
      .then((data) => { if (alive) setState({ phase: "ready", data }); })
      .catch((error) => { if (alive) setState((old) => old.phase === "ready"
        ? { ...old, refreshError: String(error) }
        : { phase: "error", error: String(error) }); });
    return () => { alive = false; };
  }, [attemptId, clipSpan, templateRevision, retry]);

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
    const { frames, stretches } = state.data;
    const readAt = (seconds) => {
      const at = seconds == null ? null : mappedFrameAtTime(seconds,
        frameMap, clock, stretches, frames);
      setFrame((current) => (current === at ? current : at));
      setPresentedSlot(seconds == null ? null : slotAtTime(seconds, clock));
    };
    // ReplayPlayer starts observing before autoplay. A late mount or template
    // refresh therefore reuses the last delivered mediaTime even while paused.
    // No delivered picture means unknown, never the requested seek's time.
    return watchVideoPicture(video, readAt);
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
      Could not read this attempt's inputs: ${state.error}
      <button onclick=${() => setRetry((value) => value + 1)}>Retry</button></div>`;
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
  const boundedClock = { ...clock, duration: Number.isFinite(video?.duration)
    ? video.duration : clock?.duration };
  const slotCount = clock?.times?.length || frameMap?.length || 0;
  const seekSlot = video ? (slot) => {
    if (!Number.isInteger(slot) || slot < 0 || slot >= slotCount) return;
    if (!video.paused) video.pause();
    video.currentTime = timeOfSlot(slot, boundedClock);
  } : null;
  const seek = (next) => {
    const clamped = Math.max(0, Math.min(total - 1, next));
    if (video) {
      // Seeking the video is how the timeline moves: the clock loop above
      // reads the new time back on the next frame, so the two cannot
      // disagree even for a frame.
      if (!video.paused) video.pause();
      const mapped = mappedTimeAtFrame(clamped, frameMap, boundedClock, data.stretches);
      if (mapped !== null) {
        video.currentTime = mapped;
        return;
      }
      // An absent association cannot locate this input in the footage.
      // Playback still works; guessing an anchor offset would reintroduce
      // the desync that the presented-picture reader refuses to display.
    } else {
      setFrame(clamped);
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
  const reach = stickReach(data.stick_max, data.runs, template ? template.runs : []);
  const percent = (value) => `${(value / total) * 100}%`;

  // ONE lane per button, with the template's bars drawn BEHIND yours inside
  // it — which is what "drawn behind your own" means, and what two stacked
  // rows of identically-named lanes did not mean.
  const byBit = new Map(lanes.map((lane) => [lane.bit, lane]));
  const ghostByBit = new Map(templateLanes.map((lane) => [lane.bit, lane]));
  const bits = [...new Set([...byBit.keys(), ...ghostByBit.keys()])];
  const laneRow = (bit) => {
    const mine = byBit.get(bit);
    const ghost = overlayVisible(`button:${bit}`) ? ghostByBit.get(bit) : null;
    const name = (mine || ghostByBit.get(bit)).name;
    return html`<div class="input-lane" key=${bit}>
      <span class="input-lane-name">${name}</span>
      <div class="input-lane-track">
        ${(ghost ? ghost.bars : []).map((bar) => html`
          <span class="input-bar is-template" key=${`t${bar.start}`}
                style=${`left:${percent(bar.start)};width:${percent(bar.length)}`}
                title=${`Template — ${name} ${spanLabel(bar.start, bar.length, lead)} (${bar.length}f)`} />`)}
        ${(mine ? mine.bars : []).map((bar) => html`
          <button class="input-bar" key=${bar.start}
                  style=${`left:${percent(bar.start)};width:${percent(bar.length)}`}
                  onclick=${(event) => { event.stopPropagation(); seek(bar.start); }}
                  title=${`${name} ${spanLabel(bar.start, bar.length, lead)} (${bar.length}f)`}
                  aria-label=${`${name} held from ${spanLabel(bar.start, bar.length, lead)}, ${bar.length} frames`} />`)}
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
        ${screenCheck(padAgreement, checkOpen,
                      () => setCheckOpen((open) => !open))}
        ${frameMapNote(frameMapSource, inputAlignment, () => setSetupOpen(true))}
      </div>
    </header>
    ${state.refreshError && html`<p class="is-error" role="alert">
      Could not refresh the template. Showing the previous comparison.
      <button onclick=${() => setRetry((value) => value + 1)}>Retry</button>
    </p>`}
    ${checkOpen && html`<${DisagreementList} agreement=${padAgreement}
        stretches=${data.stretches} seek=${seek} seekSlot=${seekSlot}
        slotCount=${slotCount} lead=${lead} buttons=${data.buttons} />`}

    ${data.template && html`<div class="input-template-note">
      <${Icon} name="bookmark" size=${13} />
      <span>Compared against${" "}<strong>${data.template.name}</strong>${
        data.template.error
          ? html` — <span class="is-error">that template no longer loads:${" "}
              ${data.template.error}</span>`
          : html` — ${data.template.author || "Uncredited"}. Both start at frame 0.
              ${data.template.frames > total - lead
                ? "The template continues beyond this attempt’s visible timeline."
                : data.template.frames < attemptFrames ? "The template ends before your attempt." : ""}`}</span>
    </div>`}

    ${template && html`<div class="input-overlay-controls">
      <div class="input-overlay-legend"><span class="is-attempt">Your attempt — solid</span>
        <span class="is-template">Template — dashed / outlined</span></div>
      <details><summary>Template overlay rows</summary>
        <div class="input-overlay-switches">
          ${[["stick", "Stick"], ["actions", "Mario actions"], ["speed", "Speed"],
            ...data.buttons.map(([bit, name]) => [`button:${bit}`, name])]
            .map(([key, label]) => html`<label key=${key}><input type="checkbox"
                checked=${overlayVisible(key)} onchange=${(event) => toggleOverlay(key, event.target.checked)} />
              ${label}</label>`)}
        </div>
        <p class="meta">Applies to all open and future timelines in this browser.</p>
      </details>
    </div>`}

    <div class="input-lanes"
         onpointerdown=${seekFromPointer}
         onpointermove=${(event) => { if (event.buttons & 1) seekFromPointer(event); }}
         role="group" aria-label="Input lanes">
      <div class="input-track-column" ref=${trackColumn}>
        ${lead > 0 && html`<div class="input-lead-shade"
            style=${`width:${percent(lead)}`}></div>`}
        ${frame != null && html`<div class="input-playhead" style=${`left:${percent(frame)}`}></div>`}
      </div>
      <div class="input-lane is-stick">
        <span class="input-lane-name">Stick</span>
        <div class="input-lane-track">
          <svg viewBox=${`0 0 ${total} ${STICK_HEIGHT}`} height=${STICK_HEIGHT}
               preserveAspectRatio="none" aria-hidden="true">
            <line x1="0" y1=${STICK_HEIGHT / 2} x2=${total} y2=${STICK_HEIGHT / 2}
                  class="stick-axis" vector-effect="non-scaling-stroke" />
            ${template && overlayVisible("stick") && html`
              <path class="stick-line is-x is-template" vector-effect="non-scaling-stroke"
                    d=${curvePath(template.runs, (runs) => stickPath(runs, "x", reach))} />
              <path class="stick-line is-y is-template" vector-effect="non-scaling-stroke"
                    d=${curvePath(template.runs, (runs) => stickPath(runs, "y", reach))} />`}
            <path class="stick-line is-x" vector-effect="non-scaling-stroke"
                  d=${curvePath(data.runs, (runs) => stickPath(runs, "x", reach))} />
            <path class="stick-line is-y" vector-effect="non-scaling-stroke"
                  d=${curvePath(data.runs, (runs) => stickPath(runs, "y", reach))} />
          </svg>
        </div>
      </div>
      ${((data.actions || []).length > 0 || (template && template.actions?.length > 0)) && html`
        <${ActionRow} name="Mario" spans=${data.actions || []} percent=${percent}
            templateSpans=${template && overlayVisible("actions") ? template.actions || [] : []}
            seek=${seek} total=${total} lead=${lead} />`}
      ${markers.length > 0 && html`
        <${MomentRow} markers=${markers} total=${total} percent=${percent}
            seek=${seek} lead=${lead} />`}
      <div class="input-lane is-speed">
        <span class="input-lane-name">Speed</span>
        <div class="input-lane-track">
          <svg viewBox=${`0 0 ${total} ${SPEED_HEIGHT}`} height=${SPEED_HEIGHT}
               preserveAspectRatio="none" aria-hidden="true">
            ${template && overlayVisible("speed") && html`
              <path class="speed-line is-template" vector-effect="non-scaling-stroke"
                    d=${curvePath(template.runs, (runs) => speedPath(runs, peak))} />`}
            <path class="speed-line" vector-effect="non-scaling-stroke"
                  d=${curvePath(data.runs, (runs) => speedPath(runs, peak))} />
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
        <strong>${frame == null ? "—" : frame - lead} / ${Math.max(0, total - lead - 1)}</strong>
        ${(() => {
          // Keep the presented slot. Reversing through the raw counter can
          // select a previous visit and show that visit's IGT after a reset.
          const shown = inspectorClock(video ? null : frame, lead, pictureIgt, presentedSlot);
          if (!shown) return html`<span class="meta">${frame != null && frame < lead ? "lead-in" : "Time unavailable"}</span>`;
          return html`<span class="meta ${shown.stamped ? "is-stamped" : ""}"
              title=${shown.stamped ? "the game's own timer in this picture"
                                    : "counted from the attempt's first frame"}>
            ${timeLabel(shown.frames)}</span>`;
        })()}
      </div>
      <${ControllerPanel} frame=${here} buttons=${data.buttons}
          stickMax=${data.stick_max} deadZone=${data.dead_zone}
          label=${template ? "Stick" : "Pressing"}
          templateFrame=${template ? there : undefined} />
      <${FacingDial} yaw=${here ? here.yaw : null}
          angleUnits=${data.angle_units} speed=${here ? here.speed : 0}
          templateYaw=${template ? (there ? there.yaw : null) : undefined}
          templateSpeed=${there ? there.speed : null}
          label="Mario faces" />
      <div class="input-inspector-read">
        ${here
          ? html`<span>Stick ${stickPhrase(here.stick_x, here.stick_y,
              data.dead_zone, data.stick_max)}</span>`
          : html`<span class="is-error">${frame == null ? "Input unavailable for this picture" : "No capture on this frame"}</span>`}
        ${nowDoing && html`<span class="input-inspector-action">
          ${nowDoing.label}</span>`}
        ${thereDoing && html`<span class="input-inspector-action is-template"
            title="What the template was doing on this frame">
          template: ${thereDoing.label}</span>`}
        ${lastMoment && html`<span class="input-inspector-moment"
            title="The last moment before this frame">
          ${lastMoment.label}${" "}<span class="meta">at ${timeLabel(lastMoment.frame - lead)}</span></span>`}
      </div>
    </footer>
    ${typeof tools === "function" ? tools(data) : tools}
    ${setupOpen && html`<${SetupModal} onClose=${() => setSetupOpen(false)}
        initialPane=${EMU} />`}
  </div>`;
}
